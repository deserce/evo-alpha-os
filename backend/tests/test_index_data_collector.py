import socket
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# Imports may initialize a configured cloud engine; tests must never connect to it.
with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database on import")):
    from data_job.collectors import index_data_collector as index_module
    from data_job.collectors.index_data_collector import IndexDataCollector

from data_job.core.base_collector import NetworkError
from data_job.core.mootdx_adapter import MARKET_SH


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", unexpected_connection)
    monkeypatch.setattr(index_module.requests.sessions.Session, "request", unexpected_connection)


@pytest.fixture
def run_collector(tmp_path):
    collector = IndexDataCollector.__new__(IndexDataCollector)
    collector.engine = create_engine(f"sqlite:///{tmp_path / 'indices.db'}")
    collector.table_name = "index_daily"
    collector.index_list = [
        {"code": "sh000300", "name": "沪深300", "symbol": "000300", "tdx_market": MARKET_SH}
    ]
    collector.session = MagicMock()
    collector.session.get.side_effect = index_module.requests.ConnectionError("Baidu unavailable")
    collector.logger = MagicMock()
    collector.request_timeout = 1
    collector.request_delay = 0
    collector.max_retries = 2
    collector.stats = {
        "total_requests": 0,
        "failed_requests": 0,
        "retry_count": 0,
        "timeout_count": 0,
    }
    collector.log_collection_start = MagicMock()
    collector.log_collection_end = MagicMock()
    try:
        yield collector
    finally:
        collector.engine.dispose()


@pytest.fixture
def sources(monkeypatch):
    calls = MagicMock()
    adapter = MagicMock()
    adapter.is_available.return_value = True
    adapter.get_index_kline.return_value = None
    constructor = MagicMock(return_value=adapter)
    http_get = MagicMock()
    http_get.return_value.json.return_value = {"data": {"klines": []}}
    ak_get = MagicMock(return_value=None)
    calls.attach_mock(adapter.get_index_kline, "tdx")
    calls.attach_mock(http_get, "eastmoney")
    calls.attach_mock(ak_get, "akshare")
    monkeypatch.setattr(index_module, "MootdxAdapter", constructor)
    monkeypatch.setattr(index_module.requests, "get", http_get)
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_index_daily=ak_get))
    monkeypatch.setattr("data_job.core.base_collector.time.sleep", lambda seconds: None)
    return SimpleNamespace(
        adapter=adapter, constructor=constructor, http_get=http_get, ak_get=ak_get, calls=calls
    )


def index_frame(trade_date="2026-06-04"):
    return pd.DataFrame({
        "symbol": ["000300"],
        "trade_date": [trade_date],
        "open": [10.0],
        "high": [11.0],
        "low": [9.5],
        "close": [10.5],
        "volume": [100.0],
        "amount": [1000.0],
        "change": [0.5],
        "pct_chg": [1.0],
    })


def read_index_rows(collector):
    with collector.engine.connect() as conn:
        return conn.execute(text("SELECT * FROM index_daily ORDER BY index_code, trade_date")).all()


def test_default_target_skips_exchange_holiday():
    assert index_module.latest_completed_trading_date(datetime(2026, 9, 25, 16, 0)).isoformat() == "2026-09-24"


def test_parse_eastmoney_klines_uses_reported_amount():
    collector = IndexDataCollector.__new__(IndexDataCollector)
    idx = {"symbol": "399317", "name": "国证A指"}

    df = collector._parse_eastmoney_klines(
        idx,
        [
            "2026-05-08,7540.42,7583.47,7592.73,7531.45,"
            "1475213385,3007460405679.29,0.81,0.00,0.13,2.10"
        ],
    )

    assert len(df) == 1
    row = df.iloc[0]
    assert row["index_code"] == "399317"
    assert row["trade_date"] == "2026-05-08"
    assert row["amount"] == 3007460405679.29
    assert row["amount"] != row["volume"] * row["close"]


