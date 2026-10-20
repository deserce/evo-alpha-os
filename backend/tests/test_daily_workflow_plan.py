import sqlite3
from datetime import datetime
from io import StringIO

import pytest

import scripts.run_daily_data_workflow as workflow
from scripts.run_daily_data_workflow import (
    Task,
    TaskResult,
    build_task_env,
    build_parser,
    collect_quality_checks,
    latest_completed_trading_date,
    make_tasks,
    print_quality_checks,
    run_task,
    run_tasks_until_blocked,
    stock_kline_freshness,
)


def task_names(argv: list[str]) -> list[str]:
    args = build_parser().parse_args(["--skip-kline", "--force-valuation", *argv])
    return [task.name for task in make_tasks(args)]


def test_skip_p0_does_not_skip_p1_tasks():
    names = task_names(["--skip-p0"])

    assert "strength_features" not in names
    assert "structure_health" not in names
    assert "lifecycle_state_base" not in names
    assert "lifecycle_state" not in names
    assert "timing_signals" in names
    assert "risk_plans" in names
    assert "research_snapshot" in names


def test_skip_p1_keeps_p0_tasks():
    names = task_names(["--skip-p1"])

    assert "strength_features" in names
    assert "structure_health" in names
    assert "lifecycle_state_base" in names
    assert "lifecycle_state" in names
    assert "timing_signals" not in names
    assert "risk_plans" not in names
    assert "research_snapshot" not in names


def test_watch_engine_runs_after_final_lifecycle_with_immediate_dispatch():
    names = task_names([])

    assert names.index("lifecycle_state") < names.index("watch_engine")
    assert names.index("watch_engine") < names.index("watch_email_immediate")
    assert names.index("watch_email_immediate") < names.index("watch_email_digest")

    args = build_parser().parse_args(["--skip-kline", "--force-valuation"])
    digest = next(task for task in make_tasks(args) if task.name == "watch_email_digest")
    assert digest.command[-5:] == (
        "--mode",
        "digest",
        "--trade-date",
        workflow.latest_completed_trading_date(),
        "--require-trusted",
    )


def test_skip_watch_removes_engine_and_dispatcher():
    names = task_names(["--skip-watch"])

    assert "watch_engine" not in names
    assert "watch_email_immediate" not in names
    assert "watch_email_digest" not in names


def test_etf_retry_requires_all_configured_symbols_on_exact_target(tmp_path, monkeypatch):
    database = tmp_path / "etf.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE etf_info(symbol TEXT);
            CREATE TABLE etf_daily_prices(symbol TEXT, trade_date TEXT);
            INSERT INTO etf_info VALUES ('510300'), ('159915');
            INSERT INTO etf_daily_prices VALUES
                ('510300', '2026-09-15'), ('159915', '2026-09-16');
        """)
    monkeypatch.setattr(workflow, "DB_PATH", database)
    for mode in ("retry", "morning"):
        assert workflow.retry_task_skip_reason("etf_daily_prices", "2026-09-15", mode=mode) is None
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO etf_daily_prices VALUES ('159915', '2026-09-15')")
    for mode in ("retry", "morning"):
        reason = workflow.retry_task_skip_reason("etf_daily_prices", "2026-09-15", mode=mode)
        assert reason and "ETF coverage 2/2" in reason


def test_etf_retry_extra_or_duplicate_rows_cannot_cover_missing_symbol(tmp_path, monkeypatch):
    database = tmp_path / "etf.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE etf_info(symbol TEXT);
            CREATE TABLE etf_daily_prices(symbol TEXT, trade_date TEXT);
            INSERT INTO etf_info VALUES ('510300'), ('159915');
            INSERT INTO etf_daily_prices VALUES
                ('510300', '2026-09-15'), ('510300', '2026-09-15'),
                ('999999', '2026-09-15');
        """)
    monkeypatch.setattr(workflow, "DB_PATH", database)
    assert workflow.retry_task_skip_reason("etf_daily_prices", "2026-09-15") is None


def test_etf_retry_unknown_universe_must_not_skip(tmp_path, monkeypatch):
    database = tmp_path / "etf.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE etf_daily_prices(symbol TEXT, trade_date TEXT);
            INSERT INTO etf_daily_prices VALUES ('510300', '2026-09-15');
        """)
    monkeypatch.setattr(workflow, "DB_PATH", database)
    assert workflow.retry_task_skip_reason("etf_daily_prices", "2026-09-15") is None
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE etf_info(symbol TEXT)")
    assert workflow.retry_task_skip_reason("etf_daily_prices", "2026-09-15") is None


def test_etf_rps_retry_covers_source_symbols_and_pending_collection(tmp_path, monkeypatch):
    database = tmp_path / "etf_rps.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE etf_info(symbol TEXT);
            CREATE TABLE etf_daily_prices(symbol TEXT, trade_date TEXT);
            CREATE TABLE quant_feature_etf_rps(symbol TEXT, trade_date TEXT);
            INSERT INTO etf_info VALUES ('510300'), ('159915');
            INSERT INTO etf_daily_prices VALUES ('510300', '2026-09-15');
            INSERT INTO quant_feature_etf_rps VALUES ('510300', '2026-09-15');
        """)
    monkeypatch.setattr(workflow, "DB_PATH", database)
    assert workflow.retry_task_skip_reason("etf_rps_features", "2026-09-15") is None
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO etf_daily_prices VALUES ('159915', '2026-09-15')")
    assert workflow.retry_task_skip_reason("etf_rps_features", "2026-09-15") is None
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO quant_feature_etf_rps VALUES ('159915', '2026-09-15')")
    assert workflow.retry_task_skip_reason("etf_rps_features", "2026-09-15")


def test_etf_rps_recalculation_does_not_reuse_partial_dense_peak():
    args = build_parser().parse_args(["--skip-kline", "--force-valuation"])
    task = next(task for task in make_tasks(args) if task.name == "etf_rps_features")
    assert "--missing-only" not in task.command
    assert "--target-dates" in task.command


def test_skip_notifications_keeps_computation_without_dispatchers():
    args = build_parser().parse_args(["--skip-kline", "--skip-notifications"])
    tasks = make_tasks(args)
    names = {task.name for task in tasks}
    assert {"watch_engine", "portfolio_daily_snapshots", "portfolio_exit_engine"} <= names
    assert all("quant_engine.watch.dispatcher" not in task.command for task in tasks)


