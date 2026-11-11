import socket
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests
from sqlalchemy import create_engine, text

from data_job.collectors import etf_kline_collector as etf_module
from data_job.collectors.etf_kline_collector import ETFKlineCollector
from data_job.core.base_collector import NetworkError


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def unexpected_network(*args, **kwargs):
        pytest.fail("ETF collector tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", unexpected_network)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected_network)
    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_network)


@pytest.fixture
def etf_collector(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'etf_run.db'}")
    collector = ETFKlineCollector.__new__(ETFKlineCollector)
    collector.engine = engine
    collector.engines = [("local", engine)]
    collector.table_name = "etf_daily_prices"
    collector.http_failure_count = 0
    collector.http_circuit_threshold = 3
    collector.request_delay = 0
    collector.max_retries = 3
    collector.logger = etf_module.logger
    collector.stats = {
        "total_requests": 0,
        "failed_requests": 0,
        "retry_count": 0,
        "timeout_count": 0,
    }
    collector.session = MagicMock()
    collector.session.get.side_effect = requests.ConnectionError("Baidu is unreachable")
    collector.log_collection_start = MagicMock()
    collector.log_collection_end = MagicMock()

    rows = pd.DataFrame({
        "trade_date": ["2026-06-02", "2026-06-03", "2026-06-04"],
        "open": [4.0, 4.1, 4.2],
        "high": [4.2, 4.3, 4.4],
        "low": [3.9, 4.0, 4.1],
        "close": [4.1, 4.2, 4.3],
        "volume": [100.0, 120.0, 130.0],
        "amount": [410.0, 504.0, 559.0],
    })
    adapter = MagicMock()
    adapter.is_available.return_value = True
    adapter.get_etf_kline.return_value = rows
    monkeypatch.setattr(etf_module, "MootdxAdapter", MagicMock(return_value=adapter))
    monkeypatch.setattr(etf_module.time, "sleep", MagicMock())
    monkeypatch.setattr(
        etf_module.ak,
        "fund_etf_hist_em",
        MagicMock(side_effect=AssertionError("Unexpected HTTP fallback")),
    )

    try:
        collector._init_table()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO etf_daily_prices VALUES
                ('510300', '2026-06-02', 4.0, 4.2, 3.9, 4.1, 100.0, 410.0, 1.0)
            """))
        yield collector
    finally:
        engine.dispose()


def stored_etf_rows(collector):
    with collector.engines[0][1].connect() as conn:
        return conn.execute(text("""
            SELECT * FROM etf_daily_prices ORDER BY symbol, trade_date
        """)).fetchall()


def test_run_succeeds_with_tdx_when_baidu_is_unreachable(etf_collector):
    old_rows = stored_etf_rows(etf_collector)

    assert etf_collector.run(symbols=["510300"], end="2026-06-04") is True

    rows = stored_etf_rows(etf_collector)
    assert rows[:1] == old_rows
    assert [row.trade_date for row in rows] == ["2026-06-02", "2026-06-03", "2026-06-04"]
    assert rows[1].pct_chg == pytest.approx((4.2 / 4.1 - 1) * 100)
    etf_module.MootdxAdapter.assert_called_once_with(max_retries=2, timeout=8)
    adapter = etf_module.MootdxAdapter.return_value
    adapter.is_available.assert_called_once_with()
    adapter.get_etf_kline.assert_called_once_with(
        "510300",
        start_date="2026-05-23",
        end_date="2026-06-04",
        period="daily",
        max_bars=60,
    )
    etf_collector.session.get.assert_not_called()
    etf_module.ak.fund_etf_hist_em.assert_not_called()
    assert etf_collector.log_collection_end.call_args.args[0] is True


def test_run_stops_before_collection_when_database_fails(etf_collector):
    etf_collector.engine = MagicMock()
    etf_collector.engine.connect.side_effect = RuntimeError("database offline")
    etf_collector._check_network_connection = MagicMock(return_value=True)

    with patch.object(etf_collector, "_init_table") as init_table:
        assert etf_collector.run(symbols=["510300"], end="2026-06-04") is False

    etf_collector.engine.connect.assert_called_once_with()
    init_table.assert_not_called()
    etf_module.MootdxAdapter.assert_not_called()
    etf_module.ak.fund_etf_hist_em.assert_not_called()
    assert etf_collector.log_collection_end.call_args.args[0] is False
    assert "database offline" in etf_collector.log_collection_end.call_args.args[1]


def test_run_returns_false_and_preserves_old_rows_when_sources_fail(etf_collector):
    old_rows = stored_etf_rows(etf_collector)
    adapter = etf_module.MootdxAdapter.return_value
    adapter.get_etf_kline.return_value = None
    etf_module.ak.fund_etf_hist_em.side_effect = RuntimeError("HTTP source unavailable")

    assert etf_collector.run(symbols=["510300"], end="2026-06-04") is False

    adapter.get_etf_kline.assert_called_once()
    assert etf_module.ak.fund_etf_hist_em.call_count == 2
    assert etf_collector.http_failure_count == 1
    assert stored_etf_rows(etf_collector) == old_rows
    etf_collector.session.get.assert_not_called()
    assert etf_collector.log_collection_end.call_args.args[0] is False


def test_health_check_only_executes_database_check(etf_collector):
    etf_collector.engine = MagicMock()
    connection = etf_collector.engine.connect.return_value.__enter__.return_value

    etf_collector._health_check()

    etf_collector.engine.connect.assert_called_once_with()
    connection.execute.assert_called_once()
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    etf_collector.engine.connect.return_value.__exit__.assert_called_once_with(None, None, None)
    etf_collector.session.get.assert_not_called()
    etf_module.MootdxAdapter.assert_not_called()
    etf_module.ak.fund_etf_hist_em.assert_not_called()


def test_database_query_failure_keeps_network_error_contract(etf_collector):
    etf_collector.engine = MagicMock()
    connection = etf_collector.engine.connect.return_value.__enter__.return_value
    error = RuntimeError("database query failed")
    connection.execute.side_effect = error

    with pytest.raises(NetworkError, match="database query failed") as raised:
        etf_collector._health_check()

    assert raised.value.__cause__ is error
    assert etf_collector.run(symbols=["510300"], end="2026-06-04") is False
    etf_collector.engine.connect.return_value.__exit__.assert_called()
    etf_collector.session.get.assert_not_called()
    etf_module.MootdxAdapter.assert_not_called()
    etf_module.ak.fund_etf_hist_em.assert_not_called()


@pytest.mark.parametrize("tdx_available", [True, False])
def test_run_falls_back_to_eastmoney_when_tdx_has_no_data(etf_collector, tdx_available):
    old_rows = stored_etf_rows(etf_collector)
    adapter = etf_module.MootdxAdapter.return_value
    http_rows = adapter.get_etf_kline.return_value.rename(columns={
        "trade_date": "日期",
        "open": "开盘",
        "high": "最高",
        "low": "最低",
        "close": "收盘",
        "volume": "成交量",
        "amount": "成交额",
    })
    adapter.is_available.return_value = tdx_available
    adapter.get_etf_kline.return_value = None
    etf_module.ak.fund_etf_hist_em.side_effect = None
    etf_module.ak.fund_etf_hist_em.return_value = http_rows

    assert etf_collector.run(symbols=["510300"], end="2026-06-04") is True

    rows = stored_etf_rows(etf_collector)
    assert rows[:1] == old_rows
    assert [row.trade_date for row in rows] == ["2026-06-02", "2026-06-03", "2026-06-04"]
    assert rows[1].pct_chg == pytest.approx((4.2 / 4.1 - 1) * 100)
    assert adapter.get_etf_kline.call_count == int(tdx_available)
    etf_module.ak.fund_etf_hist_em.assert_called_once_with(
        symbol="510300", period="daily", start_date="20260523", end_date="20260604",
    )
    assert etf_collector.http_failure_count == 0
    etf_collector.session.get.assert_not_called()


def test_run_keeps_http_circuit_breaker_and_old_data(etf_collector):
    old_rows = stored_etf_rows(etf_collector)
    adapter = etf_module.MootdxAdapter.return_value
    adapter.get_etf_kline.return_value = None
    etf_module.ak.fund_etf_hist_em.side_effect = None
    etf_module.ak.fund_etf_hist_em.return_value = None

    assert etf_collector.run(
        symbols=["510300", "510500", "159915", "588000"], end="2026-06-04",
    ) is False

    assert adapter.get_etf_kline.call_count == 4
    assert etf_module.ak.fund_etf_hist_em.call_count == 3
    assert etf_collector.http_failure_count == etf_collector.http_circuit_threshold
    assert stored_etf_rows(etf_collector) == old_rows
    etf_collector.session.get.assert_not_called()


def test_run_returns_false_when_saved_rows_do_not_cover_target(etf_collector):
    old_rows = stored_etf_rows(etf_collector)
    adapter = etf_module.MootdxAdapter.return_value
    adapter.get_etf_kline.return_value = adapter.get_etf_kline.return_value.iloc[:2]

    assert etf_collector.run(symbols=["510300"], end="2026-06-04") is False

    rows = stored_etf_rows(etf_collector)
    assert rows[:1] == old_rows
    assert [row.trade_date for row in rows] == ["2026-06-02", "2026-06-03"]
    assert "2026-06-04" in etf_collector.log_collection_end.call_args.args[1]
    assert etf_collector.log_collection_end.call_args.args[0] is False
    etf_collector.session.get.assert_not_called()
    etf_module.ak.fund_etf_hist_em.assert_not_called()


def test_run_remains_compatible_with_mocked_health_check(etf_collector):
    etf_collector._health_check = MagicMock()

    assert etf_collector.run(symbols=["510300"], end="2026-06-04") is True

    etf_collector._health_check.assert_called_once_with()


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
