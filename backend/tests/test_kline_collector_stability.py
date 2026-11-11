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


@pytest.fixture
def resume_db():
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    conn = sqlite3.connect(":memory:")
    collector.init_target_table(conn, "prices")
    collector.init_raw_target_table(conn, "raw_prices")
    for symbol in ("000001", "600000", "920001", "000003", "000004"):
        bse = symbol.startswith("92")
        conn.execute(
            "INSERT INTO prices(symbol,trade_date,open,high,low,close,volume,amount,"
            "turnover_rate,source,adjustment_mode,quality_flag,market) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, "2026-10-30", 10, 11, 9, 10, 100, 1000,
             None if bse else 1, "tdx_standard_bj" if bse else collector.SOURCE_NAME,
             "none" if bse else "qfq", "ok", "BSE" if bse else "SH" if symbol.startswith("6") else "SZ"),
        )
    for symbol in ("000001", "920001", "000002", "000003", "000004"):
        conn.execute(
            "INSERT INTO raw_prices(symbol,trade_date,close,source,adjustment_mode,quality_flag) "
            "VALUES (?, '2026-10-30 00:00:00.000000', ?, ?, 'none', 'ok_unadjusted')",
            (symbol, 0 if symbol == "000004" else 10, collector.RAW_SOURCE_NAME),
        )
    conn.execute("UPDATE prices SET high=8 WHERE symbol='000003'")
    conn.commit()
    yield conn
    conn.close()


def test_resume_selects_missing_or_invalid_dual_rows_without_history_scan(resume_db):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    queries = []
    resume_db.set_trace_callback(queries.append)
    symbols = ["000001", "600000", "920001", "000002", "000003", "000004"]
    pending, complete = collector.select_resume_symbols(
        resume_db, "prices", "raw_prices", symbols, date(2026, 10, 30)
    )
    assert pending == ["600000", "000002", "000003", "000004"]
    assert complete == ["000001", "920001"]
    assert not any("MAX(" in sql.upper() or "GROUP BY" in sql.upper() for sql in queries)
    assert resume_db.total_changes == 11


def test_resume_does_not_treat_another_date_as_target_completion(resume_db):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    assert collector.select_resume_symbols(
        resume_db, "prices", "raw_prices", ["000001"], date(2026, 10, 29)
    ) == (["000001"], [])


def test_resume_validates_table_names_before_sql(resume_db):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    with pytest.raises(ValueError, match="invalid table"):
        collector.select_resume_symbols(
            resume_db, "prices; DROP TABLE prices", "raw_prices", ["000001"], date(2026, 10, 30)
        )


@pytest.mark.parametrize("column,value", [
    ("close", None), ("source", "unknown"), ("adjustment_mode", "none"),
    ("quality_flag", "FAIL"), ("turnover_rate", None),
])
def test_resume_never_skips_invalid_market_data(resume_db, column, value):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    resume_db.execute(f"UPDATE prices SET {column}=? WHERE symbol='000001'", (value,))
    assert collector.select_resume_symbols(
        resume_db, "prices", "raw_prices", ["000001"], date(2026, 10, 30)
    ) == (["000001"], [])


def test_resume_rejects_duplicate_same_day_rows(resume_db):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    resume_db.execute("INSERT INTO prices SELECT symbol, '2026-10-30 00:00:00', "
                      "open,close,high,low,volume,amount,pct_chg,turnover_rate,source,"
                      "quality_flag,run_id,updated_at,adjustment_mode,market "
                      "FROM prices WHERE symbol='000001'")
    assert collector.select_resume_symbols(
        resume_db, "prices", "raw_prices", ["000001"], date(2026, 10, 30)
    ) == (["000001"], [])


@pytest.mark.parametrize("symbol", ["000001", "920001"])
def test_resume_does_not_skip_inconsistent_raw_and_latest_close(resume_db, symbol):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    resume_db.execute("UPDATE raw_prices SET close=1309.22 WHERE symbol=?", (symbol,))
    assert collector.select_resume_symbols(
        resume_db, "prices", "raw_prices", [symbol], date(2026, 10, 30)
    ) == ([symbol], [])


@pytest.mark.parametrize("column,value", [("source", "unknown"), ("turnover_rate", 0),
                                         ("quality_flag", "FAIL")])
