from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError

from data_job.collectors.stock_valuation_collector import StockValuationCollector
from data_job.core.base_collector import NetworkError


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("valuation tests must not access the network")

    monkeypatch.setattr("socket.socket.connect", blocked)
    monkeypatch.setattr("socket.create_connection", blocked)


@pytest.fixture
def local_collector(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'valuation.db'}")
    collector = StockValuationCollector.__new__(StockValuationCollector)
    collector.engine = engine
    collector.table_name = "stock_valuation_daily"
    collector.log_collection_start = lambda: None
    collector.log_collection_end = lambda success, message: None
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY, name TEXT, pe REAL, pb REAL, market_cap REAL
            )
        """))
        conn.execute(text("""
            INSERT INTO stock_info VALUES ('000001', 'Bank', 8, 1.2, 200)
        """))
        conn.execute(text("""
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, close REAL, pct_chg REAL,
                turnover_rate REAL, volume REAL, PRIMARY KEY (symbol, trade_date)
            )
        """))
    collector._init_table()
    try:
        yield collector
    finally:
        engine.dispose()


def valuation_frame(collector, **overrides):
    row = {
        "code": "000001", "name": "Bank", "trade_date": "2020-01-02",
        "price": 10.0, "pe_ttm": 9.0, "pb": 1.5, "total_mv": 30_000_000_000.0,
        "circ_mv": 20_000_000_000.0, "pct_chg": 1.0, "turnover": 2.0,
        "volume_ratio": 1.0, "_source": "tencent_direct",
    }
    row.update(overrides)
    return collector.process_data(pd.DataFrame([row]))


def stored_valuations(collector):
    return pd.read_sql(
        "SELECT * FROM stock_valuation_daily ORDER BY trade_date, code", collector.engine
    )


def test_partial_response_preserves_other_keys_and_is_idempotent(local_collector):
    collector = local_collector
    existing = pd.concat([
        valuation_frame(collector),
        valuation_frame(collector, code="000002"),
        valuation_frame(collector, trade_date="2020-01-03"),
    ], ignore_index=True)
    with collector.engine.begin() as conn:
        existing.to_sql(collector.table_name, conn, if_exists="append", index=False)

    partial = valuation_frame(collector, price=11.0)
    collector.save_data(partial)
    collector.save_data(partial)

    stored = stored_valuations(collector)
    assert len(stored) == 3
    assert stored["price"].tolist() == [11.0, 10.0, 10.0]


@pytest.mark.parametrize("source", ["tencent_direct", "eastmoney", None, ""])
def test_local_estimate_cannot_replace_real_or_unlabelled_row(local_collector, source):
    collector = local_collector
    real = valuation_frame(collector)
    real["source"] = source
    with collector.engine.begin() as conn:
        real.to_sql(collector.table_name, conn, if_exists="append", index=False)
    before = stored_valuations(collector)

    collector.save_data(valuation_frame(
        collector, _source="local_kline", price=11.0, volume=1_000_000.0,
    ))

    pd.testing.assert_frame_equal(stored_valuations(collector), before)


@pytest.mark.parametrize("source", ["local_kline", "tencent_direct"])
def test_historical_save_does_not_change_current_stock_info(local_collector, source):
    collector = local_collector
    collector.save_data(valuation_frame(collector, _source=source, volume=1_000_000.0))

    with collector.engine.connect() as conn:
        current = conn.execute(text("SELECT pe, pb, market_cap FROM stock_info")).one()
    assert tuple(current) == (8.0, 1.2, 200.0)


def test_local_estimate_keeps_unrecoverable_fields_null(local_collector):
    collector = local_collector
    clean = valuation_frame(collector, _source="local_kline", volume=1_000_000.0)

    assert clean.loc[0, "source"] == "local_kline"
    assert clean.loc[0, "circ_mv"] == pytest.approx(500_000_000.0)
    for field in ("pe_ttm", "pb", "total_mv", "volume_ratio",
                  "limit_up_price", "limit_down_price"):
        assert pd.isna(clean.loc[0, field]), field

    collector.save_data(clean)
    stored = stored_valuations(collector)
    assert stored.loc[0, "source"] == "local_kline"
    assert stored.loc[0, ["pe_ttm", "pb", "total_mv"]].isna().all()


def test_local_snapshot_fills_missing_codes_on_partially_populated_date(local_collector):
    collector = local_collector
    with collector.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2020-01-02', 10, 1, 2, 1000000),
                ('000002', '2020-01-02', 20, 1, 2, 1000000)
        """))
        valuation_frame(collector).to_sql(
            collector.table_name, conn, if_exists="append", index=False,
        )

    missing = collector.fetch_local_snapshot()

    assert missing["code"].tolist() == ["000002"]
    collector.save_data(collector.process_data(missing))
    assert stored_valuations(collector)["code"].tolist() == ["000001", "000002"]
    assert collector.fetch_local_snapshot().empty