def test_fetch_data_prefers_tdx_over_http():
    collector = IndexDataCollector.__new__(IndexDataCollector)
    collector.index_list = [
        {"code": "sh000300", "name": "沪深300", "symbol": "000300", "tdx_market": MARKET_SH}
    ]
    collector._fetch_from_eastmoney = MagicMock(side_effect=AssertionError("HTTP should not run"))
    collector._fetch_from_akshare = MagicMock(side_effect=AssertionError("AkShare should not run"))

    class FakeAdapter:
        def is_available(self):
            return True

        def get_index_kline(self, *args, **kwargs):
            return pd.DataFrame(
                {
                    "symbol": ["000300", "000300"],
                    "trade_date": ["2026-06-03", "2026-06-04"],
                    "open": [10.0, 10.5],
                    "high": [11.0, 11.5],
                    "low": [9.5, 10.0],
                    "close": [10.5, 11.0],
                    "volume": [100.0, 120.0],
                    "amount": [1000.0, 1320.0],
                    "pct_chg": [None, 4.76],
                }
            )

    with patch.object(index_module, "MootdxAdapter", return_value=FakeAdapter()):
        result = collector.fetch_data(start="2026-06-03", end="2026-06-04", max_bars=20)

    assert set(result) == {"000300"}
    assert result["000300"]["trade_date"].max() == "2026-06-04"
    collector._fetch_from_eastmoney.assert_not_called()
    collector._fetch_from_akshare.assert_not_called()


def test_fetch_data_falls_back_after_empty_tdx():
    collector = IndexDataCollector.__new__(IndexDataCollector)
    collector.index_list = [
        {"code": "sh000300", "name": "沪深300", "symbol": "000300", "tdx_market": MARKET_SH}
    ]
    collector._fetch_from_eastmoney = MagicMock(
        return_value=pd.DataFrame(
            {
                "index_code": ["000300"],
                "index_name": ["沪深300"],
                "trade_date": ["2026-06-04"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.5],
                "close": [10.5],
                "volume": [100.0],
                "amount": [1000.0],
                "change": [0.5],
                "pct_chg": [1.0],
            }
        )
    )
    collector._fetch_from_akshare = MagicMock(side_effect=AssertionError("AkShare should not run"))

    class FakeAdapter:
        def is_available(self):
            return True

        def get_index_kline(self, *args, **kwargs):
            return None

    with patch.object(index_module, "MootdxAdapter", return_value=FakeAdapter()):
        result = collector.fetch_data(start="2026-06-03", end="2026-06-04", max_bars=20)

    assert result["000300"]["trade_date"].tolist() == ["2026-06-04"]
    collector._fetch_from_eastmoney.assert_called_once()


def test_run_succeeds_with_tdx_when_baidu_is_unreachable(run_collector, sources):
    sources.adapter.get_index_kline.return_value = index_frame()

    assert run_collector.run(start="2026-06-03", end="2026-06-04", max_bars=20) is True

    run_collector.session.get.assert_not_called()
    sources.constructor.assert_called_once_with(max_retries=2, timeout=8)
    sources.adapter.get_index_kline.assert_called_once_with(
        "000300", start_date="2026-06-03", end_date="2026-06-04",
        period="daily", market=MARKET_SH, max_bars=20,
    )
    sources.http_get.assert_not_called()
    sources.ak_get.assert_not_called()
    rows = read_index_rows(run_collector)
    assert len(rows) == 1
    assert rows[0]._mapping["trade_date"] == "2026-06-04"
    assert run_collector.log_collection_end.call_args.args[0] is True


def test_health_check_only_checks_database_and_releases_connection(run_collector, sources):
    connection = MagicMock()
    with patch.object(run_collector.engine, "connect") as connect:
        connect.return_value.__enter__.return_value = connection

        assert run_collector._health_check() is None

        connect.assert_called_once_with()
        assert str(connection.execute.call_args.args[0]) == "SELECT 1"
        connect.return_value.__exit__.assert_called_once_with(None, None, None)
    run_collector.session.get.assert_not_called()
    sources.constructor.assert_not_called()
    sources.http_get.assert_not_called()
    sources.ak_get.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["connect", "execute"])