def test_task_environment_removes_inherited_proxies(monkeypatch):
    from data_job.common.network_utils import PROXY_ENV_KEYS

    for key in PROXY_ENV_KEYS:
        monkeypatch.setenv(key, "http://127.0.0.1:7897")
    env = build_task_env()
    assert not set(PROXY_ENV_KEYS) & env.keys()
    assert env["NO_PROXY"] == env["no_proxy"] == "*"


def test_recent_source_dates_close_connection(tmp_path, monkeypatch):
    database = tmp_path / "dates.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE stock_daily_prices(trade_date TEXT)")
    connection.execute("INSERT INTO stock_daily_prices VALUES ('2026-09-14')")
    connection.commit()
    monkeypatch.setattr(workflow, "DB_PATH", database)
    monkeypatch.setattr(workflow.sqlite3, "connect", lambda *args, **kwargs: connection)
    assert workflow.recent_source_trading_dates("2026-09-15") == ["2026-09-14", "2026-09-15"]
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("Source-date connection was not closed")


def test_historical_quality_scopes_turnover_and_factors_to_requested_date(tmp_path, monkeypatch):
    database = tmp_path / "historical.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE stock_daily_prices(symbol TEXT, trade_date TEXT, turnover_rate REAL);
            CREATE TABLE quant_feature_rps(symbol TEXT, trade_date TEXT);
            INSERT INTO stock_daily_prices VALUES ('000001', '2026-09-14', 1.0);
            INSERT INTO stock_daily_prices VALUES ('000001', '2026-09-15', NULL);
            INSERT INTO quant_feature_rps VALUES ('000001', '2026-09-14');
        """)
    monkeypatch.setattr(workflow, "DB_PATH", database)
    tasks = [Task("rps_features", (), critical=True)]
    checks = {c.name: c for c in collect_quality_checks(tasks, "2026-09-14", persist_missing=False)}
    assert not checks["stock_kline_latest_turnover"].failed
    assert not checks["stock_kline_turnover_recent_window"].failed
    assert not checks["quant_feature_rps_freshness"].failed
    checks = {c.name: c for c in collect_quality_checks(tasks, "2026-09-15", persist_missing=False)}
    assert checks["stock_kline_latest_turnover"].failed
    assert checks["quant_feature_rps_freshness"].failed
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE quant_feature_rps SET trade_date='2026-09-15'")
    checks = {c.name: c for c in collect_quality_checks(tasks, "2026-09-14", persist_missing=False)}
    assert checks["quant_feature_rps_freshness"].failed


def test_portfolio_snapshots_use_target_date_and_are_best_effort():
    target = "2026-07-24"
    args = build_parser().parse_args(
        [
            "--kline-end",
            target,
            "--force-kline",
            "--force-valuation",
            "--include-strategy",
        ]
    )
    tasks = make_tasks(args)
    names = [task.name for task in tasks]
    portfolio = next(
        task for task in tasks if task.name == "portfolio_daily_snapshots"
    )
    exit_engine = next(
        task for task in tasks if task.name == "portfolio_exit_engine"
    )
    exit_digest = next(
        task for task in tasks if task.name == "portfolio_exit_digest"
    )

    assert portfolio.command == (
        workflow.sys.executable,
        "scripts/build_portfolio_snapshots.py",
        "--trade-date",
        target,
    )
    assert not portfolio.critical
    assert portfolio.timeout_seconds == 60
    assert exit_engine.command == (
        workflow.sys.executable,
        "scripts/run_portfolio_exit_engine.py",
        "--trade-date",
        target,
    )
    assert not exit_engine.critical
    assert exit_engine.timeout_seconds == 60
    assert exit_digest.command == (
        workflow.sys.executable,
        "-m",
        "quant_engine.watch.dispatcher",
        "--mode",
        "portfolio-digest",
        "--trade-date",
        target,
    )
    assert not exit_digest.critical
    assert exit_digest.timeout_seconds == 60
    assert names.index("mootdx_qfq_stock_kline") < names.index(
        "portfolio_daily_snapshots"
    )
    assert names.index("mrgc_preselect") < names.index(
        "portfolio_daily_snapshots"
    )
    assert names.index("watch_engine") < names.index(
        "portfolio_daily_snapshots"
    )
    assert names.index("portfolio_daily_snapshots") < names.index(
        "portfolio_exit_engine"
    )
    assert names.index("portfolio_exit_engine") < names.index(
        "portfolio_exit_digest"
    )
    assert names.index("portfolio_exit_digest") < names.index(
        "research_snapshot"
    )


def test_morning_and_skip_portfolio_exclude_daily_snapshots():
    morning_names = task_names(["--morning"])
    skipped_names = task_names(["--skip-portfolio"])

    assert "portfolio_daily_snapshots" not in morning_names
    assert "portfolio_daily_snapshots" not in skipped_names
    assert "portfolio_exit_engine" not in morning_names
    assert "portfolio_exit_engine" not in skipped_names
    assert "portfolio_exit_digest" not in morning_names
    assert "portfolio_exit_digest" not in skipped_names


def test_retry_keeps_portfolio_snapshot_task_for_idempotent_rebuild():
    args = build_parser().parse_args(
        ["--skip-kline", "--force-valuation", "--retry"]
    )
    tasks = make_tasks(args)
    names = [task.name for task in tasks]

    assert "portfolio_daily_snapshots" in names
    assert "portfolio_exit_engine" in names
    assert "portfolio_exit_digest" in names
    assert next(
        task for task in tasks if task.name == "portfolio_exit_engine"
    ).skip_reason is None
    assert next(
        task for task in tasks if task.name == "portfolio_exit_digest"
    ).skip_reason is None


def test_latest_completed_trading_date_uses_previous_day_before_close():
    assert latest_completed_trading_date(datetime(2026, 5, 8, 8, 50)) == "2026-05-07"
    assert latest_completed_trading_date(datetime(2026, 5, 8, 16, 0)) == "2026-05-08"
    assert latest_completed_trading_date(datetime(2026, 5, 11, 9, 0)) == "2026-05-08"


def test_latest_completed_trading_date_skips_exchange_holidays():
    assert latest_completed_trading_date(datetime(2026, 9, 25, 16, 0)) == "2026-09-24"
    assert latest_completed_trading_date(datetime(2026, 9, 27, 16, 0)) == "2026-09-24"
    assert latest_completed_trading_date(datetime(2026, 9, 28, 9, 0)) == "2026-09-24"
    assert latest_completed_trading_date(datetime(2026, 9, 28, 16, 0)) == "2026-09-28"
    assert latest_completed_trading_date(datetime(2026, 10, 7, 16, 0)) == "2026-09-30"
    assert latest_completed_trading_date(datetime(2026, 10, 8, 16, 0)) == "2026-10-08"


def test_workflow_rejects_explicit_exchange_holiday_before_database_write(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow.sys, "argv", ["run_daily_data_workflow.py", "--kline-end", "2026-09-25"])
    monkeypatch.setattr(workflow, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(
        workflow,
        "ensure_data_core_schema",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not write database")),
    )

    assert workflow.main() == 2
    assert not (tmp_path / "logs").exists()


def test_fresh_kline_is_local_skip_by_default(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL
            );
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-07', 1.3),
                ('000002', '2026-05-07', 2.1);
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    args = build_parser().parse_args(["--kline-end", "2026-05-07"])
    tasks = make_tasks(args)
    kline_task = next(task for task in tasks if task.name == "mootdx_qfq_stock_kline")

    assert kline_task.name == "mootdx_qfq_stock_kline"
    assert kline_task.command == ()
    assert kline_task.skip_reason and "local K-line fresh" in kline_task.skip_reason


@pytest.mark.parametrize(
    ("prices", "expected_fresh", "reason_fragment"),
    [
        ([('000001', '2026-09-30')], False, "SH=0/1"),
        (
            [('000001', '2026-10-09'), ('600000', '2026-10-09'),
             ('920001', '2026-10-09')],
            False,
            "SH=0/1",
        ),
        (
            [('000001', '2026-09-30'), ('600000', '2026-09-30'),
             ('920001', '2026-09-30')],
            True,
            "SH=1/1",
        ),
        (
            [('000001', '2026-09-30'), ('600000', '2026-09-30')],
            False,
            "BSE=0/1",
        ),
    ],
)
def test_kline_freshness_requires_exact_date_market_coverage(
    tmp_path, monkeypatch, prices, expected_fresh, reason_fragment,
):
    db_path = tmp_path / "coverage.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE stock_info(symbol TEXT, list_date TEXT);
            CREATE TABLE stock_daily_prices(
                symbol TEXT, trade_date TEXT, turnover_rate REAL
            );
            INSERT INTO stock_info VALUES
                ('000001', '1991-04-03'), ('600000', '1999-11-10'),
                ('920001', '2020-01-01'), ('601001', '2026-10-09');
        """)
        conn.executemany(
            "INSERT INTO stock_daily_prices VALUES (?, ?, 1.0)", prices,
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    is_fresh, reason = stock_kline_freshness("2026-09-30")

    assert is_fresh is expected_fresh
    assert reason_fragment in reason
    retry_skip = workflow.retry_task_skip_reason("mootdx_qfq_stock_kline", "2026-09-30")
    assert bool(retry_skip) is expected_fresh


def test_kline_freshness_keeps_accepted_no_raw_data_exemptions(tmp_path, monkeypatch):
    db_path = tmp_path / "accepted_gaps.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE stock_info(symbol TEXT, list_date TEXT);
            CREATE TABLE stock_daily_prices(
                symbol TEXT, trade_date TEXT, turnover_rate REAL
            );
            CREATE TABLE stock_daily_gap_exception(
                symbol TEXT, trade_date TEXT, status TEXT
            );
            INSERT INTO stock_info VALUES
                ('600000', '1999-11-10'), ('600001', '2000-01-01');
            INSERT INTO stock_daily_prices VALUES ('600000', '2026-09-30', 1.0);
            INSERT INTO stock_daily_gap_exception VALUES
                ('600001', '2026-09-30', 'accepted_no_raw_data');
        """)
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    is_fresh, reason = stock_kline_freshness("2026-09-30")

    assert is_fresh
    assert "SH=1/2" in reason


def test_fresh_kline_exempts_bse_unadjusted_turnover(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL,
                market TEXT,
                source TEXT,
                adjustment_mode TEXT
            );
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-06-02', 1.3, 'SZ', 'tdx_mootdx_qfq', 'qfq'),
                ('920002', '2026-06-02', NULL, 'BSE', 'tdx_standard_bj', 'none');
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    is_fresh, reason = stock_kline_freshness("2026-06-02")

    assert is_fresh
    assert "bse_unadjusted_exempt=1" in reason


def test_fresh_kline_exempts_suspended_zero_turnover(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL,
                market TEXT,
                source TEXT,
                adjustment_mode TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL
            );
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-07-29', 1.3, 'SZ', 'tdx_mootdx_qfq', 'qfq',
                 10.0, 10.5, 9.8, 10.2, 100000, 1020000),
                ('000820', '2026-07-29', 0, 'SZ', 'tdx_mootdx_qfq', 'qfq',
                 2.91, 2.91, 2.91, 2.91, 0, 0);
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    is_fresh, reason = stock_kline_freshness("2026-07-29")

    assert is_fresh
    assert "suspended_turnover_exempt=1" in reason


def test_fresh_kline_rejects_zero_turnover_when_stock_traded(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL,
                market TEXT,
                source TEXT,
                adjustment_mode TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL
            );
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-07-29', 0, 'SZ', 'tdx_mootdx_qfq', 'qfq',
                 10.0, 10.5, 9.8, 10.2, 100000, 1020000);
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    is_fresh, reason = stock_kline_freshness("2026-07-29")

    assert not is_fresh
    assert "zero_turnover=1" in reason
    assert "suspended_turnover_exempt=0" in reason


def test_force_kline_keeps_downloader_even_when_fresh(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL
            );
            INSERT INTO stock_daily_prices VALUES ('000001', '2026-05-07', 1.3);
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    args = build_parser().parse_args(["--kline-end", "2026-05-07", "--force-kline"])
    tasks = make_tasks(args)
    kline_task = next(task for task in tasks if task.name == "mootdx_qfq_stock_kline")

    assert kline_task.command
    assert kline_task.skip_reason is None
    assert "--max-bars" in kline_task.command
    assert kline_task.command[kline_task.command.index("--max-bars") + 1] == "40"
    assert "--submit-window" in kline_task.command
    assert kline_task.command[kline_task.command.index("--submit-window") + 1] == "64"
    assert "--market" in kline_task.command
    assert kline_task.command[kline_task.command.index("--market") + 1] == "ALL"
    assert "--force-window" not in kline_task.command


def test_gap_repair_runs_between_targeted_repair_and_final_quality():
    names = task_names([])

    assert "stock_daily_gap_repair" in names
    assert names.index("stock_daily_quality_repair") < names.index("stock_daily_gap_repair")
    assert names.index("stock_daily_gap_repair") < names.index("data_core_stock_daily_quality")


def test_skip_gap_repair_removes_rolling_gap_task():
    names = task_names(["--skip-gap-repair"])

    assert "stock_daily_gap_repair" not in names


def test_stock_collection_is_the_only_tdx_gate():
    args = build_parser().parse_args(["--kline-end", "2026-06-02", "--force-kline"])
    tasks = make_tasks(args)
    names = [task.name for task in tasks]

    assert names[0] == "mootdx_qfq_stock_kline"
    assert "tdx_source_capability" not in names


def test_morning_is_bounded_and_excludes_slow_expansion_tasks():
    args = build_parser().parse_args(
        [
            "--morning",
            "--pending-days",
            "7",
            "--time-budget-minutes",
            "25",
            "--kline-end",
            "2026-07-14",
        ]
    )
    tasks = make_tasks(args)
    names = [task.name for task in tasks]

    assert "stock_daily_gap_repair" in names
    gap_task = next(task for task in tasks if task.name == "stock_daily_gap_repair")
    assert gap_task.command[gap_task.command.index("--window-trading-days") + 1] == "7"
    assert "index_daily" in names
    assert "sector_daily_prices" in names
    assert "etf_daily_prices" in names
    assert "news" not in names
    assert "macro_indicators" not in names
    assert "stock_enrichment_core" not in names
    assert "research_snapshot" not in names
    assert "watch_engine" not in names


def test_market_side_tasks_have_timeouts():
    args = build_parser().parse_args(["--skip-kline", "--force-valuation"])
    tasks = {task.name: task for task in make_tasks(args)}

    assert tasks["index_daily"].timeout_seconds == 180
    assert tasks["sector_daily_prices"].timeout_seconds == 300
    assert tasks["etf_daily_prices"].timeout_seconds == 240
    assert tasks["limit_boards"].timeout_seconds == 180
    assert tasks["news"].timeout_seconds == 180
    assert tasks["macro_indicators"].timeout_seconds == 180


def test_sector_kline_repair_runs_between_sector_collection_and_rps():
    names = task_names([])

    assert "sector_kline_continuity_repair" in names
    assert names.index("sector_daily_prices") < names.index("sector_kline_continuity_repair")
    assert names.index("sector_kline_continuity_repair") < names.index("rps_features")


def test_skip_sector_kline_repair_removes_continuity_task():
    names = task_names(["--skip-sector-kline-repair"])

    assert "sector_kline_continuity_repair" not in names


def test_sector_kline_continuity_stats_detects_jump(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sector_list (
                sector_name TEXT PRIMARY KEY,
                sector_type TEXT
            );
            CREATE TABLE sector_daily_prices (
                sector_name TEXT,
                trade_date TEXT,
                open REAL,
                close REAL,
                high REAL,
                low REAL,
                pct_chg REAL
            );
            INSERT INTO sector_list VALUES ('建筑材料', 'Industry');
            INSERT INTO sector_daily_prices VALUES
                ('建筑材料', '2026-04-29', 1000, 1000, 1000, 1000, 0),
                ('建筑材料', '2026-04-30', 4000, 4000, 4000, 4000, 0);
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    with sqlite3.connect(db_path) as conn:
        stats = workflow.sector_kline_continuity_stats(conn)

    assert stats is not None
    assert stats["rows"] == 2
    assert stats["sectors"] == 1
    assert stats["huge_jump_count"] == 1
    assert stats["pct_mismatch_count"] == 1


def test_rps_tasks_split_etf_as_best_effort():
    args = build_parser().parse_args(["--skip-kline", "--force-valuation"])
    tasks = {task.name: task for task in make_tasks(args)}

    assert tasks["rps_features"].critical
    assert "stock" in tasks["rps_features"].command
    assert "sector" not in tasks["rps_features"].command
    assert "etf" not in tasks["rps_features"].command
    assert not tasks["sector_rps_legacy_shadow"].critical
    assert "sector" in tasks["sector_rps_legacy_shadow"].command
    assert not tasks["etf_rps_features"].critical
    assert "etf" in tasks["etf_rps_features"].command


def test_run_task_returns_failure_on_timeout():
    task = Task(
        "slow_best_effort",
        (
            workflow.sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(5)",
        ),
        timeout_seconds=1,
    )

    result = run_task(task, StringIO())

    assert result.failed
    assert result.returncode != 0


def test_valuation_runs_when_kline_will_update(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL
            );
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                turnover REAL,
                total_mv REAL,
                circ_mv REAL
            );
            INSERT INTO stock_daily_prices VALUES ('000001', '2026-05-25', 1.3);
            INSERT INTO stock_valuation_daily VALUES
                ('000001', '2026-05-25', 1.3, 1000000000, 900000000);
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    args = build_parser().parse_args(["--kline-end", "2026-05-28", "--force-kline"])
    tasks = make_tasks(args)
    valuation_task = next(task for task in tasks if task.name == "stock_valuation")

    assert valuation_task.command == (
        workflow.sys.executable,
        "-m",
        "data_job.collectors.stock_valuation_collector",
        "--target-date",
        "2026-05-28",
    )
    assert valuation_task.skip_reason is None


def test_morning_calculation_dates_include_target_missing_from_source(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT, turnover_rate REAL)"
        )
        conn.executemany(
            "INSERT INTO stock_daily_prices VALUES ('000001', ?, 1.0)",
            [
                ("2026-07-03",),
                ("2026-07-06",),
                ("2026-07-07",),
                ("2026-07-08",),
                ("2026-07-09",),
                ("2026-07-10",),
                ("2026-07-13",),
            ],
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    args = build_parser().parse_args(
        ["--morning", "--kline-end", "2026-07-14", "--pending-days", "7"]
    )
    task = next(item for item in make_tasks(args) if item.name == "rps_features")
    target_index = task.command.index("--target-dates")

    assert "--missing-only" in task.command
    assert task.command[target_index + 1 :] == (
        "2026-07-06",
        "2026-07-07",
        "2026-07-08",
        "2026-07-09",
        "2026-07-10",
        "2026-07-13",
        "2026-07-14",
    )


def test_retry_rechecks_recent_internal_rps_and_mrgc_gaps(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    dates = (
        "2026-07-09",
        "2026-07-10",
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, turnover_rate REAL
            );
            CREATE TABLE quant_feature_rps (symbol TEXT, trade_date TEXT);
            CREATE TABLE quant_feature_sector_rps (sector_name TEXT, trade_date TEXT);
            CREATE TABLE market_strength_daily (index_code TEXT, trade_date TEXT);
            CREATE TABLE industry_strength_daily (sector_name TEXT, trade_date TEXT);
            CREATE TABLE structure_health_daily (symbol TEXT, trade_date TEXT);
            CREATE TABLE stock_lifecycle_state (symbol TEXT, trade_date TEXT);
            CREATE TABLE strategy_run_log (
                strategy_name TEXT, trade_date TEXT, status TEXT, finished_at TEXT
            );
            """
        )
        for trade_date in dates:
            conn.execute(
                "INSERT INTO stock_daily_prices VALUES ('000001', ?, 1.0)",
                (trade_date,),
            )
            if trade_date != "2026-07-16":
                conn.execute(
                    "INSERT INTO quant_feature_rps VALUES ('000001', ?)",
                    (trade_date,),
                )
                conn.execute(
                    "INSERT INTO quant_feature_sector_rps VALUES ('银行', ?)",
                    (trade_date,),
                )
                conn.execute(
                    "INSERT INTO strategy_run_log VALUES ('mrgc_v1', ?, 'success', ?)",
                    (trade_date, f"{trade_date} 16:00:00"),
                )
                conn.execute(
                    "INSERT INTO market_strength_daily VALUES ('ALL', ?)",
                    (trade_date,),
                )
                conn.execute(
                    "INSERT INTO industry_strength_daily VALUES ('银行', ?)",
                    (trade_date,),
                )
                conn.execute(
                    "INSERT INTO structure_health_daily VALUES ('000001', ?)",
                    (trade_date,),
                )
                conn.execute(
                    "INSERT INTO stock_lifecycle_state VALUES ('000001', ?)",
                    (trade_date,),
                )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    args = build_parser().parse_args(
        ["--retry", "--kline-end", "2026-07-17", "--skip-market-side", "--include-strategy"]
    )
    tasks = {task.name: task for task in make_tasks(args)}

    rps_task = tasks["rps_features"]
    target_index = rps_task.command.index("--target-dates")
    assert rps_task.skip_reason is None
    assert "--missing-only" in rps_task.command
    assert rps_task.command[target_index + 1 :] == dates

    mrgc_task = tasks["mrgc_preselect"]
    dates_index = mrgc_task.command.index("--dates")
    assert mrgc_task.skip_reason is None
    assert mrgc_task.command[dates_index + 1 : -1] == dates
    assert mrgc_task.command[-1] == "--missing-only"

    for task_name in (
        "strength_features",
        "structure_health",
        "lifecycle_state_base",
        "lifecycle_state",
    ):
        task = tasks[task_name]
        target_index = task.command.index("--target-dates")
        assert task.skip_reason is None
        assert task.command[target_index + 1 :] == dates