def test_historical_missing_turnover_keeps_price_without_invented_valuation(local_collector):
    collector = local_collector
    with collector.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2020-01-02', 10, 1, NULL, 1000000)
        """))
    collector._health_check = lambda: pytest.fail("historical run must not probe network")
    collector.fetch_data = lambda: pytest.fail("historical run must not fetch live data")

    assert collector.run(target_date="2020-01-02") is True

    stored = stored_valuations(collector)
    assert stored.loc[0, "price"] == 10
    assert stored.loc[0, "source"] == "local_kline"
    assert stored.loc[0, ["pe_ttm", "pb", "circ_mv", "total_mv", "turnover"]].isna().all()


def test_current_real_snapshot_still_updates_stock_info(local_collector):
    collector = local_collector
    collector.save_data(valuation_frame(collector, trade_date=date.today()))

    with collector.engine.connect() as conn:
        current = conn.execute(text("SELECT pe, pb, market_cap FROM stock_info")).one()
    assert tuple(current) == (9.0, 1.5, 300.0)


def test_current_local_snapshot_never_updates_stock_info(local_collector):
    collector = local_collector
    collector.save_data(valuation_frame(
        collector, trade_date=date.today(), _source="local_kline", volume=1_000_000.0,
    ))

    with collector.engine.connect() as conn:
        current = conn.execute(text("SELECT pe, pb, market_cap FROM stock_info")).one()
    assert tuple(current) == (8.0, 1.2, 200.0)


def test_partial_real_fields_do_not_erase_existing_real_values(local_collector):
    collector = local_collector
    collector.save_data(valuation_frame(collector))
    collector.save_data(valuation_frame(
        collector, price=11.0, pe_ttm=None, pb=None, total_mv=None, circ_mv=None,
    ))

    stored = stored_valuations(collector).iloc[0]
    assert stored["price"] == 11.0
    assert stored[["pe_ttm", "pb", "total_mv", "circ_mv"]].tolist() == [
        9.0, 1.5, 30_000_000_000.0, 20_000_000_000.0,
    ]


def test_repeated_local_save_removes_legacy_fabricated_values(local_collector):
    collector = local_collector
    legacy = valuation_frame(collector)
    legacy["source"] = "local_kline"
    legacy[["pe_ttm", "pb", "volume_ratio", "limit_up_price", "limit_down_price"]] = 0
    legacy["total_mv"] = legacy["circ_mv"]
    with collector.engine.begin() as conn:
        legacy.to_sql(collector.table_name, conn, if_exists="append", index=False)

    collector.save_data(valuation_frame(collector, _source="local_kline", volume=1_000_000.0))

    stored = stored_valuations(collector).iloc[0]
    assert stored["source"] == "local_kline"
    assert stored[["pe_ttm", "pb", "total_mv", "volume_ratio",
                   "limit_up_price", "limit_down_price"]].isna().all()


def test_real_upgrade_does_not_relabel_local_estimates_as_real(local_collector):
    collector = local_collector
    collector.save_data(valuation_frame(collector, _source="local_kline", volume=1_000_000.0))
    collector.save_data(valuation_frame(collector, price=11.0, total_mv=None, circ_mv=None))

    stored = stored_valuations(collector).iloc[0]
    assert stored["source"] == "tencent_direct"
    assert stored["pe_ttm"] == 9.0
    assert stored["pb"] == 1.5
    assert pd.isna(stored["circ_mv"])
    assert pd.isna(stored["total_mv"])


@pytest.mark.parametrize("local_first", [False, True])
def test_duplicate_keys_in_one_batch_keep_real_source(local_collector, local_first):
    collector = local_collector
    real = valuation_frame(collector)
    local = valuation_frame(collector, _source="local_kline", volume=1_000_000.0)
    rows = [local, real] if local_first else [real, local]
    combined = pd.concat(rows, ignore_index=True)

    collector.save_data(combined)
    collector.save_data(combined)

    stored = stored_valuations(collector)
    assert len(stored) == 1
    assert stored.loc[0, "source"] == "tencent_direct"
    assert stored.loc[0, "pe_ttm"] == 9.0


@pytest.mark.parametrize("field,value", [
    ("volume", None), ("volume", -1), ("volume", float("inf")),
    ("turnover", 0), ("turnover", None), ("turnover", "invalid"),
    ("price", float("inf")),
])
def test_incomplete_local_inputs_do_not_invent_market_value(local_collector, field, value):
    overrides = {"_source": "local_kline", "volume": 1_000_000.0, field: value}
    clean = valuation_frame(local_collector, **overrides)

    assert clean.loc[0, "source"] == "local_kline"
    assert clean.loc[0, ["pe_ttm", "pb", "circ_mv", "total_mv"]].isna().all()


@pytest.mark.parametrize("field,value", [
    ("code", None), ("trade_date", None), ("trade_date", "not-a-date"),
])
def test_invalid_primary_key_is_rejected_before_writing(local_collector, field, value):
    collector = local_collector
    clean = valuation_frame(collector)
    clean[field] = value

    with pytest.raises(ValueError):
        collector.save_data(clean)
    assert stored_valuations(collector).empty


def test_failed_batch_rolls_back_without_deleting_or_updating_other_rows(local_collector):
    collector = local_collector
    collector.save_data(valuation_frame(collector))
    before = stored_valuations(collector)
    with collector.engine.begin() as conn:
        conn.execute(text("""
            CREATE TRIGGER reject_second_stock BEFORE INSERT ON stock_valuation_daily
            WHEN NEW.code = '000002'
            BEGIN SELECT RAISE(ABORT, 'test rejected stock'); END
        """))
    batch = pd.concat([
        valuation_frame(collector, price=11.0),
        valuation_frame(collector, code="000002"),
    ], ignore_index=True)

    with pytest.raises(IntegrityError, match="test rejected stock"):
        collector.save_data(batch)

    pd.testing.assert_frame_equal(stored_valuations(collector), before)


def test_historical_run_reports_local_degradation_and_only_saves_target(local_collector, caplog):
    collector = local_collector
    with collector.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2020-01-02', 10, 1, 2, 1000000),
                ('000001', '2020-01-03', 11, 1, 2, 1000000)
        """))
    collector._health_check = lambda: pytest.fail("historical run must not probe network")
    collector.fetch_data = lambda: pytest.fail("historical run must not fetch live data")

    assert collector.run(target_date="2020-01-02") is True
    assert collector.run(target_date="2020-01-02") is True

    stored = stored_valuations(collector)
    assert stored["trade_date"].tolist() == ["2020-01-02"]
    assert stored["source"].tolist() == ["local_kline"]
    assert "degraded" in caplog.text
    assert "PE/PB" in caplog.text


