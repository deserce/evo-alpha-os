import sqlite3
from io import StringIO

import pytest

import scripts.run_daily_data_workflow as workflow
from scripts import run_local_data_skill as local_skill
from data_core.quality import (
    MODULE_SECTOR_COMPOSITE,
    MODULE_SECTOR_VENDOR,
    generate_sector_quality_reports,
)
from data_core.source_registry import (
    MODULE_MARKET_SIDE,
    get_source,
    table_definition_map,
)


def _workflow_task_args():
    return workflow.build_parser().parse_args(
        [
            "--skip-kline",
            "--force-valuation",
            "--skip-kline-repair",
            "--skip-gap-repair",
            "--skip-sector-vendor",
            "--include-strategy",
        ]
    )


@pytest.mark.parametrize(
    "failed_gate", ["sector_composite_quality", "sector_rps_v2_quality"]
)
def test_sector_quality_gate_failure_blocks_downstream_core_tasks(
    tmp_path, monkeypatch, failed_gate
):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "missing.db")
    tasks = workflow.make_tasks(_workflow_task_args())
    called: list[str] = []

    def runner(task, _log_file):
        called.append(task.name)
        return workflow.TaskResult(
            name=task.name,
            returncode=1 if task.name == failed_gate else 0,
            elapsed_seconds=0.0,
            critical=task.critical,
        )

    results = workflow.run_tasks_until_blocked(tasks, StringIO(), runner=runner)
    result_by_name = {result.name: result for result in results}

    assert result_by_name[failed_gate].failed
    gate_index = called.index(failed_gate)
    downstream_core = {
        "sector_rps_v2",
        "sector_rps_legacy_shadow",
        "etf_rps_features",
        "strength_features",
        "structure_health",
        "core_stock_pool",
        "mrgc_preselect",
        "timing_signals",
        "risk_plans",
        "lifecycle_state",
        "watch_engine",
    }
    assert not downstream_core.intersection(called[gate_index + 1 :])


def test_legacy_sector_rps_is_shadow_and_stock_rps_remains_critical(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "missing.db")
    tasks = workflow.make_tasks(_workflow_task_args())
    by_name = {task.name: task for task in tasks}

    stock_rps = by_name["rps_features"]
    legacy_sector_rps = by_name["sector_rps_legacy_shadow"]

    assert stock_rps.critical
    assert "stock" in stock_rps.command
    assert "sector" not in stock_rps.command
    assert not legacy_sector_rps.critical
    assert not legacy_sector_rps.independent
    assert "sector" in legacy_sector_rps.command
    assert "stock" not in legacy_sector_rps.command
    assert tasks.index(legacy_sector_rps) > tasks.index(by_name["sector_rps_v2_quality"])


def test_legacy_sector_rps_failure_does_not_block_core_downstream(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "missing.db")
    tasks = workflow.make_tasks(_workflow_task_args())
    called: list[str] = []

    def runner(task, _log_file):
        called.append(task.name)
        return workflow.TaskResult(
            name=task.name,
            returncode=1 if task.name == "sector_rps_legacy_shadow" else 0,
            elapsed_seconds=0.0,
            critical=task.critical,
        )

    results = workflow.run_tasks_until_blocked(tasks, StringIO(), runner=runner)
    result_by_name = {result.name: result for result in results}

    assert result_by_name["sector_rps_legacy_shadow"].failed
    shadow_index = called.index("sector_rps_legacy_shadow")
    assert "strength_features" in called[shadow_index + 1 :]
    assert "watch_engine" in called[shadow_index + 1 :]


def test_daily_plan_has_sector_core_chain_and_vendor_tail():
    args = workflow.build_parser().parse_args(
        ["--skip-kline", "--force-valuation", "--skip-p1", "--skip-watch", "--skip-portfolio"]
    )
    tasks = workflow.make_tasks(args)
    names = [task.name for task in tasks]
    assert "sector_composite_daily" in names
    assert "sector_composite_quality" in names
    assert "sector_rps_v2" in names
    assert "sector_vendor_daily" in names
    assert names.index("data_core_stock_daily_quality") < names.index("sector_composite_daily")
    assert names.index("sector_composite_daily") < names.index("sector_composite_quality")
    assert names.index("sector_composite_quality") < names.index("sector_rps_v2")
    assert names.index("sector_rps_v2") < names.index("sector_vendor_daily")

    by_name = {task.name: task for task in tasks}
    assert by_name["sector_composite_daily"].critical
    assert by_name["sector_composite_quality"].critical
    assert by_name["sector_rps_v2"].critical
    assert by_name["sector_vendor_daily"].independent
    assert not by_name["sector_vendor_daily"].critical