def test_daily_rechecks_recent_internal_rps_and_mrgc_gaps(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    dates = (
        "2026-07-21",
        "2026-07-22",
        "2026-07-23",
        "2026-07-24",
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT, turnover_rate REAL)"
        )
        conn.executemany(
            "INSERT INTO stock_daily_prices VALUES ('000001', ?, 1.0)",
            [(trade_date,) for trade_date in dates],
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    args = build_parser().parse_args(
        ["--kline-end", "2026-07-29", "--skip-market-side", "--include-strategy"]
    )
    tasks = {task.name: task for task in make_tasks(args)}

    rps_task = tasks["rps_features"]
    target_index = rps_task.command.index("--target-dates")
    assert "--missing-only" in rps_task.command
    assert rps_task.command[target_index + 1 :] == dates

    mrgc_task = tasks["mrgc_preselect"]
    dates_index = mrgc_task.command.index("--dates")
    assert mrgc_task.command[dates_index + 1 : -1] == dates
    assert mrgc_task.command[-1] == "--missing-only"


def test_retry_skips_noncritical_enrichment_cache():
    args = build_parser().parse_args(
        ["--retry", "--kline-end", "2026-07-29", "--include-strategy"]
    )
    enrichment = next(
        task for task in make_tasks(args) if task.name == "stock_enrichment_core"
    )

    assert enrichment.skip_reason == (
        "retry target already fresh: non-target enrichment skipped 2026-07-29"
    )


def test_workflow_mode_distinguishes_retry_from_daily():
    parser = build_parser()

    assert workflow.resolve_workflow_mode(parser.parse_args([])) == "daily"
    assert workflow.resolve_workflow_mode(parser.parse_args(["--retry"])) == "retry"
    assert workflow.resolve_workflow_mode(parser.parse_args(["--morning"])) == "morning"
    assert workflow.resolve_workflow_mode(parser.parse_args(["--check-only"])) == "check-only"


def test_fresh_valuation_is_local_skip_by_default(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL
            );
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                turnover REAL,
                total_mv REAL,
                circ_mv REAL
            );
            INSERT INTO stock_daily_prices VALUES ('000001', '2026-05-07', 1.3);
            INSERT INTO stock_valuation_daily VALUES
                ('000001', '2026-05-07', 1.3, 1000000000, 900000000);
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    args = build_parser().parse_args(["--kline-end", "2026-05-07"])
    tasks = make_tasks(args)
    valuation_task = next(task for task in tasks if task.name == "stock_valuation")

    assert valuation_task.command == ()
    assert not valuation_task.critical
    assert valuation_task.skip_reason and "local valuation fresh" in valuation_task.skip_reason


