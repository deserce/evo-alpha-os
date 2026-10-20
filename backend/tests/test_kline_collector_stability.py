from datetime import date
import sqlite3

import pandas as pd
import pytest

from scripts.standalone.collect_stock_kline_mootdx_qfq import (
    FetchResult,
    SourceOutageCircuitBreaker,
    fetch_one_symbol,
    save_batch,
    validate_batch_results,
)


@pytest.mark.parametrize("results, expected", [
    ([FetchResult("600000", "fail", "SH"),
      FetchResult("000001", "fail", "SZ"),
      FetchResult("920001", "success", "BSE")], ["SH", "SZ"]),
    ([FetchResult("600000", "skip", "SH", reason="up-to-date"),
      FetchResult("600001", "fail", "SH", reason="no data")], []),
    ([FetchResult("600000", "skip", "SH", reason="no new rows after seed filtering")], []),
    ([FetchResult("600000", "success", "SH"),
      FetchResult("600001", "fail", "SH")], []),
    ([], []),
])
def test_market_wide_failures_cannot_be_hidden_by_another_market(results, expected):
    from scripts.standalone.collect_stock_kline_mootdx_qfq import failed_markets

    assert failed_markets(results) == expected


class ExplodingPool:
    def acquire(self):
        raise AssertionError("adapter should not be acquired")


class FakeAdapter:
    def __init__(self):
        self.max_bars_seen = None
        self.adjust_seen = None

    def get_xdxr_info(self, symbol):
        return pd.DataFrame()

    def get_stock_kline(self, **kwargs):
        self.max_bars_seen = kwargs.get("max_bars")
        self.adjust_seen = kwargs.get("adjust")
        return pd.DataFrame(
            [
                {
                    "trade_date": "2026-05-11",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1000.0,
                    "amount": 10200.0,
                },
                {
                    "trade_date": "2026-05-12",
                    "open": 10.2,
                    "high": 10.8,
                    "low": 10.1,
                    "close": 10.6,
                    "volume": 1100.0,
                    "amount": 11660.0,
                },
            ]
        )


class FakePool:
    def __init__(self):
        self.adapter = FakeAdapter()
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        return self.adapter

    def release(self, adapter):
        assert adapter is self.adapter


class DisconnectedAdapter:
    def __init__(self):
        self.last_failure_kind = None
        self.kline_called = False

    def get_xdxr_info(self, symbol):
        self.last_failure_kind = "connectivity"
        return None

    def get_stock_kline(self, **kwargs):
        self.kline_called = True
        raise AssertionError("K-line request should stop after connectivity failure")


class DisconnectedPool:
    def __init__(self):
        self.adapter = DisconnectedAdapter()

    def acquire(self):
        return self.adapter

    def release(self, adapter):
        assert adapter is self.adapter


def test_up_to_date_symbol_does_not_acquire_adapter():
    result = fetch_one_symbol(
        "000001",
        date(2026, 5, 15),
        date(2023, 1, 1),
        date(2026, 5, 15),
        lookback_days=7,
        full_refresh=False,
        pool=ExplodingPool(),
        max_bars=160,
    )

    assert result.status == "skip"
    assert result.reason == "up-to-date"


def test_bse_symbol_uses_standard_tdx_without_exhq_or_http_fallback():
    pool = FakePool()

    result = fetch_one_symbol(
        "920002",
        date(2026, 5, 15),
        date(2023, 1, 1),
        None,
        lookback_days=7,
        full_refresh=False,
        pool=pool,
        max_bars=160,
        bse_pool=ExplodingPool(),
    )

    assert result.status == "success"
    assert result.source == "tdx_standard_bj"
    assert pool.acquired == 1
    assert pool.adapter.adjust_seen == "none"
    assert result.data is not None
    assert result.data.iloc[0]["source"] == "tdx_standard_bj"
    assert result.data.iloc[0]["market"] == "BSE"
    assert result.data.iloc[0]["adjustment_mode"] == "none"
    assert result.data.iloc[0]["quality_flag"] == "ok_unadjusted"