def test_resume_replaces_classified_bad_rows_in_the_database(resume_db, column, value):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    daily = pd.read_sql_query("SELECT * FROM prices WHERE symbol='000001'", resume_db)
    raw = pd.read_sql_query("SELECT * FROM raw_prices WHERE symbol='000001'", resume_db)
    for data in (daily, raw):
        data["trade_date"] = pd.to_datetime(data["trade_date"]).dt.date
    resume_db.execute(f"UPDATE prices SET {column}=? WHERE symbol='000001'", (value,))
    resume_db.commit()
    result = FetchResult("000001", "success", "SZ", data=daily, raw_data=raw,
                         start_date=date(2026, 10, 30), end_date=date(2026, 10, 30),
                         write_policy="resume_target")
    assert collector.save_dual_batch(resume_db, "prices", "raw_prices", [result]) == (1, 1)
    assert collector.select_resume_symbols(
        resume_db, "prices", "raw_prices", ["000001"], date(2026, 10, 30)
    ) == ([], ["000001"])
    assert resume_db.execute("SELECT count(*) FROM raw_prices WHERE symbol='000001'").fetchone()[0] == 1


def test_resume_post_write_invalid_pair_rolls_back_both_tables(resume_db):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    daily = pd.read_sql_query("SELECT * FROM prices WHERE symbol='000001'", resume_db)
    raw = pd.read_sql_query("SELECT * FROM raw_prices WHERE symbol='000001'", resume_db)
    for data in (daily, raw):
        data["trade_date"] = pd.to_datetime(data["trade_date"]).dt.date
    raw["close"] = 1309.22
    before = list(resume_db.iterdump())
    result = FetchResult("000001", "success", "SZ", data=daily, raw_data=raw,
                         start_date=date(2026, 10, 30), end_date=date(2026, 10, 30),
                         write_policy="resume_target")
    with pytest.raises(ValueError, match="resume.*validation"):
        collector.save_dual_batch(resume_db, "prices", "raw_prices", [result])
    assert list(resume_db.iterdump()) == before


def test_resume_cleans_duplicate_target_rows_but_preserves_history_and_other_symbols(resume_db):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    daily = pd.read_sql_query("SELECT * FROM prices WHERE symbol='000001'", resume_db)
    raw = pd.read_sql_query("SELECT * FROM raw_prices WHERE symbol='000001'", resume_db)
    for data in (daily, raw):
        data["trade_date"] = pd.to_datetime(data["trade_date"]).dt.date
    resume_db.execute("INSERT INTO prices SELECT symbol,'2026-10-29',open,close,high,low,"
                      "volume,amount,pct_chg,turnover_rate,source,quality_flag,run_id,updated_at,"
                      "adjustment_mode,market FROM prices WHERE symbol='000001'")
    resume_db.execute("INSERT INTO prices SELECT symbol,'2026-10-30 00:00:00.000000',"
                      "open,close,high,low,volume,amount,pct_chg,turnover_rate,source,quality_flag,"
                      "run_id,updated_at,adjustment_mode,market FROM prices WHERE symbol='000001' "
                      "AND trade_date='2026-10-30'")
    resume_db.execute("UPDATE raw_prices SET close=0 WHERE symbol='000001'")
    resume_db.commit()
    queries = {table: f"SELECT * FROM {table} WHERE symbol<>'000001' "
                     "OR trade_date<'2026-10-30' ORDER BY symbol,trade_date"
               for table in ("prices", "raw_prices")}
    before = {table: resume_db.execute(query).fetchall() for table, query in queries.items()}
    result = FetchResult("000001", "success", "SZ", data=daily, raw_data=raw,
                         start_date=date(2026, 10, 30), end_date=date(2026, 10, 30),
                         write_policy="resume_target")
    collector.save_dual_batch(resume_db, "prices", "raw_prices", [result])
    assert collector.select_resume_symbols(
        resume_db, "prices", "raw_prices", ["000001"], date(2026, 10, 30)
    ) == ([], ["000001"])
    assert {table: resume_db.execute(query).fetchall()
            for table, query in queries.items()} == before


@pytest.mark.parametrize("target,full_refresh,valid", [
    ("2026-10-30", False, True), ("2026-10-29", False, False),
    ("2026-10-31", False, False), ("2026-10-30", True, False),
])
def test_resume_date_guard_preserves_historical_adjustment_boundary(target, full_refresh, valid):
    from datetime import datetime
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    day = date.fromisoformat(target)
    if valid:
        collector.validate_resume_request(day, day, full_refresh, datetime(2026, 10, 31, 10))
    else:
        with pytest.raises(ValueError, match="latest completed"):
            collector.validate_resume_request(day, day, full_refresh, datetime(2026, 10, 31, 10))


