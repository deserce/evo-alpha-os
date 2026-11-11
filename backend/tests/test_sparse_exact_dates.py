"""Exact-date sparse analytics; all databases are disposable pytest fixtures."""

from contextlib import closing
from datetime import date, timedelta
import sqlite3
import sys

import pandas as pd
import pytest

# The CLI path helper must not change package resolution for subsequent API tests.
_import_path = sys.path[:]
try:
    from quant_engine.calculators.risk_plan_calculator import RiskPlanCalculator
    from quant_engine.calculators.timing_signal_calculator import TimingSignalCalculator
    from quant_engine.runner.feature_runner import FeatureRunner
    from quant_engine.runner import feature_runner
finally:
    sys.path[:] = _import_path


DAYS = ["2026-10-21", "2026-10-22", "2026-10-23"]


@pytest.fixture
def sparse_db(tmp_path):
    path = tmp_path / "sparse.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL,
                close REAL, volume REAL, amount REAL, pct_chg REAL, turnover_rate REAL
            );
            CREATE TABLE stock_lifecycle_state (
                trade_date TEXT, symbol TEXT, name TEXT, sector_name TEXT,
                lifecycle_state TEXT, state_rank INTEGER, market_state TEXT,
                industry_state TEXT, rps_20 REAL, rps_50 REAL, rps_120 REAL,
                rps_250 REAL, structure_score REAL
            );
            CREATE TABLE stock_finance_summary (
                code TEXT, report_date TEXT, net_profit_up REAL, revenue_up REAL, roe REAL
            );
            CREATE TABLE pipeline_task_log (
                task_name TEXT, status TEXT, target_dates TEXT
            );
            CREATE TABLE portfolio_trades (trade_id TEXT, quantity INTEGER);
            INSERT INTO portfolio_trades VALUES ('real-trade',100);
        """)
        for day in pd.bdate_range(end=DAYS[-1], periods=60).strftime("%Y-%m-%d"):
            conn.execute(
                "INSERT INTO stock_daily_prices VALUES ('600001',?,9.5,10.2,9.4,10,200,2000,0,1)",
                (day,),
            )
        for day in DAYS:
            conn.execute(
                "INSERT INTO stock_lifecycle_state VALUES "
                "(?,'600001','Test','Sector','S4',4,'M3','I3',92,90,88,85,80)",
                (day,),
            )
        TimingSignalCalculator(str(path))._create_table(conn)
        RiskPlanCalculator(str(path))._create_table(conn)
    return path


def seed_signal(conn, day, signal_id="old-signal"):
    conn.execute(
        "INSERT INTO timing_signals_daily "
        "(signal_id,symbol,trade_date,signal_type,entry_price,stop_loss_price,"
        "stop_loss_pct,risk_amount_pct,suggested_position_pct,is_invalidated) "
        "VALUES (?,'600001',?,'pocket_pivot',10,9,0.1,1,10,0)",
        (signal_id, day),
    )


def seed_plan(conn, day, plan_id="legacy-plan", signal_id="old-signal"):
    conn.execute(
        "INSERT INTO trade_plans (plan_id,signal_id,trade_date,symbol,created_at) "
        "VALUES (?,?,?,'600001','original-stamp')",
        (plan_id, signal_id, day),
    )


def rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


@pytest.mark.parametrize("name", ["timing", "risk"])
def test_runner_preserves_original_sparse_target_dates(name):
    calls = []
    dates = [DAYS[2], DAYS[0]]

    class Calculator:
        def run_daily(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "records": 0}

    runner = object.__new__(FeatureRunner)
    runner.calculators = {name: Calculator()}
    result = runner.run([name], target_dates=dates, missing_only=True)

    assert result[name]["success"]
    assert calls == [{"target_dates": dates, "missing_only": True}]
    assert calls[0]["target_dates"] is dates


def test_timing_exact_date_keeps_later_signals_and_excludes_future_inputs(sparse_db):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        seed_signal(conn, DAYS[2], "future-signal")
        conn.execute(
            "UPDATE stock_daily_prices SET close=100,high=101 WHERE trade_date=?", (DAYS[2],)
        )
        conn.execute(
            "INSERT INTO stock_finance_summary VALUES ('600001',?,999,999,99)", (DAYS[2],)
        )
    calculator = TimingSignalCalculator(str(sparse_db))
    result = calculator.run_daily(target_dates=[DAYS[1]])

    assert result["success"] and result["records"] == 1
    assert result["computed_dates"] == [DAYS[1]]
    with closing(sqlite3.connect(sparse_db)) as conn:
        output = conn.execute(
            "SELECT trade_date,entry_price FROM timing_signals_daily ORDER BY trade_date"
        ).fetchall()
        source = calculator._load_source(conn, [DAYS[1]], "2026-08-01")
    assert output == [(DAYS[1], 10.0), (DAYS[2], 10.0)]
    assert source["net_profit_up"].isna().all()
    assert rows(sparse_db, "portfolio_trades") == [("real-trade", 100)]


def test_timing_internal_gap_uses_untouched_intermediate_cooldown(sparse_db):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        seed_signal(conn, DAYS[1], "middle-signal")
        conn.execute("UPDATE stock_lifecycle_state SET state_rank=2 WHERE trade_date=?", (DAYS[0],))

    result = TimingSignalCalculator(str(sparse_db)).run_daily(target_dates=[DAYS[0], DAYS[2]])

    assert result["success"] and result["computed_dates"] == [DAYS[0], DAYS[2]]
    with closing(sqlite3.connect(sparse_db)) as conn:
        assert conn.execute("SELECT signal_id FROM timing_signals_daily").fetchall() == [
            ("middle-signal",)
        ]


def test_risk_same_signal_coexists_per_day_and_historical_rerun_keeps_latest(sparse_db):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        seed_signal(conn, DAYS[0])
        seed_signal(conn, DAYS[2], "future-signal")
        conn.execute(
            "UPDATE stock_daily_prices SET close=12,high=12.2 WHERE trade_date=?", (DAYS[2],)
        )
    calculator = RiskPlanCalculator(str(sparse_db))
    assert calculator.run_daily(target_dates=[DAYS[2]])["success"]
    latest_before = rows(sparse_db, "trade_plans")
    result = calculator.run_daily(target_dates=[DAYS[1]])

    assert result["success"] and result["computed_dates"] == [DAYS[1]]
    with closing(sqlite3.connect(sparse_db)) as conn:
        conn.row_factory = sqlite3.Row
        old = conn.execute("SELECT * FROM trade_plans WHERE trade_date=?", (DAYS[1],)).fetchall()
        latest_after = conn.execute(
            "SELECT * FROM trade_plans WHERE trade_date=? ORDER BY rowid", (DAYS[2],)
        ).fetchall()
    assert len(old) == 1 and old[0]["signal_id"] == "old-signal"
    assert old[0]["latest_close"] == 10.0
    assert [tuple(row) for row in latest_after] == latest_before
    assert old[0]["plan_id"] not in {row[0] for row in latest_before}
    assert calculator.run_daily(target_dates=[DAYS[1]])["success"]
    with closing(sqlite3.connect(sparse_db)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM trade_plans WHERE trade_date=?", (DAYS[1],)
        ).fetchone()[0] == 1
    assert rows(sparse_db, "portfolio_trades") == [("real-trade", 100)]


def test_risk_legacy_latest_identity_is_untouched_by_historical_write(sparse_db):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        seed_signal(conn, DAYS[0])
        seed_plan(conn, DAYS[2], plan_id="plan_old-signal")
    before = rows(sparse_db, "trade_plans")

    result = RiskPlanCalculator(str(sparse_db)).run_daily(target_dates=[DAYS[1]])

    assert result["success"]
    assert rows(sparse_db, "trade_plans")[0] == before[0]
    assert len(rows(sparse_db, "trade_plans")) == 2


@pytest.mark.parametrize("calculator_type,table", [
    (TimingSignalCalculator, "timing_signals_daily"),
    (RiskPlanCalculator, "trade_plans"),
])
def test_exact_zero_clears_only_requested_day_and_is_actually_computed(
    sparse_db, calculator_type, table
):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        if table == "timing_signals_daily":
            seed_signal(conn, DAYS[1], "target-signal")
            seed_signal(conn, DAYS[2], "future-signal")
            conn.execute("UPDATE stock_lifecycle_state SET state_rank=2 WHERE trade_date=?", (DAYS[1],))
        else:
            seed_plan(conn, DAYS[1], "target-plan")
            seed_plan(conn, DAYS[2], "future-plan")
    before = rows(sparse_db, table)[1]

    result = calculator_type(str(sparse_db)).run_daily(target_dates=[DAYS[1]], missing_only=True)

    assert result["success"] and result["records"] == 0
    assert result["computed_dates"] == [DAYS[1]]
    assert rows(sparse_db, table) == [before]


@pytest.mark.parametrize("calculator_type,task", [
    (TimingSignalCalculator, "timing_signals"), (RiskPlanCalculator, "risk_plans"),
])
def test_missing_only_uses_success_audit_including_zero_results(sparse_db, calculator_type, task):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        conn.execute(
            "INSERT INTO pipeline_task_log VALUES (?,'success',?)", (task + "@" + DAYS[1], DAYS[1])
        )
        conn.execute(
            "INSERT INTO pipeline_task_log VALUES (?,'failed',?)", (task + "@" + DAYS[0], DAYS[0])
        )
        conn.execute("UPDATE stock_lifecycle_state SET state_rank=2")

    result = calculator_type(str(sparse_db)).run_daily(
        target_dates=[DAYS[0], DAYS[1]], missing_only=True
    )

    assert result["success"]
    assert result["computed_dates"] == [DAYS[0]]
    assert result["skipped_dates"] == [DAYS[1]]


@pytest.mark.parametrize("calculator_type", [TimingSignalCalculator, RiskPlanCalculator])
@pytest.mark.parametrize("dates", [[], ["bad-date"], ["20261022"], ["2026-10-22suffix"],
                                   [(date.today() + timedelta(days=30)).isoformat()]])
def test_invalid_explicit_dates_fail_without_fallback(sparse_db, calculator_type, dates):
    result = calculator_type(str(sparse_db)).run_daily(target_dates=dates)
    assert result["success"] is False
    assert result.get("computed_dates", []) == []


@pytest.mark.parametrize("calculator_type,table", [
    (TimingSignalCalculator, "timing_signals_daily"), (RiskPlanCalculator, "trade_plans"),
])
def test_missing_source_date_fails_whole_request_without_latest_fallback(
    sparse_db, calculator_type, table
):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        seed_signal(conn, DAYS[2], "future-signal")
        seed_plan(conn, DAYS[2], "future-plan")
        conn.execute("DELETE FROM stock_daily_prices WHERE trade_date=?", (DAYS[1],))
    before = rows(sparse_db, table)

    result = calculator_type(str(sparse_db)).run_daily(target_dates=[DAYS[0], DAYS[1]])

    assert result["success"] is False
    assert rows(sparse_db, table) == before


@pytest.mark.parametrize("calculator_type", [TimingSignalCalculator, RiskPlanCalculator])
def test_missing_lifecycle_is_not_successful_zero(sparse_db, calculator_type):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        conn.execute("DELETE FROM stock_lifecycle_state WHERE trade_date=?", (DAYS[1],))
    result = calculator_type(str(sparse_db)).run_daily(target_dates=[DAYS[1]])
    assert result["success"] is False


@pytest.mark.parametrize("calculator_type,table", [
    (TimingSignalCalculator, "timing_signals_daily"), (RiskPlanCalculator, "trade_plans"),
])
def test_insert_failure_rolls_back_target_replacement(sparse_db, calculator_type, table):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        if table == "trade_plans":
            seed_signal(conn, DAYS[0])
            seed_plan(conn, DAYS[1])
        else:
            seed_signal(conn, DAYS[1])
        conn.execute(f"CREATE TRIGGER reject_insert BEFORE INSERT ON {table} "
                     "BEGIN SELECT RAISE(ABORT,'test insert failure'); END")
    before = rows(sparse_db, table)

    result = calculator_type(str(sparse_db)).run_daily(target_dates=[DAYS[1]])

    assert result["success"] is False
    assert rows(sparse_db, table) == before


def test_risk_never_issues_an_unscoped_delete(sparse_db, monkeypatch):
    statements = []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    result = RiskPlanCalculator(str(sparse_db)).run_daily()

    assert result["success"]
    deletes = [sql.upper() for sql in statements if sql.lstrip().upper().startswith("DELETE")]
    assert deletes and all("WHERE" in sql for sql in deletes)


@pytest.mark.parametrize("name,calculator_type,table", [
    ("timing", TimingSignalCalculator, "timing_signals_daily"),
    ("risk", RiskPlanCalculator, "trade_plans"),
])
def test_runner_cli_exact_zero_succeeds_only_after_actual_computation(
    sparse_db, monkeypatch, name, calculator_type, table
):
    runner = object.__new__(FeatureRunner)
    runner.calculators = {name: calculator_type(str(sparse_db))}
    results = []
    original_run = runner.run

    def capture(**kwargs):
        result = original_run(**kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(runner, "run", capture)
    monkeypatch.setattr(feature_runner, "FeatureRunner", lambda: runner)
    monkeypatch.setattr(sys, "argv", [
        "feature_runner", "--mode", "daily", "--calculators", name,
        "--target-dates", DAYS[1], "--missing-only",
    ])
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        conn.execute("UPDATE stock_lifecycle_state SET state_rank=2")

    assert feature_runner.main() == 0
    assert results[0][name]["computed_dates"] == [DAYS[1]]
    assert results[0][name]["records"] == 0
    assert rows(sparse_db, table) == []
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        conn.execute("DELETE FROM stock_daily_prices WHERE trade_date=?", (DAYS[1],))
    assert feature_runner.main() == 1
    assert results[1][name]["computed_dates"] == []


@pytest.mark.parametrize("calculator_type,table", [
    (TimingSignalCalculator, "timing_signals_daily"), (RiskPlanCalculator, "trade_plans"),
])
def test_timestamp_sources_and_unsorted_internal_targets(sparse_db, calculator_type, table):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        seed_signal(conn, DAYS[0])
        for source in ("stock_daily_prices", "stock_lifecycle_state"):
            conn.execute(
                f"UPDATE {source} SET trade_date=trade_date||' 00:00:00.000000' WHERE trade_date=?",
                (DAYS[1],),
            )
    result = calculator_type(str(sparse_db)).run_daily(target_dates=[DAYS[2], DAYS[1], DAYS[1]])

    assert result["success"]
    assert result["computed_dates"] == [DAYS[1], DAYS[2]]
    with closing(sqlite3.connect(sparse_db)) as conn:
        output_dates = {row[0] for row in conn.execute(f"SELECT trade_date FROM {table}")}
    assert output_dates.issubset(set(DAYS))
    if table == "trade_plans":
        assert output_dates == {DAYS[1], DAYS[2]}


@pytest.mark.parametrize("calculator_type", [TimingSignalCalculator, RiskPlanCalculator])
def test_source_connection_closed_after_success_and_validation_failure(
    sparse_db, calculator_type, monkeypatch
):
    connections = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def tracked_connect(*args, **kwargs):
        conn = connect(*args, factory=TrackedConnection, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    calculator = calculator_type(str(sparse_db))
    assert calculator.run_daily(target_dates=[DAYS[1]])["success"]
    assert not calculator.run_daily(target_dates=["invalid"])["success"]
    assert len(connections) == 2 and all(conn.closed for conn in connections)


def test_risk_missing_timing_table_is_not_successful_zero(sparse_db):
    with closing(sqlite3.connect(sparse_db)) as conn, conn:
        seed_plan(conn, DAYS[1])
        conn.execute("DROP TABLE timing_signals_daily")
    before = rows(sparse_db, "trade_plans")

    result = RiskPlanCalculator(str(sparse_db)).run_daily(target_dates=[DAYS[1]])

    assert result["success"] is False and result["computed_dates"] == []
    assert rows(sparse_db, "trade_plans") == before
