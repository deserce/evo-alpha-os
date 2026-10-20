import pandas as pd

from data_job.collectors.stock_valuation_collector import StockValuationCollector
from data_job.core.base_collector import NetworkError


def test_run_uses_local_snapshot_when_health_check_fails():
    collector = StockValuationCollector.__new__(StockValuationCollector)
    saved = []
    ended = []

    collector.log_collection_start = lambda: None
    collector.log_collection_end = lambda success, message: ended.append((success, message))
    collector._health_check = lambda: (_ for _ in ()).throw(NetworkError("network down"))
    collector._init_table = lambda: None
    collector.fetch_local_snapshot = lambda: pd.DataFrame(
        {
            "_source": ["local_kline"],
            "code": ["000001"],
            "name": ["平安银行"],
            "trade_date": ["2026-05-07"],
            "price": [10.0],
            "pct_chg": [1.0],
            "turnover": [2.0],
            "volume": [1000.0],
        }
    )
    collector.process_data = lambda df: df
    collector.save_data = lambda df: saved.append(df.copy())

    assert collector.run() is True
    assert len(saved) == 1
    assert ended and ended[-1][0] is True


def test_collector_import_does_not_shadow_backend_scripts():
    import scripts.run_daily_data_workflow as workflow

    assert workflow.Task("noop", ("python", "-V")).name == "noop"


def test_fetch_data_prefers_tencent_direct(monkeypatch):
    collector = StockValuationCollector.__new__(StockValuationCollector)
    collector.fetch_tencent_snapshot = lambda: pd.DataFrame(
        {
            "code": ["000001"],
            "name": ["平安银行"],
            "price": [10.0],
            "_source": ["tencent_direct"],
        }
    )

    def fail_akshare():
        raise AssertionError("AkShare fallback should not run when Tencent succeeds")

    monkeypatch.setattr("akshare.stock_zh_a_spot_em", fail_akshare)

    df = collector.fetch_data()

    assert df["_source"].iloc[0] == "tencent_direct"


def test_historical_target_uses_matching_local_kline_without_network():
    collector = StockValuationCollector.__new__(StockValuationCollector)
    saved = []
    requested_targets = []

    collector.log_collection_start = lambda: None
    collector.log_collection_end = lambda success, message: None
    collector._init_table = lambda: None
    collector._health_check = lambda: (_ for _ in ()).throw(
        AssertionError("historical valuation must not probe live quotes")
    )
    collector.fetch_data = lambda: (_ for _ in ()).throw(
        AssertionError("historical valuation must not use current snapshots")
    )

    def local_snapshot(target_date=None):
        requested_targets.append(target_date)
        return pd.DataFrame(
            {
                "_source": ["local_kline"],
                "code": ["000001"],
                "trade_date": [target_date],
            }
        )

    collector.fetch_local_snapshot = local_snapshot
    collector.process_data = lambda df: df
    collector.save_data = lambda df: saved.append(df.copy())

    assert collector.run(target_date="2026-07-14") is True
    assert requested_targets == ["2026-07-14"]
    assert saved[0]["trade_date"].tolist() == ["2026-07-14"]


def test_explicit_target_snapshot_uses_indexable_date_query(monkeypatch):
    collector = StockValuationCollector.__new__(StockValuationCollector)
    collector.engine = object()
    captured = {}

    def read_sql(query, engine, params=None):
        captured["query"] = str(query)
        captured["engine"] = engine
        captured["params"] = params
        return pd.DataFrame()

    monkeypatch.setattr(
        "data_job.collectors.stock_valuation_collector.pd.read_sql",
        read_sql,
    )

    collector.fetch_local_snapshot("2026-07-14")

    assert "k.trade_date = :target_date" in captured["query"]
    assert "LEFT JOIN stock_valuation_daily" not in captured["query"]
    assert captured["params"] == {"target_date": "2026-07-14"}