def test_database_failure_stops_run_before_collection(run_collector, sources, failure_stage):
    error = SQLAlchemyError("database unavailable")
    with (
        patch.object(run_collector.engine, "connect") as connect,
        patch.object(run_collector, "_init_table") as init_table,
        patch.object(run_collector, "fetch_data") as fetch_data,
    ):
        if failure_stage == "connect":
            connect.side_effect = error
        else:
            connect.return_value.__enter__.return_value.execute.side_effect = error

        with pytest.raises(NetworkError, match="数据库连接失败: database unavailable") as caught:
            run_collector._health_check()
        assert caught.value.__cause__ is error
        if failure_stage == "execute":
            connect.return_value.__exit__.assert_called_once()

        assert run_collector.run(start="2026-06-03", end="2026-06-04") is False

        init_table.assert_not_called()
        fetch_data.assert_not_called()
    run_collector.session.get.assert_not_called()
    sources.constructor.assert_not_called()
    message = run_collector.log_collection_end.call_args.args
    assert message == (False, "数据库连接失败: database unavailable")


def test_run_falls_back_to_eastmoney_without_baidu_probe(run_collector, sources):
    sources.http_get.return_value.json.return_value = {
        "data": {"klines": ["2026-06-04,10,10.5,11,9.5,100,1000,1,1,0.5,2"]}
    }

    assert run_collector.run(start="2026-06-03", end="2026-06-04") is True

    assert [call[0] for call in sources.calls.method_calls] == ["tdx", "eastmoney"]
    assert sources.http_get.call_args.kwargs["url"].startswith("https://push2his.eastmoney.com/")
    sources.ak_get.assert_not_called()
    run_collector.session.get.assert_not_called()
    assert len(read_index_rows(run_collector)) == 1


def test_run_falls_back_to_akshare_without_baidu_probe(run_collector, sources):
    sources.ak_get.return_value = index_frame().rename(columns={"trade_date": "date"})

    assert run_collector.run(start="2026-06-03", end="2026-06-04") is True

    assert [call[0] for call in sources.calls.method_calls] == ["tdx", "eastmoney", "akshare"]
    sources.ak_get.assert_called_once_with(symbol="sh000300")
    run_collector.session.get.assert_not_called()
    assert len(read_index_rows(run_collector)) == 1


@pytest.mark.parametrize("source_failure", ["empty", "exception"])
def test_run_returns_false_and_keeps_old_data_when_all_sources_fail(
    run_collector, sources, source_failure,
):
    run_collector._init_table()
    old_data = run_collector._normalize_index_df(
        run_collector.index_list[0], index_frame("2026-06-03"),
        datetime(2026, 6, 3), datetime(2026, 6, 4),
    )
    run_collector.save_data(old_data)
    before = read_index_rows(run_collector)
    if source_failure == "exception":
        sources.http_get.side_effect = index_module.requests.ConnectionError("Eastmoney unavailable")
        sources.ak_get.side_effect = index_module.requests.ConnectionError("AkShare unavailable")

    with patch.object(run_collector, "save_data", wraps=run_collector.save_data) as save_data:
        assert run_collector.run(start="2026-06-03", end="2026-06-04") is False
        save_data.assert_not_called()

    retries = 2 if source_failure == "exception" else 1
    assert [call[0] for call in sources.calls.method_calls] == (
        ["tdx"] + ["eastmoney"] * retries + ["akshare"] * retries
    )
    assert read_index_rows(run_collector) == before
    run_collector.session.get.assert_not_called()
    run_collector.log_collection_end.assert_called_once_with(False, "无数据获取")


@pytest.mark.parametrize("coverage_failure", ["missing", "stale"])
def test_run_returns_false_when_target_date_coverage_is_incomplete(
    run_collector, sources, coverage_failure,
):
    trade_date = "2026-06-03" if coverage_failure == "stale" else "2026-06-04"
    sources.adapter.get_index_kline.side_effect = [index_frame(trade_date), None]
    if coverage_failure == "missing":
        run_collector.index_list.append(
            {"code": "sh000001", "name": "上证指数", "symbol": "000001", "tdx_market": MARKET_SH}
        )

    assert run_collector.run(start="2026-06-03", end="2026-06-04") is False

    success, message = run_collector.log_collection_end.call_args.args
    assert success is False
    expected = "缺失指数=['000001']" if coverage_failure == "missing" else "未到目标日=['000300']"
    assert expected in message
    run_collector.session.get.assert_not_called()
