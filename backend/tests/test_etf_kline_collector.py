from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy import create_engine, text

from data_job.collectors import etf_kline_collector as etf_module
from data_job.collectors.etf_kline_collector import ETFKlineCollector


def test_default_target_skips_exchange_holiday():
    assert etf_module.latest_completed_trading_date(datetime(2026, 9, 25, 16, 0)).isoformat() == "2026-09-24"


def test_fetch_etf_kline_prefers_tdx_over_http():
    collector = ETFKlineCollector.__new__(ETFKlineCollector)
    collector.http_failure_count = 0
    collector.http_circuit_threshold = 3
    collector._fetch_from_eastmoney = MagicMock(side_effect=AssertionError("HTTP should not run"))

    class FakeAdapter:
        def is_available(self):
            return True

        def get_etf_kline(self, *args, **kwargs):
            return pd.DataFrame(
                {
                    "symbol": ["510300", "510300", "510300"],
                    "trade_date": ["2026-06-02", "2026-06-03", "2026-06-04"],
                    "open": [4.0, 4.1, 4.2],
                    "high": [4.2, 4.3, 4.4],
                    "low": [3.9, 4.0, 4.1],
                    "close": [4.1, 4.2, 4.3],
                    "volume": [100.0, 120.0, 130.0],
                    "amount": [410.0, 504.0, 559.0],
                }
            )

    result = collector.fetch_etf_kline(
        "510300",
        start_date=datetime(2026, 5, 25),
        end_date=datetime(2026, 6, 4),
        save_start=datetime(2026, 6, 3),
        save_end=datetime(2026, 6, 4),
        max_bars=20,
        tdx_adapter=FakeAdapter(),
    )

    assert result["trade_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-06-03",
        "2026-06-04",
    ]
    assert "turnover_rate" not in result.columns
    assert result["pct_chg"].notna().all()
    collector._fetch_from_eastmoney.assert_not_called()


def test_eastmoney_circuit_breaker_skips_http_call():
    collector = ETFKlineCollector.__new__(ETFKlineCollector)
    collector.http_failure_count = 3
    collector.http_circuit_threshold = 3

    with patch.object(etf_module.ak, "fund_etf_hist_em", side_effect=AssertionError("no HTTP")):
        result = collector._fetch_from_eastmoney(
            "510300",
            start_date=datetime(2026, 6, 3),
            end_date=datetime(2026, 6, 4),
        )

    assert result is None


def test_save_etf_kline_replaces_timestamp_dates(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'etf.db'}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE etf_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                pct_chg REAL,
                PRIMARY KEY (symbol, trade_date)
            )
        """))
        conn.execute(text("""
            INSERT INTO etf_daily_prices VALUES
            ('510300', '2026-06-03 00:00:00.000000', 1, 1, 1, 1, 1, 1, 0),
            ('510300', '2026-06-04 00:00:00.000000', 1, 1, 1, 1, 1, 1, 0)
        """))

    collector = ETFKlineCollector.__new__(ETFKlineCollector)
    collector.engines = [("local", engine)]
    collector.table_name = "etf_daily_prices"
    df = pd.DataFrame(
        {
            "symbol": ["510300", "510300"],
            "trade_date": pd.to_datetime(["2026-06-03", "2026-06-04"]),
            "open": [4.0, 4.1],
            "high": [4.2, 4.3],
            "low": [3.9, 4.0],
            "close": [4.1, 4.2],
            "volume": [100.0, 120.0],
            "amount": [410.0, 504.0],
            "pct_chg": [1.0, 2.0],
        }
    )

    assert collector.save_etf_kline("510300", df)

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT trade_date, close
            FROM etf_daily_prices
            ORDER BY trade_date
        """)).fetchall()

    assert rows == [("2026-06-03", 4.1), ("2026-06-04", 4.2)]