def test_missing_historical_kline_returns_false_without_writes(local_collector):
    collector = local_collector
    collector._health_check = lambda: pytest.fail("historical run must not probe network")
    collector.fetch_data = lambda: pytest.fail("historical run must not fetch live data")

    assert collector.run(target_date="2020-01-02") is False
    assert stored_valuations(collector).empty


def test_cleaned_empty_snapshot_returns_false_without_saving(local_collector):
    collector = local_collector
    collector.fetch_local_snapshot = lambda target_date=None: pd.DataFrame([
        {"code": "invalid", "_source": "local_kline", "trade_date": target_date},
    ])
    collector.save_data = lambda df: pytest.fail("empty snapshot must not be saved")

    assert collector.run(target_date="2020-01-02") is False


def test_sql_values_are_bound_and_save_issues_no_delete(local_collector):
    collector = local_collector
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    malicious_name = "x'); DROP TABLE stock_info; --"
    event.listen(collector.engine, "before_cursor_execute", capture)
    try:
        collector.save_data(valuation_frame(collector, name=malicious_name))
    finally:
        event.remove(collector.engine, "before_cursor_execute", capture)

    assert stored_valuations(collector).loc[0, "name"] == malicious_name
    assert not any("DELETE" in statement.upper() for statement in statements)
    assert not any(malicious_name in statement for statement in statements)


def test_large_history_save_uses_bounded_batches_and_returns_connections(local_collector):
    collector = local_collector
    rows = pd.concat([valuation_frame(collector)] * 1001, ignore_index=True)
    rows["code"] = [f"{600000 + index:06d}" for index in range(len(rows))]
    batch_sizes = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("INSERT INTO stock_valuation_daily"):
            batch_sizes.append(len(parameters) if executemany else 1)

    event.listen(collector.engine, "before_cursor_execute", capture)
    try:
        collector.save_data(rows)
        collector.save_data(rows)
    finally:
        event.remove(collector.engine, "before_cursor_execute", capture)

    assert batch_sizes == [500, 500, 1, 500, 500, 1]
    assert len(stored_valuations(collector)) == 1001
    assert collector.engine.pool.checkedout() == 0


def test_implicit_local_backfill_stays_within_thirty_day_window(local_collector):
    collector = local_collector
    with collector.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2020-01-01', 10, 1, 2, 1000000),
                ('000001', '2020-02-01', 11, 1, 2, 1000000)
        """))

    snapshot = collector.fetch_local_snapshot()

    assert snapshot["trade_date"].tolist() == ["2020-02-01"]
    assert collector.fetch_local_snapshot("2020-01-01")["trade_date"].tolist() == ["2020-01-01"]


def test_real_snapshot_missing_numeric_fields_remain_null(local_collector):
    clean = local_collector.process_data(pd.DataFrame([
        {"代码": "000001", "名称": "Bank", "最新价": 10, "_source": "eastmoney"},
    ]))

    assert clean.loc[0, "price"] == 10
    assert clean.loc[0, "trade_date"] == date.today()
    assert clean.loc[0, ["pe_ttm", "pb", "total_mv", "circ_mv"]].isna().all()


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