def test_retry_skips_fresh_core_tasks_and_non_target_enrichment(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    target = "2026-07-14"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT, turnover_rate REAL);
            CREATE TABLE stock_valuation_daily (
                code TEXT, trade_date TEXT, turnover REAL, total_mv REAL, circ_mv REAL
            );
            CREATE TABLE quant_feature_rps (symbol TEXT, trade_date TEXT);
            CREATE TABLE quant_feature_sector_rps (sector_name TEXT, trade_date TEXT);
            CREATE TABLE market_strength_daily (index_code TEXT, trade_date TEXT);
            CREATE TABLE industry_strength_daily (sector_name TEXT, trade_date TEXT);
            CREATE TABLE structure_health_daily (symbol TEXT, trade_date TEXT);
            CREATE TABLE stock_lifecycle_state (symbol TEXT, trade_date TEXT);
            CREATE TABLE timing_signals_daily (symbol TEXT, trade_date TEXT);
            CREATE TABLE trade_plans (signal_id TEXT, trade_date TEXT);
            CREATE TABLE research_stock_daily_snapshot (symbol TEXT, trade_date TEXT);
            CREATE TABLE watch_daily_snapshot (symbol TEXT, trade_date TEXT);
            CREATE TABLE quant_stock_pool (symbol TEXT, add_date TEXT);
            CREATE TABLE strategy_run_log (
                strategy_name TEXT, trade_date TEXT, status TEXT, finished_at TEXT
            );
            CREATE TABLE data_quality_report (
                trade_date TEXT, module TEXT, market TEXT, status TEXT, created_at TEXT
            );
            INSERT INTO stock_daily_prices VALUES ('000001', '{target}', 1.2);
            INSERT INTO stock_valuation_daily VALUES ('000001', '{target}', 1.2, 100, 90);
            INSERT INTO quant_feature_rps VALUES ('000001', '{target}');
            INSERT INTO quant_feature_sector_rps VALUES ('银行', '{target}');
            INSERT INTO market_strength_daily VALUES ('ALL', '{target}');
            INSERT INTO industry_strength_daily VALUES ('银行', '{target}');
            INSERT INTO structure_health_daily VALUES ('000001', '{target}');
            INSERT INTO stock_lifecycle_state VALUES ('000001', '{target}');
            INSERT INTO timing_signals_daily VALUES ('000001', '{target}');
            INSERT INTO trade_plans VALUES ('s1', '{target}');
            INSERT INTO research_stock_daily_snapshot VALUES ('000001', '{target}');
            INSERT INTO watch_daily_snapshot VALUES ('000001', '{target}');
            INSERT INTO quant_stock_pool VALUES ('000001', '{target}');
            INSERT INTO strategy_run_log VALUES ('mrgc_v1', '{target}', 'success', '{target} 16:00:00');
            INSERT INTO data_quality_report VALUES
                ('{target}', 'stock_daily', 'SH', 'PASS', '{target} 16:00:00'),
                ('{target}', 'stock_daily', 'SZ', 'PASS', '{target} 16:00:00'),
                ('{target}', 'stock_daily', 'BSE', 'PASS', '{target} 16:00:00');
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)
    args = build_parser().parse_args(
        ["--retry", "--kline-end", target, "--skip-market-side", "--include-strategy"]
    )
    tasks = {task.name: task for task in make_tasks(args)}

    for name in (
        "mootdx_qfq_stock_kline",
        "data_core_stock_daily_quality_preview",
        "stock_daily_quality_repair",
        "data_core_stock_daily_quality",
        "rps_features",
        "strength_features",
        "structure_health",
        "lifecycle_state",
        "mrgc_preselect",
    ):
        assert tasks[name].skip_reason and "retry target already fresh" in tasks[name].skip_reason
    assert tasks["stock_enrichment_core"].skip_reason == (
        f"retry target already fresh: non-target enrichment skipped {target}"
    )
    assert tasks["stock_enrichment_core"].timeout_seconds == 600

    morning_args = build_parser().parse_args(
        ["--morning", "--kline-end", target, "--skip-market-side", "--include-strategy"]
    )
    morning_tasks = {task.name: task for task in make_tasks(morning_args)}
    for name in (
        "data_core_stock_daily_quality_preview",
        "stock_daily_quality_repair",
        "data_core_stock_daily_quality",
        "strength_features",
        "structure_health",
        "lifecycle_state",
        "mrgc_preselect",
    ):
        assert morning_tasks[name].skip_reason and "morning target already fresh" in (
            morning_tasks[name].skip_reason
        )
    assert "local K-line fresh" in morning_tasks["mootdx_qfq_stock_kline"].skip_reason
    assert morning_tasks["rps_features"].skip_reason is None
    assert "--missing-only" in morning_tasks["rps_features"].command