def test_bse_symbol_fails_tdx_only_when_standard_pool_unavailable():
    result = fetch_one_symbol(
        "920002",
        date(2026, 5, 15),
        date(2023, 1, 1),
        None,
        lookback_days=7,
        full_refresh=False,
        pool=None,
        max_bars=160,
        bse_pool=None,
    )

    assert result.status == "fail"
    assert result.source == "tdx_standard_bj"
    assert "standard TDX" in result.reason


def test_incremental_short_window_passes_max_bars_to_adapter():
    pool = FakePool()

    result = fetch_one_symbol(
        "000001",
        date(2026, 5, 15),
        date(2023, 1, 1),
        date(2026, 5, 8),
        lookback_days=7,
        full_refresh=False,
        pool=pool,
        max_bars=123,
    )

    assert result.status == "success"
    assert pool.acquired == 1
    assert pool.adapter.max_bars_seen == 123


def test_incremental_fetch_uses_seed_row_but_only_returns_new_rows():
    pool = FakePool()

    result = fetch_one_symbol(
        "000001",
        date(2026, 5, 12),
        date(2023, 1, 1),
        date(2026, 5, 11),
        lookback_days=7,
        full_refresh=False,
        pool=pool,
        max_bars=40,
    )

    assert result.status == "success"
    assert result.data is not None
    assert result.data["trade_date"].tolist() == [date(2026, 5, 12)]
    assert round(float(result.data.iloc[0]["pct_chg"]), 4) == round((10.6 / 10.2 - 1) * 100, 4)


def test_force_window_repair_does_not_overwrite_valid_seed_row(tmp_path):
    db_path = tmp_path / "kline.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, open REAL, close REAL, high REAL, low REAL,
                volume REAL, amount REAL, pct_chg REAL, turnover_rate REAL, source TEXT,
                quality_flag TEXT, run_id TEXT, updated_at TEXT, adjustment_mode TEXT, market TEXT,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO stock_daily_prices VALUES (
                '000001', '2026-05-11', 10, 10.2, 10.5, 9.8,
                1000, 10200, 1.23, 1.0, 'old', 'ok', 'old', NULL, 'qfq', 'SZ'
            )
            """
        )
        result = FetchResult(
            symbol="000001",
            status="success",
            market="SZ",
            write_policy="repair",
            data=pd.DataFrame(
                [
                    {
                        "symbol": "000001", "trade_date": date(2026, 5, 11),
                        "open": 10, "close": 10.2, "high": 10.5, "low": 9.8,
                        "volume": 1000, "amount": 10200, "pct_chg": None,
                        "turnover_rate": 1.0, "source": "new", "quality_flag": "ok",
                        "run_id": "new", "updated_at": None, "adjustment_mode": "qfq", "market": "SZ",
                    },
                    {
                        "symbol": "000001", "trade_date": date(2026, 5, 12),
                        "open": 10.2, "close": 10.6, "high": 10.8, "low": 10.1,
                        "volume": 1100, "amount": 11660, "pct_chg": 3.9216,
                        "turnover_rate": 1.1, "source": "new", "quality_flag": "ok",
                        "run_id": "new", "updated_at": None, "adjustment_mode": "qfq", "market": "SZ",
                    },
                ]
            ),
            start_date=date(2026, 5, 11),
            end_date=date(2026, 5, 12),
        )

        saved = save_batch(conn, "stock_daily_prices", [result])
        rows = conn.execute(
            "SELECT trade_date, pct_chg, source FROM stock_daily_prices ORDER BY trade_date"
        ).fetchall()

    assert saved == 1
    assert rows == [("2026-05-11", 1.23, "old"), ("2026-05-12", 3.9216, "new")]


def test_replace_window_seeds_first_pct_chg_from_stored_previous_close(tmp_path):
    db_path = tmp_path / "kline.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, open REAL, close REAL, high REAL, low REAL,
                volume REAL, amount REAL, pct_chg REAL, turnover_rate REAL, source TEXT,
                quality_flag TEXT, run_id TEXT, updated_at TEXT, adjustment_mode TEXT, market TEXT,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO stock_daily_prices VALUES (
                '000001', '2026-07-29', 10, 10, 10.2, 9.8,
                1000, 10000, 0.0, 1.0, 'old', 'ok', 'old', NULL, 'qfq', 'SZ'
            )
            """
        )
        result = FetchResult(
            symbol="000001",
            status="success",
            market="SZ",
            write_policy="replace_window",
            data=pd.DataFrame(
                [
                    {
                        "symbol": "000001", "trade_date": date(2026, 7, 30),
                        "open": 10.4, "close": 10.5, "high": 10.6, "low": 10.3,
                        "volume": 1100, "amount": 11550, "pct_chg": None,
                        "turnover_rate": 1.1, "source": "new", "quality_flag": "ok",
                        "run_id": "new", "updated_at": None, "adjustment_mode": "qfq", "market": "SZ",
                    }
                ]
            ),
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
        )

        save_batch(conn, "stock_daily_prices", [result])
        pct_chg = conn.execute(
            "SELECT pct_chg FROM stock_daily_prices WHERE trade_date='2026-07-30'"
        ).fetchone()[0]

    assert round(float(pct_chg), 4) == 5.0