def test_retry_skips_sector_tasks_only_when_target_is_complete(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sector_composite_daily (
                sector_name TEXT,
                trade_date TEXT,
                net_value REAL
            );
            CREATE TABLE sector_vendor_daily (
                sector_name TEXT,
                trade_date TEXT,
                close REAL
            );
            INSERT INTO sector_composite_daily VALUES ('银行', '2026-08-25', 1000);
            INSERT INTO sector_vendor_daily VALUES ('银行', '2026-08-25', 1000);
            """
        )
    monkeypatch.setattr(workflow, "DB_PATH", db_path)

    assert workflow.retry_task_skip_reason("sector_composite_daily", "2026-08-25")
    assert workflow.retry_task_skip_reason("sector_vendor_daily", "2026-08-25")
    assert workflow.retry_task_skip_reason("sector_composite_daily", "2026-08-26") is None


def test_vendor_daily_preflight_never_starts_unbounded_initial_backfill(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sector_list (sector_name TEXT, sector_type TEXT);
            INSERT INTO sector_list VALUES ('银行', 'Industry');
            CREATE TABLE sector_vendor_daily (sector_name TEXT, trade_date TEXT);
            INSERT INTO sector_vendor_daily VALUES ('银行', '2026-08-25');
            """
        )
        assert workflow.sector_vendor_incremental_skip_reason(conn, "2026-08-26") is None
        assert "bounded catchup" in workflow.sector_vendor_incremental_skip_reason(
            conn, "2026-08-26", max_catchup_days=0
        )

    with sqlite3.connect(tmp_path / "empty.db") as conn:
        conn.execute("CREATE TABLE sector_list (sector_name TEXT, sector_type TEXT)")
        conn.execute("INSERT INTO sector_list VALUES ('银行', 'Industry')")
        assert "not initialized" in workflow.sector_vendor_incremental_skip_reason(
            conn, "2026-08-26"
        )


def test_vendor_daily_target_only_bootstrap_allows_empty_vendor_table(tmp_path):
    """目标日批量快照不会触发历史回填，首次运行应允许采集器自建表。"""
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sector_list (sector_name TEXT, sector_type TEXT)")
        conn.execute("INSERT INTO sector_list VALUES ('银行', 'Industry')")

        assert workflow.sector_vendor_incremental_skip_reason(
            conn,
            "2026-08-26",
            target_only=True,
        ) is None


def _sector_quality_db(tmp_path, *, coverage: float = 1.0, vendor: bool = True):
    db_path = tmp_path / "quality.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sector_list (
                sector_name TEXT,
                sector_type TEXT
            );
            CREATE TABLE sector_composite_daily (
                sector_name TEXT,
                trade_date TEXT,
                net_value REAL,
                pct_chg REAL,
                coverage_ratio REAL,
                method_version TEXT,
                quality_flag TEXT
            );
            INSERT INTO sector_list VALUES ('银行', 'Industry');
            INSERT INTO sector_list VALUES ('证券', 'Industry');
            """
        )
        conn.executemany(
            "INSERT INTO sector_composite_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("银行", "2026-08-25", 1000, 0.2, coverage, "ew_qfq_v1", "PASS"),
                ("证券", "2026-08-25", 1010, 1.0, coverage, "ew_qfq_v1", "PASS"),
            ],
        )
        if vendor:
            conn.execute(
                """
                CREATE TABLE sector_vendor_daily (
                    sector_name TEXT,
                    trade_date TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    source TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO sector_vendor_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("银行", "2026-08-25", 1000, 1010, 990, 1005, "eastmoney_bk"),
                    ("证券", "2026-08-25", 1010, 1020, 1000, 1015, "eastmoney_bk"),
                ],
            )
    return db_path


def test_sector_quality_reports_pass_composite_and_vendor(tmp_path):
    reports = generate_sector_quality_reports(
        _sector_quality_db(tmp_path),
        trade_date="2026-08-25",
        run_id="test-sector",
        persist=False,
    )
    by_module = {report["module"]: report for report in reports}
    assert by_module[MODULE_SECTOR_COMPOSITE]["status"] == "PASS"
    assert by_module[MODULE_SECTOR_COMPOSITE]["detail"]["method_versions"] == ["ew_qfq_v1"]
    assert by_module[MODULE_SECTOR_VENDOR]["status"] == "PASS"


def test_sector_quality_does_not_allow_low_composite_coverage(tmp_path):
    reports = generate_sector_quality_reports(
        _sector_quality_db(tmp_path, coverage=0.70, vendor=False),
        trade_date="2026-08-25",
        run_id="test-sector",
        persist=False,
    )
    by_module = {report["module"]: report for report in reports}
    assert by_module[MODULE_SECTOR_COMPOSITE]["status"] == "FAIL"
    assert by_module[MODULE_SECTOR_VENDOR]["status"] == "WARNING"


def test_sector_sources_and_tables_are_registered():
    vendor = get_source("eastmoney_bk_vendor")
    composite = get_source("local_sector_composite")
    tables = table_definition_map()
    assert vendor is not None
    assert vendor.module == MODULE_MARKET_SIDE
    assert not vendor.blocks_downstream
    assert composite is not None
    assert "sector_composite_daily" in tables
    assert "sector_vendor_daily" in tables
    assert "quant_feature_sector_rps_v2" in tables


def test_health_report_exposes_composite_vendor_and_method_version(tmp_path, monkeypatch):
    db_path = _sector_quality_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE quant_feature_sector_rps_v2 (
                sector_name TEXT,
                trade_date TEXT,
                rps_20 REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO quant_feature_sector_rps_v2 VALUES (?, ?, ?)",
            [("银行", "2026-08-25", 80), ("证券", "2026-08-25", 60)],
        )
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    with sqlite3.connect(db_path) as conn:
        health = local_skill.sector_data_health(conn, "2026-08-25")

    assert health["composite"]["latest"] == "2026-08-25"
    assert health["composite"]["coverage_ratio"] == 1.0
    assert health["composite"]["method_versions"] == ["ew_qfq_v1"]
    assert health["vendor"]["latest"] == "2026-08-25"
    assert health["rps"]["latest"] == "2026-08-25"