def test_retry_does_not_skip_partial_industry_strength(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    previous = "2026-07-03"
    target = "2026-07-06"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT);
            CREATE TABLE market_strength_daily (index_code TEXT, trade_date TEXT);
            CREATE TABLE industry_strength_daily (sector_name TEXT, trade_date TEXT);
            """
        )
        conn.execute("INSERT INTO stock_daily_prices VALUES ('000001', ?)", (target,))
        conn.executemany(
            "INSERT INTO market_strength_daily VALUES (?, ?)",
            [(f"index-{index}", target) for index in range(4)],
        )
        conn.executemany(
            "INSERT INTO industry_strength_daily VALUES (?, ?)",
            [(f"sector-{index}", previous) for index in range(100)],
        )
        conn.executemany(
            "INSERT INTO industry_strength_daily VALUES (?, ?)",
            [(f"sector-{index}", target) for index in range(20)],
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    skip_reason = workflow.retry_task_skip_reason("strength_features", target)

    assert skip_reason is None


def test_retry_does_not_skip_watch_engine_when_snapshot_misses_watchlist_member(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "local_quant.db"
    target = "2026-07-17"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE stock_pools (
                pool_id TEXT PRIMARY KEY,
                monitor_enabled INTEGER
            );
            CREATE TABLE stock_pool_members (
                pool_id TEXT,
                symbol TEXT
            );
            CREATE TABLE watch_daily_snapshot (
                symbol TEXT,
                trade_date TEXT
            );
            INSERT INTO stock_pools VALUES ('watchlist', 1);
            INSERT INTO stock_pool_members VALUES ('watchlist', '000001');
            INSERT INTO stock_pool_members VALUES ('watchlist', '000002');
            INSERT INTO watch_daily_snapshot VALUES ('000001', '{target}');
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    skip_reason = workflow.retry_task_skip_reason("watch_engine", target)

    assert skip_reason is None


def test_retry_skips_watch_engine_when_snapshot_covers_watchlist(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    target = "2026-07-17"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE stock_pools (
                pool_id TEXT PRIMARY KEY,
                monitor_enabled INTEGER
            );
            CREATE TABLE stock_pool_members (
                pool_id TEXT,
                symbol TEXT
            );
            CREATE TABLE watch_daily_snapshot (
                symbol TEXT,
                trade_date TEXT
            );
            INSERT INTO stock_pools VALUES ('watchlist', 1);
            INSERT INTO stock_pool_members VALUES ('watchlist', '000001');
            INSERT INTO stock_pool_members VALUES ('watchlist', '000002');
            INSERT INTO watch_daily_snapshot VALUES ('000001', '{target}');
            INSERT INTO watch_daily_snapshot VALUES ('000002', '{target}');
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    skip_reason = workflow.retry_task_skip_reason("watch_engine", target)

    assert skip_reason and "watch_engine coverage 2026-07-17 2/2" in skip_reason


def test_retry_does_not_skip_lifecycle_when_new_group_member_is_missing(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "local_quant.db"
    target = "2026-07-17"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE quant_stock_pool (
                symbol TEXT, pool_name TEXT, is_active INTEGER
            );
            CREATE TABLE stock_pool_members (pool_id TEXT, symbol TEXT);
            CREATE TABLE stock_lifecycle_state (symbol TEXT, trade_date TEXT);
            INSERT INTO quant_stock_pool VALUES ('000001', 'core_pool', 1);
            INSERT INTO stock_pool_members VALUES ('watchlist', '000002');
            INSERT INTO stock_lifecycle_state VALUES ('000001', '{target}');
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    assert workflow.retry_task_skip_reason("lifecycle_state", target) is None


def test_retry_skips_lifecycle_only_when_core_and_group_union_is_covered(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "local_quant.db"
    target = "2026-07-17"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE quant_stock_pool (
                symbol TEXT, pool_name TEXT, is_active INTEGER
            );
            CREATE TABLE stock_pool_members (pool_id TEXT, symbol TEXT);
            CREATE TABLE stock_lifecycle_state (symbol TEXT, trade_date TEXT);
            INSERT INTO quant_stock_pool VALUES ('000001', 'core_pool', 1);
            INSERT INTO stock_pool_members VALUES ('watchlist', '000002');
            INSERT INTO stock_lifecycle_state VALUES ('000001', '{target}');
            INSERT INTO stock_lifecycle_state VALUES ('000002', '{target}');
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    skip_reason = workflow.retry_task_skip_reason("lifecycle_state", target)

    assert skip_reason and "lifecycle coverage 2026-07-17 2/2" in skip_reason


def test_retry_watch_engine_freshness_keeps_legacy_snapshot_behavior(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    target = "2026-07-17"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE watch_daily_snapshot (
                symbol TEXT,
                trade_date TEXT
            );
            INSERT INTO watch_daily_snapshot VALUES ('000001', '{target}');
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    skip_reason = workflow.retry_task_skip_reason("watch_engine", target)

    assert skip_reason and "watch_engine coverage 2026-07-17 1/1" in skip_reason


def test_stock_failure_defers_dependents_but_keeps_support_lane_running():
    tasks = [
        Task("mootdx_qfq_stock_kline", ("python", "kline.py"), critical=True),
        Task("index_daily", ("python", "index.py"), independent=True),
        Task("rps_features", ("python", "rps.py"), critical=True),
    ]
    calls = []

    def fake_runner(task, log_file):
        calls.append(task.name)
        returncode = 1 if task.name == "mootdx_qfq_stock_kline" else 0
        return TaskResult(task.name, returncode, 0.1, task.critical)

    log_file = StringIO()
    results = run_tasks_until_blocked(tasks, log_file, runner=fake_runner)

    assert calls == ["mootdx_qfq_stock_kline", "index_daily"]
    assert [result.name for result in results] == [
        "mootdx_qfq_stock_kline",
        "index_daily",
        "rps_features",
    ]
    assert results[0].failed
    assert results[1].ok
    assert results[2].skipped
    assert "关键任务失败，递延依赖任务" in log_file.getvalue()


def test_time_budget_defers_tasks_at_task_boundary():
    tasks = [
        Task("mootdx_qfq_stock_kline", ("python", "kline.py"), critical=True),
        Task("rps_features", ("python", "rps.py"), critical=True),
    ]

    results = run_tasks_until_blocked(
        tasks,
        StringIO(),
        deadline_monotonic=0,
    )

    assert [result.status_label for result in results] == ["DEFERRED", "DEFERRED"]
    assert all(result.deferred_reason == "time_budget_exhausted" for result in results)


def test_best_effort_task_failure_does_not_block_later_tasks():
    tasks = [
        Task("news", ("python", "news.py")),
        Task("rps_features", ("python", "rps.py"), critical=True),
    ]
    calls = []

    def fake_runner(task, log_file):
        calls.append(task.name)
        returncode = 1 if task.name == "news" else 0
        return TaskResult(task.name, returncode, 0.1, task.critical)

    results = run_tasks_until_blocked(tasks, StringIO(), runner=fake_runner)

    assert calls == ["news", "rps_features"]
    assert [result.status_label for result in results] == ["FAILED", "OK"]


def test_conditional_quality_repair_runs_only_after_preview_failure():
    tasks = [
        Task("quality_preview", ("python",), critical=False),
        Task("quality_repair", ("python",), run_if_failed="quality_preview"),
        Task("quality_final", ("python",), critical=True, run_if_failed="quality_preview"),
    ]
    calls = []

    def passing_runner(task, log_file):
        calls.append(task.name)
        return TaskResult(task.name, 0, 0.1, task.critical)

    passed = run_tasks_until_blocked(tasks, StringIO(), runner=passing_runner)

    assert calls == ["quality_preview"]
    assert [result.status_label for result in passed] == ["OK", "SKIPPED", "SKIPPED"]

    calls.clear()

    def failing_preview_runner(task, log_file):
        calls.append(task.name)
        returncode = 1 if task.name == "quality_preview" else 0
        return TaskResult(task.name, returncode, 0.1, task.critical)

    repaired = run_tasks_until_blocked(tasks, StringIO(), runner=failing_preview_runner)

    assert calls == ["quality_preview", "quality_repair", "quality_final"]
    assert [result.status_label for result in repaired] == ["FAILED", "OK", "OK"]


def test_default_task_env_masks_model_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("GLM_API_KEY", "secret")

    env = build_task_env()

    assert env["EVOALPHA_LOCAL_ONLY"] == "1"
    assert env["EVOALPHA_DISABLE_LLM"] == "1"
    assert env["APP_DEBUG"] == "false"
    assert "OPENAI_API_KEY" not in env
    assert "GLM_API_KEY" not in env


def test_quality_checks_detect_recent_valuation_gap(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL
            );
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                turnover REAL,
                total_mv REAL,
                circ_mv REAL
            );
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-06', 1.2),
                ('000001', '2026-05-07', 1.3);
            INSERT INTO stock_valuation_daily VALUES
                ('000001', '2026-05-06', 1.2, 1000000000, 900000000);
            """
        )

    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    checks = collect_quality_checks([Task("stock_valuation", ("python",), critical=True)])
    by_name = {check.name: check for check in checks}

    assert by_name["valuation_latest_matches_kline"].status == "WARN"
    assert not by_name["valuation_latest_matches_kline"].critical
    assert by_name["valuation_recent_date_gaps"].status == "WARN"
    assert not by_name["valuation_recent_date_gaps"].critical