def test_force_window_refetches_even_when_symbol_is_latest():
    pool = FakePool()

    result = fetch_one_symbol(
        "000001",
        date(2026, 5, 15),
        date(2023, 1, 1),
        date(2026, 5, 15),
        lookback_days=90,
        full_refresh=False,
        pool=pool,
        max_bars=120,
        force_window=True,
    )

    assert result.status == "success"
    assert pool.acquired == 1
    assert pool.adapter.max_bars_seen == 120


def test_connectivity_failure_stops_before_duplicate_kline_reconnect():
    pool = DisconnectedPool()

    result = fetch_one_symbol(
        "000001",
        date(2026, 6, 17),
        date(2023, 1, 1),
        date(2026, 6, 16),
        lookback_days=7,
        full_refresh=False,
        pool=pool,
        max_bars=40,
    )

    assert result.status == "fail"
    assert result.reason == "source unavailable"
    assert pool.adapter.kline_called is False


def test_source_outage_circuit_breaker_trips_and_success_resets_streak():
    breaker = SourceOutageCircuitBreaker(threshold=3)
    unavailable = FetchResult(
        symbol="000001",
        status="fail",
        market="SZ",
        reason="source unavailable",
    )
    success = FetchResult(symbol="000002", status="success", market="SZ")

    assert breaker.observe(unavailable) is False
    assert breaker.observe(unavailable) is False
    assert breaker.observe(success) is False
    assert breaker.observe(unavailable) is False
    assert breaker.observe(unavailable) is False
    assert breaker.observe(unavailable) is True
    assert breaker.tripped is True


def test_source_outage_circuit_breaker_ignores_symbol_level_no_data():
    breaker = SourceOutageCircuitBreaker(threshold=2)
    no_data = FetchResult(
        symbol="000001",
        status="fail",
        market="SZ",
        reason="no raw data",
    )

    assert breaker.observe(no_data) is False
    assert breaker.observe(no_data) is False
    assert breaker.consecutive_failures == 0


def test_batch_validation_rejects_cross_symbol_cloned_ohlcv():
    def cloned_result(symbol):
        return FetchResult(
            symbol=symbol,
            status="success",
            market="SH",
            data=pd.DataFrame(
                [
                    {
                        "symbol": symbol,
                        "trade_date": date(2026, 8, 7),
                        "open": 1308.66,
                        "high": 1315.28,
                        "low": 1301.0,
                        "close": 1309.22,
                        "volume": 2497581.0,
                        "amount": 3266919424.0,
                    }
                ]
            ),
        )

    try:
        validate_batch_results([cloned_result("601696"), cloned_result("603359")])
    except ValueError as exc:
        assert "cross-symbol cloned OHLCV" in str(exc)
    else:
        raise AssertionError("cloned cross-symbol payload must be rejected")