def test_resume_all_complete_cli_never_opens_a_market_connection(resume_db, monkeypatch):
    import sys
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    monkeypatch.setattr(collector, "ensure_data_core_schema", lambda *args: None)
    monkeypatch.setattr(collector, "get_db_connection", lambda: resume_db)
    monkeypatch.setattr(collector, "load_symbols", lambda *args: ["000001", "920001"])
    monkeypatch.setattr(collector, "validate_resume_request", lambda *args: None)
    monkeypatch.setattr(collector, "log_data_source_run", lambda *args, **kwargs: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("complete dates need neither discovery nor a history scan")

    monkeypatch.setattr(collector, "AdapterPool", forbidden)
    monkeypatch.setattr(collector, "load_last_dates", forbidden)
    monkeypatch.setattr(sys, "argv", ["collector", "--start", "2026-10-30", "--end",
                                     "2026-10-30", "--force-window", "--resume-target-day",
                                     "--table", "prices", "--raw-table", "raw_prices"])
    assert collector.main() == 0


@pytest.mark.parametrize("reason,expected", [
    ("no raw data", "WARNING"), ("source unavailable", "FAIL"),
    ("malformed payload", "FAIL"),
])
def test_source_summary_separates_no_raw_data_from_source_outage(monkeypatch, reason, expected):
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    logs = []
    monkeypatch.setattr(collector, "log_data_source_run", lambda *args, **kwargs: logs.append(kwargs))
    collector.log_source_summary(
        None, run_id="fixture", trade_date="2026-10-30", duration=1,
        results=[FetchResult("000001", "skip", "SZ", reason="up-to-date"),
                 FetchResult("000004", "fail", "SZ", reason=reason)],
    )
    row = next(row for row in logs if row["market"] == "SZ")
    assert row["status"] == expected
    assert row["fail_count"] == 1


@pytest.mark.parametrize("recovery_available,permanent_outage", [
    (True, False), (False, False), (True, True),
])
def test_batch_recovers_once_without_refetching_successes(
    monkeypatch, recovery_available, permanent_outage
):
    from types import SimpleNamespace
    from scripts.standalone import collect_stock_kline_mootdx_qfq as collector

    pools = []
    calls = []
    writes = []

    class Pool:
        STARTUP_BUDGET_SECONDS = 30.0

        def __init__(self, **kwargs):
            self.generation = len(pools)
            self.available = 1 if self.generation == 0 or recovery_available else 0
            self.closed = False
            pools.append(self)

        def close(self):
            self.closed = True

    def fetch(symbol, *args, **kwargs):
        pool = args[5]
        assert not pool.closed
        calls.append((symbol, pool.generation))
        status = "fail" if symbol == "000002" and (pool.generation == 0 or permanent_outage) else "success"
        return FetchResult(symbol, status, "SZ", reason="source unavailable" if status == "fail" else "")

    def save(conn, table, raw_table, results):
        writes.extend(r.symbol for r in results)
        return len(results), len(results)

    monkeypatch.setattr(collector, "AdapterPool", Pool)
    monkeypatch.setattr(collector, "fetch_one_symbol", fetch)
    monkeypatch.setattr(collector, "save_dual_batch", save)
    args = SimpleNamespace(workers=1, pool_size=1, timeout=1, connect_retries=1,
                           connect_retry_delay=0, submit_window=1, batch_size=2,
                           diagnostic_only=False, source_outage_threshold=1,
                           lookback_days=1, full_refresh=False, force_window=True,
                           max_bars=40, table="prices", raw_table="raw_prices")
    result = collector.collect_with_recovery(
        args, None, ["000001", "000002", "000003"], {}, Pool(),
        date(2026, 10, 30), date(2026, 10, 30),
    )
    assert len(pools) == 2
    assert all(pool.closed for pool in pools)
    assert calls.count(("000001", 0)) == 1
    assert ("000001", 1) not in calls
    assert result.recoveries == 1
    recovered = recovery_available and not permanent_outage
    assert result.outage is (not recovered)
    assert writes == (["000001", "000002", "000003"] if recovered else ["000001"])
    assert [r.status for r in result.results] == (["success"] * 3 if recovered else ["success", "fail"])


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