def test_print_quality_checks_uses_target_date_and_prints_rps_gate(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                turnover_rate REAL
            );
            INSERT INTO stock_info VALUES
                ('000001', '平安银行'),
                ('600519', '贵州茅台'),
                ('920002', '万达轴承');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15', 10, 11, 9, 10.5, 100, 1000, 1.2),
                ('600519', '2026-05-15', 1300, 1310, 1290, 1305, 100, 130500, 0.5),
                ('920002', '2026-05-15', 20, 21, 19, 20.5, 100, 2050, 0.7);
            """
        )

    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    output = StringIO()
    checks = print_quality_checks(
        output,
        [Task("data_core_stock_daily_quality", ("python",), critical=True)],
        target_trade_date="2026-05-15",
    )

    text = output.getvalue()
    assert "target_trade_date=2026-05-15" in text
    assert "[RPS Gate]" in text
    assert "allowed=yes" in text
    assert all(
        check.message.startswith("PASS stock_daily")
        for check in checks
        if check.name.startswith("data_core_stock_daily_")
    )


def test_accepted_gap_failures_are_classified_separately(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_gap_exception (
                symbol TEXT,
                trade_date TEXT,
                market TEXT,
                reason TEXT,
                source TEXT,
                attempts INTEGER,
                status TEXT
            );
            INSERT INTO stock_daily_gap_exception VALUES
                ('600355', '2026-06-04', 'SH', 'tdx_no_raw_data', 'tdx_mootdx_qfq', 1, 'accepted_no_raw_data');
            """
        )

        rows = workflow.accepted_gap_exception_rows(conn, "2026-06-04")

    symbols = workflow._accepted_gap_symbols(rows)
    accepted_failure = {
        "source": "tdx_mootdx_qfq",
        "api_name": "stock_daily_qfq",
        "error_message": "600355:no raw data",
    }
    real_failure = {
        "source": "tdx_mootdx_qfq",
        "api_name": "stock_daily_qfq",
        "error_message": "600000:timeout",
    }

    assert rows[0]["count"] == 1
    assert workflow.is_accepted_gap_source_failure(accepted_failure, symbols)
    assert not workflow.is_accepted_gap_source_failure(real_failure, symbols)


def test_pipeline_task_log_records_deferred_reason(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    monkeypatch.setattr(workflow, "DB_PATH", db_path)
    workflow.ensure_pipeline_log_table()

    workflow.record_pipeline_task(
        "morning_20260715_090000",
        "2026-07-14",
        TaskResult(
            name="rps_features",
            returncode=0,
            elapsed_seconds=0,
            critical=True,
            skipped=True,
            deferred=True,
            deferred_reason="time_budget_exhausted",
        ),
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, target_dates, deferred_reason
            FROM pipeline_task_log
            WHERE run_id = 'morning_20260715_090000'
            """
        ).fetchone()
    assert row == ("deferred", "2026-07-14", "time_budget_exhausted")


def test_c_monitor_finishes_before_combined_watch_digest():
    names = task_names([])
    assert names.index("watch_engine") < names.index("structure_monitor")
    assert names.index("structure_monitor") < names.index("watch_email_digest")
    args = build_parser().parse_args(["--skip-kline", "--force-valuation"])
    monitor = next(task for task in make_tasks(args) if task.name == "structure_monitor")
    assert monitor.timeout_seconds == 120
    assert monitor.independent is True


def test_skip_watch_keeps_c_monitor_without_sending_combined_digest():
    names = task_names(["--skip-watch"])
    assert "structure_monitor" in names
    assert "watch_email_digest" not in names


def test_morning_does_not_run_c_monitor_or_combined_digest():
    names = task_names(["--morning"])
    assert "structure_monitor" not in names
    assert "watch_email_digest" not in names


def test_three_group_prepare_is_after_c_monitor_and_deterministic():
    args = build_parser().parse_args(["--skip-kline", "--force-valuation"])
    tasks = make_tasks(args)
    names = [task.name for task in tasks]
    assert names.index("structure_monitor") < names.index("three_group_report_prepare")
    prepare = next(task for task in tasks if task.name == "three_group_report_prepare")
    assert prepare.command[-3:] == ("--mode", "prepare", "--workflow-child")
    assert "--send" not in prepare.command
    assert prepare.timeout_seconds <= 180
    assert prepare.critical is False


def test_three_group_prepare_excluded_from_morning_and_skip_watch():
    assert "three_group_report_prepare" not in task_names(["--morning"])
    assert "three_group_report_prepare" not in task_names(["--skip-watch"])
