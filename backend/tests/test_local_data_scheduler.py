import gzip
import json
import os
import shlex
import subprocess
import sqlite3
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from scripts import install_local_data_launchd as launchd
from scripts import repair_stock_info_list_dates as list_dates
from scripts import run_local_data_skill as local_skill
from data_core.schema import ensure_data_core_schema
from quant_engine.runner.strategy_runner import record_strategy_run


class _RecordingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql, parameters=(), /):
        self.executed.append((str(sql), tuple(parameters)))
        return super().execute(sql, parameters)


def test_local_skill_env_masks_model_credentials():
    env = local_skill.local_only_env({
        "OPENAI_API_KEY": "secret",
        "ANTHROPIC_API_KEY": "secret",
        "PATH": os.environ.get("PATH", ""),
    })

    assert env["EVOALPHA_LOCAL_ONLY"] == "1"
    assert env["EVOALPHA_DISABLE_LLM"] == "1"
    assert env["APP_DEBUG"] == "false"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_local_skill_workflow_commands_include_strategy_and_lightweight_health(monkeypatch):
    monkeypatch.setattr(local_skill, "stock_kline_freshness", lambda target: (False, "stale"))

    early = local_skill.build_workflow_command("early", "2026-05-28")
    morning = local_skill.build_workflow_command(
        "morning",
        "2026-05-28",
        pending_days=7,
        time_budget_minutes=25,
    )
    daily = local_skill.build_workflow_command("daily", "2026-05-28")
    retry = local_skill.build_workflow_command("retry", "2026-05-28")
    catchup = local_skill.build_workflow_command("catchup", "2026-05-28")
    health = local_skill.build_workflow_command("health", "2026-05-28")

    assert early[-4:] == ["--skip-gap-repair", "--skip-rps", "--skip-p0", "--skip-p1"]
    assert "--include-strategy" not in early
    assert morning[-6:] == [
        "--include-strategy",
        "--morning",
        "--pending-days",
        "7",
        "--time-budget-minutes",
        "25",
    ]
    assert daily[-4:] == ["--kline-end", "2026-05-28", "--include-strategy", "--skip-gap-repair"]
    assert "--force-kline" not in retry
    assert "--retry" in retry
    assert "--force-kline" in catchup
    assert "--include-strategy" in retry
    assert "--include-strategy" in catchup
    assert "--skip-gap-repair" in retry
    assert "--skip-gap-repair" not in catchup
    assert health == []


def test_watch_post_workflow_commands_leave_digest_to_fixed_evening_job():
    daily = local_skill.build_watch_post_command("daily", "2026-07-13")
    retry = local_skill.build_watch_post_command("retry", "2026-07-13")

    assert daily == []
    assert retry[-2:] == ["--mode", "retry"]
    assert local_skill.build_watch_post_command("early", "2026-07-13") == []
    assert local_skill.build_watch_post_command("health", "2026-07-13") == []


def test_skip_notifications_propagates_and_suppresses_retry_dispatch():
    args = local_skill.build_parser().parse_args(["--mode", "retry", "--skip-notifications"])
    assert args.skip_notifications
    for mode in ("daily", "retry", "catchup"):
        command = local_skill.build_workflow_command(mode, "2026-09-15", skip_notifications=True)
        assert "--skip-notifications" in command
        assert local_skill.build_watch_post_command(
            mode, "2026-09-15", skip_notifications=True
        ) == []


def test_local_env_direct_copy_preserves_original():
    from data_job.common.network_utils import PROXY_ENV_KEYS

    original = {key: "http://127.0.0.1:7897" for key in PROXY_ENV_KEYS}
    env = local_skill.local_only_env(original)
    assert set(original) == set(PROXY_ENV_KEYS)
    assert not set(PROXY_ENV_KEYS) & env.keys()
    assert env["NO_PROXY"] == env["no_proxy"] == "*"


def test_launchd_clears_login_shell_proxy_environment():
    from data_job.common.network_utils import PROXY_ENV_KEYS

    for job in launchd.LAUNCH_JOBS:
        plist = launchd.build_plist(job)
        tokens = shlex.split(plist["ProgramArguments"][2])
        command = tokens[tokens.index("exec") + 1:]
        assert command[0] == "/usr/bin/env"
        for key in PROXY_ENV_KEYS:
            assert command[command.index(key) - 1] == "-u"
        assert "NO_PROXY=*" in command and "no_proxy=*" in command
        assert command.index("NO_PROXY=*") < command.index("/usr/bin/caffeinate")


def test_retry_skips_gap_repair_even_when_stock_kline_is_stale(monkeypatch):
    monkeypatch.setattr(local_skill, "stock_kline_freshness", lambda target: (False, "stale"))

    retry = local_skill.build_workflow_command("retry", "2026-05-28")

    assert "--skip-gap-repair" in retry


def test_launchd_jobs_generate_expected_weekday_schedules():
    jobs = {job.label: job for job in launchd.LAUNCH_JOBS}

    morning = jobs["com.evoalpha.datacore.morning"]
    daily = jobs["com.evoalpha.datacore.daily"]
    retry = jobs["com.evoalpha.datacore.retry"]
    health = jobs["com.evoalpha.datacore.health"]
    deep_health = jobs["com.evoalpha.datacore.deep-health"]
    intraday = jobs["com.evoalpha.watch.intraday"]
    evening = jobs["com.evoalpha.watch.evening"]
    watch_retry = jobs["com.evoalpha.watch.retry"]

    assert (morning.hour, morning.minute, morning.weekdays) == (9, 0, (1, 2, 3, 4, 5))
    assert (daily.hour, daily.minute) == (15, 30)
    assert (retry.hour, retry.minute) == (16, 30)
    assert (health.hour, health.minute, health.weekdays) == (9, 30, (1, 2, 3, 4, 5))
    assert (deep_health.hour, deep_health.minute, deep_health.weekdays) == (9, 15, (6,))
    assert deep_health.extra_arguments == ("--deep-health",)
    assert (intraday.hour, intraday.minute) == (9, 35)
    assert (evening.hour, evening.minute, evening.module) == (
        20,
        0,
        "quant_engine.watch.evening",
    )
    assert (watch_retry.hour, watch_retry.minute, watch_retry.module) == (
        22,
        30,
        "quant_engine.watch.evening",
    )
    assert watch_retry.extra_arguments == ("--mode", "retry")
    assert "com.evoalpha.datacore.early" not in jobs
    assert intraday.label == "com.evoalpha.watch.intraday"

    morning_plist = launchd.build_plist(morning)
    morning_tokens = shlex.split(morning_plist["ProgramArguments"][2])
    assert morning_tokens[-6:] == [
        "--mode",
        "morning",
        "--pending-days",
        "7",
        "--time-budget-minutes",
        "25",
    ]

    plist = launchd.build_plist(daily)
    schedule = plist["StartCalendarInterval"]
    assert len(schedule) == 5
    assert {item["Weekday"] for item in schedule} == {1, 2, 3, 4, 5}
    assert {item["Hour"] for item in schedule} == {15}
    assert {item["Minute"] for item in schedule} == {30}
    assert plist["EnvironmentVariables"]["EVOALPHA_DISABLE_LLM"] == "1"
    assert "WorkingDirectory" not in plist
    assert plist["ProgramArguments"][:2] == ["/bin/zsh", "-lc"]
    daily_tokens = shlex.split(plist["ProgramArguments"][2])
    command_start = daily_tokens.index("cd")
    assert daily_tokens[command_start : command_start + 4] == [
        "cd",
        str(launchd.BACKEND_DIR),
        "&&",
        "exec",
    ]
    caffeinate_start = daily_tokens.index("/usr/bin/caffeinate")
    assert daily_tokens[caffeinate_start : caffeinate_start + 2] == [
        "/usr/bin/caffeinate",
        "-i",
    ]
    assert daily_tokens[-2:] == ["--mode", "daily"]
    assert plist["EnvironmentVariables"]["HOME"] == str(launchd.Path.home())
    assert plist["EnvironmentVariables"]["PATH"].startswith("/opt/anaconda3/bin:")
    assert plist["EnvironmentVariables"]["LANG"] == "en_US.UTF-8"
    assert plist["EnvironmentVariables"]["LC_ALL"] == "en_US.UTF-8"
    assert plist["EnvironmentVariables"]["APP_DEBUG"] == "false"
    assert plist["EnvironmentVariables"] | {
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
    } == plist["EnvironmentVariables"]
    launchd.lint_plist_bytes(launchd.dump_plist(plist))

    intraday_plist = launchd.build_plist(intraday)
    assert intraday_plist["RunAtLoad"] is True
    assert plist["RunAtLoad"] is False
    intraday_tokens = shlex.split(intraday_plist["ProgramArguments"][2])
    assert intraday_tokens[-3:] == [
        "-m",
        "quant_engine.watch.intraday",
        "--run-until-close",
    ]
    assert intraday_plist["StartCalendarInterval"] == [
        {"Weekday": weekday, "Hour": 9, "Minute": 35}
        for weekday in (1, 2, 3, 4, 5)
    ]


def test_launchd_logs_use_persistent_directory_outside_protected_documents():
    assert launchd.LOG_DIR == launchd.Path.home() / "Library" / "Logs" / "EvoAlpha"

    for job in launchd.LAUNCH_JOBS:
        plist = launchd.build_plist(job)
        stdout = launchd.Path(str(plist["StandardOutPath"]))
        stderr = launchd.Path(str(plist["StandardErrorPath"]))

        assert stdout.parent == launchd.LOG_DIR
        assert stderr.parent == launchd.LOG_DIR
        assert not stdout.is_relative_to(launchd.BACKEND_DIR)
        assert not stderr.is_relative_to(launchd.BACKEND_DIR)
        assert str(launchd.LAUNCHD_LOG_MAX_BYTES) in str(plist["ProgramArguments"][2])
        launchd.validate_plist(plist)


def test_launchd_validation_rejects_protected_log_paths():
    plist = launchd.build_plist(launchd.LAUNCH_JOBS[0])
    plist["StandardOutPath"] = str(launchd.BACKEND_DIR / "logs" / "unsafe.out.log")

    with pytest.raises(ValueError, match="受保护目录"):
        launchd.validate_plist(plist)


def test_install_updates_active_job_plist_without_restarting(tmp_path, monkeypatch):
    job = launchd.LaunchJob(
        "com.evoalpha.test.active",
        "health",
        9,
        30,
        run_at_load=True,
    )
    launch_agents = tmp_path / "LaunchAgents"
    log_dir = tmp_path / "logs"
    calls: list[list[str]] = []

    monkeypatch.setattr(launchd, "LAUNCH_JOBS", (job,))
    monkeypatch.setattr(launchd, "OBSOLETE_LABELS", ())
    monkeypatch.setattr(launchd, "LAUNCH_AGENTS_DIR", launch_agents)
    monkeypatch.setattr(launchd, "LOG_DIR", log_dir)

    def fake_launchctl(args, *, check=False):
        calls.append(list(args))
        return subprocess.CompletedProcess(
            ["launchctl", *args],
            0,
            stdout="state = running\npid = 12345\n",
            stderr="",
        )

    monkeypatch.setattr(launchd, "run_launchctl", fake_launchctl)

    assert launchd.install() == 0
    installed = launchd.load_installed_plist(launchd.plist_path(job))
    assert installed is not None
    assert installed["StandardOutPath"] == str(log_dir / f"{job.label}.out.log")
    assert [call[0] for call in calls] == ["print"]


def test_retry_defaults_to_waiting_for_existing_workflow_lock():
    assert local_skill.default_lock_wait_minutes("morning") == 5
    assert local_skill.default_lock_wait_minutes("early") == 5
    assert local_skill.default_lock_wait_minutes("daily") == 10
    assert local_skill.default_lock_wait_minutes("retry") == 45
    assert local_skill.default_lock_wait_minutes("health") == 0
    assert local_skill.default_workflow_timeout_minutes("early") == 120
    assert local_skill.default_workflow_timeout_minutes("daily") == 240
    assert local_skill.default_workflow_timeout_minutes("retry") == 150
    assert local_skill.default_workflow_timeout_minutes("morning") == 25
    assert local_skill.default_workflow_timeout_minutes("health") == 0


def test_run_command_terminates_workflow_on_timeout(monkeypatch):
    class FakeProcess:
        pid = 12345

        def wait(self, timeout=None):
            if timeout == 1:
                raise subprocess.TimeoutExpired(cmd="workflow", timeout=timeout)
            return 0

    fake_process = FakeProcess()
    monkeypatch.setattr(local_skill.subprocess, "Popen", lambda *args, **kwargs: fake_process)
    kills = []
    monkeypatch.setattr(local_skill.os, "killpg", lambda pid, sig: kills.append((pid, sig)))

    result = local_skill.run_command(["python3", "workflow.py"], {}, timeout_seconds=1)

    assert result == local_skill.WORKFLOW_TIMEOUT_EXIT_CODE
    assert kills == [(fake_process.pid, local_skill.signal.SIGTERM)]


def test_launchd_status_helpers_parse_installed_and_active_python():
    legacy_args = [
        "/usr/bin/caffeinate",
        "-i",
        "/opt/anaconda3/bin/python3",
        "/repo/backend/scripts/run_local_data_skill.py",
        "--mode",
        "daily",
    ]
    shell_args = [
        "/bin/zsh",
        "-lc",
        (
            "cd /repo/backend && exec /usr/bin/caffeinate -i "
            "/opt/anaconda3/bin/python3 -m quant_engine.watch.intraday --run-until-close"
        ),
    ]

    assert launchd.python_from_program_arguments(legacy_args) == "/opt/anaconda3/bin/python3"
    assert launchd.python_from_program_arguments(shell_args) == "/opt/anaconda3/bin/python3"
    assert launchd.python_from_program_arguments(["/bin/zsh", "-lc", "cd 'unterminated"]) is None
    assert launchd.last_exit_status("last exit code = 78: EX_CONFIG\n") == "78: EX_CONFIG"
    assert launchd.last_exit_status("last exit code = 0\n") == "0"
    assert launchd.last_exit_status("state = running\n") == "-"
    assert launchd.active_pid("com.evoalpha.datacore.daily", "state = running\npid = 12345\n")
    assert (
        launchd.python_from_process_command(
            "/opt/anaconda3/bin/python3 /repo/backend/scripts/run_local_data_skill.py --mode daily"
        )
        == "/opt/anaconda3/bin/python3"
    )


def test_stale_lock_metadata_requires_dead_pid_and_old_heartbeat(monkeypatch):
    monkeypatch.setattr(local_skill, "process_exists", lambda pid: False)
    metadata = {
        "pid": 999999,
        "heartbeat_at": "2026-06-02 16:00:00",
    }
    monkeypatch.setattr(local_skill, "datetime", type("FakeDateTime", (), {
        "now": staticmethod(lambda: __import__("datetime").datetime(2026, 6, 2, 16, 30, 0)),
        "strptime": staticmethod(__import__("datetime").datetime.strptime),
    }))

    assert local_skill.metadata_is_stale(metadata, stale_seconds=20 * 60)

    monkeypatch.setattr(local_skill, "process_exists", lambda pid: True)
    assert not local_skill.metadata_is_stale(metadata, stale_seconds=20 * 60)


def test_local_skill_recent_gap_summary_uses_effective_missing(tmp_path, monkeypatch):
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
                ('000002', '万科A');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15', 10, 11, 9, 10.5, 100, 1000, 1.2);
            """
        )
    ensure_data_core_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO stock_daily_gap_exception (
                symbol, trade_date, market, reason, source, attempts, status
            ) VALUES ('000002', '2026-05-15', 'SZ', 'tdx_no_raw_data', 'tdx_mootdx_qfq', 1, 'accepted_no_raw_data')
            """
        )
        conn.commit()
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)

    summary = local_skill._recent_gap_summary("2026-05-15", window_trading_days=1)

    assert summary["raw_missing_count"] == 1
    assert summary["exception_count"] == 1
    assert summary["effective_missing_count"] == 0


def test_retention_prunes_auto_and_manual_backups_separately(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    now = datetime(2026, 6, 5, 12, 0, 0)

    for index in range(5):
        path = backup_dir / f"local_quant_2026-06-0{index + 1}_2026060{index + 1}_120000.db"
        path.write_text("auto")
        timestamp = (now - timedelta(days=5 - index)).timestamp()
        os.utime(path, (timestamp, timestamp))
    recent_manual = backup_dir / "local_quant_before_fix_20260601_120000.db"
    old_manual = backup_dir / "local_quant_before_fix_20260401_120000.db"
    old_nonstandard_manual = backup_dir / "local_quant.sector_fix_20260401_120000.db"
    recent_manual.write_text("manual")
    old_manual.write_text("manual")
    old_nonstandard_manual.write_text("manual")
    os.utime(recent_manual, ((now - timedelta(days=10)).timestamp(),) * 2)
    os.utime(old_manual, ((now - timedelta(days=40)).timestamp(),) * 2)
    os.utime(old_nonstandard_manual, ((now - timedelta(days=40)).timestamp(),) * 2)

    summary = local_skill.prune_backups(
        retention=2,
        backup_dir=backup_dir,
        manual_retention_days=30,
        now=now,
    )

    remaining = {path.name for path in backup_dir.glob("*.db")}
    assert summary == {"auto_deleted": 3, "manual_deleted": 2}
    assert len([name for name in remaining if name.startswith("local_quant_2026")]) == 2
    assert recent_manual.name in remaining
    assert old_manual.name not in remaining
    assert old_nonstandard_manual.name not in remaining


def test_retention_keeps_latest_auto_backup_per_target_date(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    now = datetime(2026, 6, 5, 12, 0, 0)

    old_same_target = backup_dir / "local_quant_2026-06-05_20260605_080000.db"
    new_same_target = backup_dir / "local_quant_2026-06-05_20260605_120000.db"
    previous_target = backup_dir / "local_quant_2026-06-04_20260604_120000.db"
    expired_target = backup_dir / "local_quant_2026-06-03_20260603_120000.db"

    for index, path in enumerate([expired_target, previous_target, old_same_target, new_same_target]):
        path.write_text("auto")
        timestamp = (now - timedelta(hours=4 - index)).timestamp()
        os.utime(path, (timestamp, timestamp))

    summary = local_skill.prune_backups(
        retention=2,
        backup_dir=backup_dir,
        manual_retention_days=30,
        now=now,
    )

    remaining = {path.name for path in backup_dir.glob("*.db")}
    assert summary == {"auto_deleted": 2, "manual_deleted": 0}
    assert new_same_target.name in remaining
    assert previous_target.name in remaining
    assert old_same_target.name not in remaining
    assert expired_target.name not in remaining


def test_default_backup_retention_keeps_one_automatic_snapshot():
    assert local_skill.BACKUP_RETENTION == 1


def test_retention_prunes_compressed_auto_backups(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_backup = backup_dir / "local_quant_2026-06-03_20260603_120000.db.gz"
    new_backup = backup_dir / "local_quant_2026-06-04_20260604_120000.db.gz"
    old_backup.write_bytes(b"old")
    new_backup.write_bytes(b"new")
    os.utime(old_backup, (1, 1))
    os.utime(new_backup, (2, 2))

    summary = local_skill.prune_backups(retention=1, backup_dir=backup_dir)

    assert summary == {"auto_deleted": 1, "manual_deleted": 0}
    assert not old_backup.exists()
    assert new_backup.exists()


def test_total_backup_retention_cleans_custom_names_and_orphan_sidecars(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_custom = backup_dir / "local_quant_sector_chart_final_20260826_102509.db"
    old_custom.write_text("old")
    latest = backup_dir / "local_quant_2026-08-26_20260826_130645.db"
    latest.write_text("latest")
    orphan_wal = backup_dir / "local_quant_sector_chart_final_20260826_102509.db-wal"
    orphan_shm = backup_dir / "local_quant_sector_chart_final_20260826_102509.db-shm"
    orphan_wal.write_text("wal")
    orphan_shm.write_text("shm")
    os.utime(old_custom, (1, 1))
    os.utime(latest, (2, 2))

    summary = local_skill.prune_backups(
        retention=1,
        backup_dir=backup_dir,
        total_retention=1,
    )

    assert summary["total_deleted"] == 3
    assert latest.exists()
    assert not old_custom.exists()
    assert not orphan_wal.exists()
    assert not orphan_shm.exists()


def test_backup_compression_keeps_latest_snapshot_ready_to_restore(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_backup = backup_dir / "local_quant_2026-06-04_20260604_120000.db"
    latest_backup = backup_dir / "local_quant_before_fix_20260605_120000.db"
    for path, value in ((old_backup, "old"), (latest_backup, "latest")):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE sample (value TEXT)")
            conn.execute("INSERT INTO sample VALUES (?)", (value,))
    os.utime(old_backup, (1, 1))
    os.utime(latest_backup, (2, 2))

    summary = local_skill.compress_archival_backups(backup_dir=backup_dir)

    archived = old_backup.with_suffix(".db.gz")
    assert summary["compressed"] == 1
    assert summary["bytes_freed"] > 0
    assert archived.exists()
    assert not old_backup.exists()
    assert latest_backup.exists()
    with gzip.open(archived, "rb") as handle:
        assert handle.read(16) == b"SQLite format 3\x00"


def test_backup_reuses_existing_snapshot_for_same_target(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    existing = backup_dir / "local_quant_2026-07-14_20260715_090001.db"
    existing.write_text("snapshot")
    monkeypatch.setattr(local_skill, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(
        local_skill,
        "create_database_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must reuse")),
    )

    result = local_skill.ensure_database_backup("2026-07-14")

    assert result.path == existing
    assert result.reused is True
    assert result.method == "existing"


def test_backup_reuses_compressed_snapshot_and_ignores_temp_file(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    existing = backup_dir / "local_quant_2026-07-14_20260715_090001.db.gz"
    interrupted = backup_dir / "local_quant_2026-07-14_20260715_100001.db.gz.tmp"
    existing.write_bytes(b"snapshot")
    interrupted.write_bytes(b"partial")
    os.utime(existing, (1, 1))
    os.utime(interrupted, (2, 2))
    monkeypatch.setattr(local_skill, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(
        local_skill,
        "create_database_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must reuse")),
    )

    result = local_skill.ensure_database_backup("2026-07-14")

    assert result.path == existing
    assert result.reused is True


def test_backup_uses_apfs_clone_after_wal_checkpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.execute("INSERT INTO sample VALUES ('ok')")
    calls = []

    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(local_skill, "checkpoint_wal", lambda path: calls.append("checkpoint") or True)

    def clone(source, target):
        calls.append("clone")
        target.write_bytes(source.read_bytes())
        return True

    monkeypatch.setattr(local_skill, "try_apfs_clone", clone)
    result = local_skill.ensure_database_backup("2026-07-14")

    assert calls == ["checkpoint", "clone"]
    assert result.reused is False
    assert result.method == "apfs_clone"
    with sqlite3.connect(result.path) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "ok"


def test_backup_falls_back_to_sqlite_online_backup(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.execute("INSERT INTO sample VALUES ('fallback')")

    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(local_skill, "checkpoint_wal", lambda path: False)
    monkeypatch.setattr(local_skill, "try_apfs_clone", lambda source, target: False)

    result = local_skill.ensure_database_backup("2026-07-14")

    assert result.method == "sqlite_online_backup"
    with sqlite3.connect(result.path) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "fallback"


def test_retention_compresses_workflow_logs_and_trims_only_idle_launchd_logs(tmp_path):
    log_dir = tmp_path / "logs"
    launchd_dir = log_dir / "launchd"
    launchd_dir.mkdir(parents=True)
    now = datetime(2026, 6, 5, 12, 0, 0)

    old_log = log_dir / "daily_data_workflow_20260520_120000.log"
    expired_gz = log_dir / "daily_data_workflow_20260420_120000.log.gz"
    unrelated = launchd_dir / "com.evoalpha.datacore.daily.out.log"
    old_log.write_text("old workflow")
    expired_gz.write_text("expired")
    unrelated.write_bytes(b"x" * 100)
    os.utime(old_log, ((now - timedelta(days=10)).timestamp(),) * 2)
    os.utime(expired_gz, ((now - timedelta(days=40)).timestamp(),) * 2)

    workflow_summary = local_skill.prune_workflow_logs(log_dir=log_dir, now=now)
    active_summary = local_skill.trim_launchd_logs_if_idle(
        launchd_log_dir=launchd_dir,
        is_idle=False,
        max_bytes=10,
        tail_bytes=5,
    )
    idle_summary = local_skill.trim_launchd_logs_if_idle(
        launchd_log_dir=launchd_dir,
        is_idle=True,
        max_bytes=10,
        tail_bytes=5,
    )

    assert workflow_summary == {"compressed": 1, "deleted": 1}
    assert not old_log.exists()
    assert old_log.with_suffix(".log.gz").exists()
    assert not expired_gz.exists()
    assert active_summary == {"trimmed": 0}
    assert idle_summary == {"trimmed": 1}
    assert unrelated.read_bytes().endswith(b"x" * 5)


def test_atomic_write_text_replaces_status_file(tmp_path):
    path = tmp_path / "latest.json"
    path.write_text('{"old": true}', encoding="utf-8")

    local_skill._atomic_write_text(path, '{"new": true}')

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob(".latest.json.*.tmp"))


def _create_status_db(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT);
            CREATE TABLE stock_valuation_daily (code TEXT, trade_date TEXT);
            CREATE TABLE quant_feature_rps (symbol TEXT, trade_date TEXT);
            CREATE TABLE quant_feature_sector_rps (sector_name TEXT, trade_date TEXT);
            CREATE TABLE market_strength_daily (index_code TEXT, trade_date TEXT);
            CREATE TABLE industry_strength_daily (sector_name TEXT, trade_date TEXT);
            CREATE TABLE structure_health_daily (symbol TEXT, trade_date TEXT);
            CREATE TABLE stock_lifecycle_state (symbol TEXT, trade_date TEXT);
            CREATE TABLE etf_info (symbol TEXT PRIMARY KEY);
            CREATE TABLE index_daily (index_code TEXT, trade_date TEXT);
            CREATE TABLE etf_daily_prices (symbol TEXT, trade_date TEXT);
            CREATE TABLE quant_feature_etf_rps (symbol TEXT, trade_date TEXT);
            CREATE TABLE data_quality_report (
                run_id TEXT,
                trade_date TEXT,
                module TEXT,
                market TEXT,
                status TEXT,
                created_at TEXT
            );
            CREATE TABLE strategy_run_log (
                strategy_name TEXT,
                trade_date TEXT,
                status TEXT,
                signal_count INTEGER,
                finished_at TEXT
            );
            CREATE TABLE stock_daily_gap_exception (
                symbol TEXT,
                trade_date TEXT,
                market TEXT,
                reason TEXT,
                source TEXT,
                attempts INTEGER,
                status TEXT
            );
            CREATE TABLE pipeline_run_log (
                run_id TEXT PRIMARY KEY,
                pipeline_name TEXT,
                mode TEXT,
                status TEXT,
                started_at TEXT,
                finished_at TEXT,
                elapsed_seconds REAL,
                critical_failed_tasks TEXT,
                failed_tasks TEXT,
                log_path TEXT
            );
            INSERT INTO stock_daily_prices VALUES ('000001', '2026-06-04');
            INSERT INTO stock_valuation_daily VALUES ('000001', '2026-06-04');
            INSERT INTO quant_feature_rps VALUES ('000001', '2026-06-04');
            INSERT INTO quant_feature_sector_rps VALUES ('银行', '2026-06-04');
            INSERT INTO market_strength_daily VALUES ('ALL', '2026-06-04');
            INSERT INTO industry_strength_daily VALUES ('银行', '2026-06-04');
            INSERT INTO structure_health_daily VALUES ('000001', '2026-06-04');
            INSERT INTO stock_lifecycle_state VALUES ('000001', '2026-06-04');
            INSERT INTO etf_info VALUES ('510300'), ('159915');
            INSERT INTO index_daily VALUES
                ('000001', '2026-06-04'), ('000300', '2026-06-04'),
                ('000905', '2026-06-04'), ('000852', '2026-06-04'),
                ('000688', '2026-06-04'), ('399001', '2026-06-04'),
                ('399006', '2026-06-04'), ('399005', '2026-06-04');
            INSERT INTO etf_daily_prices VALUES ('510300', '2026-06-04');
            INSERT INTO quant_feature_etf_rps VALUES ('510300', '2026-06-04');
            INSERT INTO data_quality_report VALUES
                ('run1', '2026-06-04', 'stock_daily', 'SH', 'PASS', '2026-06-04 20:00:00'),
                ('run1', '2026-06-04', 'stock_daily', 'SZ', 'PASS', '2026-06-04 20:00:00'),
                ('run1', '2026-06-04', 'stock_daily', 'BSE', 'PASS', '2026-06-04 20:00:00');
            INSERT INTO strategy_run_log VALUES ('mrgc_v1', '2026-06-04', 'success', 0, '2026-06-04 20:10:00');
            INSERT INTO stock_daily_gap_exception VALUES
                ('600355', '2026-06-04', 'SH', 'tdx_no_raw_data', 'tdx_mootdx_qfq', 1, 'accepted_no_raw_data');
            INSERT INTO pipeline_run_log VALUES
                ('old_run', 'daily_data_workflow', 'daily', 'running', '2026-06-04 00:00:00', NULL, NULL, NULL, NULL, '/tmp/old.log'),
                ('new_run', 'daily_data_workflow', 'daily', 'running', '2026-06-05 11:00:00', NULL, NULL, NULL, NULL, '/tmp/new.log');
            """
        )


def test_health_support_coverage_does_not_pull_core_down(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    status_dir = tmp_path / "status"
    _create_status_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategy_run_log VALUES (
                'mrgc_v1', '2026-06-03', 'success', 10, '2026-06-05 09:00:00'
            )
            """
        )
        conn.commit()
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(local_skill, "STATUS_DIR", status_dir)
    monkeypatch.setattr(local_skill, "LATEST_STATUS_JSON", status_dir / "latest.json")
    monkeypatch.setattr(local_skill, "LATEST_STATUS_MD", status_dir / "latest.md")
    monkeypatch.setattr(local_skill, "DEEP_HEALTH_CACHE_PATH", status_dir / "deep-health.json")

    local_skill.write_status_report(
        mode="health",
        target="2026-06-04",
        exit_code=0,
        preflight="ok",
        include_gap_summary=False,
    )

    payload = json.loads((status_dir / "latest.json").read_text())
    assert payload["core_ok"] is True
    assert payload["support_coverage"]["etf_daily_prices"]["missing"] == 1
    assert payload["support_coverage"]["quant_feature_etf_rps"]["missing"] == 1
    assert "etf_daily_prices" in payload["support_stale"]
    assert "quant_feature_etf_rps" in payload["support_stale"]
    assert payload["accepted_gap_exceptions"][0]["count"] == 1
    assert payload["stale_pipeline_runs"][0]["run_id"] == "old_run"
    assert "backup_dir" in payload["storage_summary"]
    assert payload["previous_day_complete"] is True
    assert payload["recent_pending_dates"] == []
    assert payload["market_data_ready"] is True
    assert payload["analytics_ready"] is True
    assert payload["known_legacy_anomalies"][0]["trade_date"] == "2026-07-03"
    assert "tdx_server_state" in payload


def test_light_health_skips_historical_and_anomaly_scans(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    status_dir = tmp_path / "status"
    _create_status_db(db_path)
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(local_skill, "STATUS_DIR", status_dir)
    monkeypatch.setattr(local_skill, "LATEST_STATUS_JSON", status_dir / "latest.json")
    monkeypatch.setattr(local_skill, "LATEST_STATUS_MD", status_dir / "latest.md")
    monkeypatch.setattr(local_skill, "DEEP_HEALTH_CACHE_PATH", status_dir / "deep-health.json")

    def should_not_run(*args, **kwargs):
        pytest.fail("light health must not run historical or anomaly scans")

    monkeypatch.setattr(local_skill, "stock_daily_history_health", should_not_run)
    monkeypatch.setattr(local_skill, "data_anomaly_health", should_not_run)

    local_skill.write_status_report(
        mode="health",
        target="2026-06-04",
        exit_code=0,
        preflight="ok",
    )

    payload = json.loads((status_dir / "latest.json").read_text())
    assert payload["historical_health"]["status"] == "not_checked"
    assert payload["historical_health"]["checked_at"] is None
    assert payload["stock_daily_history_health"] is None
    assert payload["data_anomaly_health"] is None
    assert payload["history_ok"] is None
    assert payload["data_anomaly_ok"] is None


def test_deep_health_scans_and_persists_historical_cache(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    status_dir = tmp_path / "status"
    cache_path = status_dir / "deep-health.json"
    _create_status_db(db_path)
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(local_skill, "STATUS_DIR", status_dir)
    monkeypatch.setattr(local_skill, "LATEST_STATUS_JSON", status_dir / "latest.json")
    monkeypatch.setattr(local_skill, "LATEST_STATUS_MD", status_dir / "latest.md")
    monkeypatch.setattr(local_skill, "DEEP_HEALTH_CACHE_PATH", cache_path)
    calls = []
    history = {"ok": True, "method": "test_history"}
    anomaly = {"ok": True, "method": "test_anomaly"}

    def history_scan(conn, target):
        calls.append(("history", target))
        return history

    def anomaly_scan(conn, target):
        calls.append(("anomaly", target))
        return anomaly

    monkeypatch.setattr(local_skill, "stock_daily_history_health", history_scan)
    monkeypatch.setattr(local_skill, "data_anomaly_health", anomaly_scan)

    local_skill.write_status_report(
        mode="health",
        target="2026-06-04",
        exit_code=0,
        preflight="ok",
        deep_health=True,
    )

    payload = json.loads((status_dir / "latest.json").read_text())
    cached = json.loads(cache_path.read_text())
    assert calls == [("history", "2026-06-04"), ("anomaly", "2026-06-04")]
    assert payload["historical_health"]["status"] == "fresh"
    assert payload["historical_health"]["checked_at"]
    assert payload["stock_daily_history_health"] == history
    assert payload["data_anomaly_health"] == anomaly
    assert cached["target"] == "2026-06-04"
    assert cached["stock_daily_history_health"] == history
    assert cached["data_anomaly_health"] == anomaly


def test_light_health_marks_deep_cache_stale_without_rescanning(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    status_dir = tmp_path / "status"
    cache_path = status_dir / "deep-health.json"
    _create_status_db(db_path)
    status_dir.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "target": "2026-06-03",
                "checked_at": "2026-06-01 00:00:00",
                "stock_daily_history_health": {"ok": True, "source": "cache"},
                "data_anomaly_health": {"ok": True, "source": "cache"},
            }
        )
    )
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(local_skill, "STATUS_DIR", status_dir)
    monkeypatch.setattr(local_skill, "LATEST_STATUS_JSON", status_dir / "latest.json")
    monkeypatch.setattr(local_skill, "LATEST_STATUS_MD", status_dir / "latest.md")
    monkeypatch.setattr(local_skill, "DEEP_HEALTH_CACHE_PATH", cache_path)

    def should_not_run(*args, **kwargs):
        pytest.fail("light health must use the deep-health cache")

    monkeypatch.setattr(local_skill, "stock_daily_history_health", should_not_run)
    monkeypatch.setattr(local_skill, "data_anomaly_health", should_not_run)

    local_skill.write_status_report(
        mode="health",
        target="2026-06-04",
        exit_code=0,
        preflight="ok",
    )

    payload = json.loads((status_dir / "latest.json").read_text())
    assert payload["historical_health"]["status"] == "stale"
    assert payload["historical_health"]["checked_at"] == "2026-06-01 00:00:00"
    assert payload["historical_health"]["stale"] is True
    assert payload["historical_health"]["stale_reason"] == "target_changed"
    assert payload["stock_daily_history_health"] is None
    assert payload["data_anomaly_health"] is None
    assert payload["history_ok"] is None
    assert payload["data_anomaly_ok"] is None


def test_light_health_invalidates_same_target_cache_after_stock_rows_change(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "local_quant.db"
    status_dir = tmp_path / "status"
    cache_path = status_dir / "deep-health.json"
    _create_status_db(db_path)
    status_dir.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "version": 2,
                "target": "2026-06-04",
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "input_signature": {
                    "target": "2026-06-04",
                    "stock_daily_latest": "2026-06-04",
                    "stock_daily_target_rows": 0,
                },
                "stock_daily_history_health": {"ok": False, "source": "old-cache"},
                "data_anomaly_health": {"ok": True, "source": "old-cache"},
            }
        )
    )
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(local_skill, "STATUS_DIR", status_dir)
    monkeypatch.setattr(local_skill, "LATEST_STATUS_JSON", status_dir / "latest.json")
    monkeypatch.setattr(local_skill, "LATEST_STATUS_MD", status_dir / "latest.md")
    monkeypatch.setattr(local_skill, "DEEP_HEALTH_CACHE_PATH", cache_path)

    local_skill.write_status_report(
        mode="health",
        target="2026-06-04",
        exit_code=0,
        preflight="ok",
    )

    payload = json.loads((status_dir / "latest.json").read_text())
    assert payload["historical_health"]["status"] == "stale"
    assert payload["historical_health"]["stale_reason"] == "stock_daily_changed"
    assert payload["stock_daily_history_health"] is None
    assert payload["history_ok"] is None


def test_lock_busy_deep_health_uses_light_path(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    status_dir = tmp_path / "status"
    _create_status_db(db_path)
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(local_skill, "STATUS_DIR", status_dir)
    monkeypatch.setattr(local_skill, "LATEST_STATUS_JSON", status_dir / "latest.json")
    monkeypatch.setattr(local_skill, "LATEST_STATUS_MD", status_dir / "latest.md")
    monkeypatch.setattr(local_skill, "DEEP_HEALTH_CACHE_PATH", status_dir / "deep-health.json")
    monkeypatch.setattr(local_skill, "latest_completed_trading_date", lambda: "2026-06-04")
    monkeypatch.setattr(local_skill, "workflow_lock_is_available", lambda: False)
    monkeypatch.setattr(local_skill, "read_lock_metadata", lambda: {"pid": 123})
    monkeypatch.setattr(local_skill, "check_disk_space", lambda min_free_gb: None)

    def should_not_run(*args, **kwargs):
        pytest.fail("lock-busy health must not run database checks or deep scans")

    monkeypatch.setattr(local_skill, "check_database", should_not_run)
    monkeypatch.setattr(local_skill, "stock_daily_history_health", should_not_run)
    monkeypatch.setattr(local_skill, "data_anomaly_health", should_not_run)
    monkeypatch.setattr(local_skill, "run_retention_maintenance", should_not_run)

    code = local_skill.write_lightweight_health_status(
        "2026-06-04",
        min_free_gb=0,
        deep_health=True,
    )

    payload = json.loads((status_dir / "latest.json").read_text())
    assert code == 0
    assert payload["preflight"] == "workflow_running"
    assert payload["historical_health"]["status"] == "not_checked"


def test_holiday_daily_skips_backup_and_workflow(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_local_data_skill.py", "--mode", "daily"])
    monkeypatch.setattr(local_skill, "latest_completed_trading_date", lambda: "2026-09-24")
    monkeypatch.setattr(local_skill, "is_a_share_session", lambda _day: False, raising=False)
    monkeypatch.setattr(local_skill, "write_lightweight_health_status", lambda target, _free: 0)
    monkeypatch.setattr(
        local_skill,
        "ensure_database_backup",
        lambda *_args, **_kwargs: pytest.fail("holiday must not create a backup"),
    )
    monkeypatch.setattr(
        local_skill,
        "run_command",
        lambda *_args, **_kwargs: pytest.fail("holiday must not run a workflow"),
    )

    assert local_skill.main() == 0


def test_explicit_exchange_holiday_is_not_a_completed_target(monkeypatch):
    monkeypatch.setattr(local_skill, "latest_completed_trading_date", lambda: "2026-09-28")
    with pytest.raises(RuntimeError, match="not an A-share trading date"):
        local_skill.validate_target_is_completed("2026-09-25")


def test_health_internal_core_gap_pulls_core_down(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    status_dir = tmp_path / "status"
    _create_status_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO stock_daily_prices VALUES ('000001', '2026-06-03')")
        conn.execute("INSERT INTO stock_valuation_daily VALUES ('000001', '2026-06-03')")
        conn.commit()
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(local_skill, "STATUS_DIR", status_dir)
    monkeypatch.setattr(local_skill, "LATEST_STATUS_JSON", status_dir / "latest.json")
    monkeypatch.setattr(local_skill, "LATEST_STATUS_MD", status_dir / "latest.md")
    monkeypatch.setattr(local_skill, "DEEP_HEALTH_CACHE_PATH", status_dir / "deep-health.json")

    local_skill.write_status_report(
        mode="health",
        target="2026-06-04",
        exit_code=0,
        preflight="ok",
        include_gap_summary=False,
    )

    payload = json.loads((status_dir / "latest.json").read_text())
    assert payload["previous_day_complete"] is True
    assert payload["recent_window_complete"] is False
    assert payload["analytics_ready"] is False
    assert payload["core_ok"] is False
    assert payload["recent_pending_dates"] == [
        {
            "trade_date": "2026-06-03",
            "missing": [
                "quant_feature_rps",
                "quant_feature_sector_rps",
                "quant_feature_etf_rps",
                "market_strength_daily",
                "industry_strength_daily",
                "structure_health_daily",
                "stock_lifecycle_state",
                "mrgc_v1",
            ],
        }
    ]
    assert "quant_feature_rps" in payload["critical_feature_stale"]
    assert "quant_feature_sector_rps" in payload["critical_feature_stale"]
    assert "market_strength_daily" in payload["critical_feature_stale"]
    assert "industry_strength_daily" in payload["critical_feature_stale"]
    assert "structure_health_daily" in payload["critical_feature_stale"]
    assert "stock_lifecycle_state" in payload["critical_feature_stale"]
    assert "mrgc_v1" in payload["critical_feature_stale"]


def test_stock_daily_history_health_flags_thin_historical_dates(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE trading_calendar (
                trade_date TEXT,
                market TEXT,
                is_open INTEGER
            );
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                market TEXT,
                list_date TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                market TEXT
            );
            INSERT INTO trading_calendar VALUES
                ('2026-06-03', 'SH', 1),
                ('2026-06-03', 'SZ', 1),
                ('2026-06-04', 'SH', 1),
                ('2026-06-04', 'SZ', 1);
            INSERT INTO stock_info VALUES
                ('600001', 'SH', '2020-01-01'),
                ('600002', 'SH', '2020-01-01'),
                ('000001', 'SZ', '');
            INSERT INTO stock_daily_prices VALUES
                ('600001', '2026-06-03', 'SH'),
                ('000001', '2026-06-03', 'SZ'),
                ('600001', '2026-06-04', 'SH'),
                ('600002', '2026-06-04', 'SH'),
                ('000001', '2026-06-04', 'SZ');
            """
        )

        health = local_skill.stock_daily_history_health(
            conn,
            "2026-06-04",
            min_market_ratio=0.75,
        )

    assert health["ok"] is False
    assert health["coverage_ok"] is False
    assert health["metadata_ok"] is False
    assert health["stock_info_list_date_missing"] == 1
    assert health["list_date_missing_symbols"] == [
        {"symbol": "000001", "name": None, "market": "SZ"}
    ]
    assert health["low_coverage_date_count"] == 1
    assert health["low_coverage_samples"][0]["trade_date"] == "2026-06-03"
    assert health["low_coverage_samples"][0]["market"] == "SH"


def test_data_anomaly_health_flags_rps_price_and_snapshot_issues(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE quant_feature_rps (
                symbol TEXT,
                trade_date TEXT,
                rps_250 REAL
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
                pct_chg REAL
            );
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                total_mv REAL,
                circ_mv REAL
            );
            CREATE TABLE sector_list (
                sector_name TEXT,
                sector_type TEXT
            );
            CREATE TABLE sector_daily_prices (
                sector_name TEXT,
                trade_date TEXT
            );

            INSERT INTO quant_feature_rps VALUES
                ('000001', '2026-07-08', 10),
                ('000001', '2026-07-09', 95),
                ('000001', '2026-07-10', 94);

            INSERT INTO stock_daily_prices VALUES
                ('000002', '2026-07-09', 10, 10, 10, 10, 1000, 10000, 1.0),
                ('000002', '2026-07-10', 11, 11, 11, 11, 1000, 11000, NULL),
                ('000003', '2026-07-10', 1262.99, 1263.0, 1252.55, 1256.0, 2786012, 3500590592, 0),
                ('000004', '2026-07-10', 1262.99, 1263.0, 1252.55, 1256.0, 2786012, 3500590592, 0);

            INSERT INTO stock_valuation_daily VALUES
                ('000001', '2026-07-10', 0, 0),
                ('000002', '2026-07-10', 0, 0);

            INSERT INTO sector_list VALUES ('银行', 'Industry');
            INSERT INTO sector_daily_prices VALUES
                ('银行', '2026-07-10'),
                ('旧行业', '2026-07-10');
            """
        )
        conn.row_factory = sqlite3.Row

        health = local_skill.data_anomaly_health(
            conn,
            "2026-07-10",
            lookback_days=10,
        )

    assert health["ok"] is False
    assert health["rps_250_large_jump_count"] == 1
    assert health["rps_250_large_jump_samples"][0]["symbol"] == "000001"
    assert health["duplicate_high_price_ohlcv_count"] == 1
    assert health["pct_chg_null_with_prev_count"] == 1
    assert health["bad_valuation_snapshot_count"] == 1
    assert health["legacy_sector_row_count"] == 1


def test_status_history_queries_use_trade_date_index_and_keep_timestamp_rows(tmp_path):
    db_path = tmp_path / "history_query_plan.db"
    conn = sqlite3.connect(db_path, factory=_RecordingConnection)
    try:
        conn.executescript(
            """
            CREATE TABLE trading_calendar (
                trade_date TEXT,
                market TEXT,
                is_open INTEGER
            );
            CREATE INDEX idx_calendar_trade_date ON trading_calendar(trade_date);
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                market TEXT,
                list_date TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                market TEXT
            );
            CREATE INDEX idx_stock_daily_trade_date ON stock_daily_prices(trade_date);
            INSERT INTO trading_calendar VALUES
                ('2026-06-03 00:00:00', 'SH', 1),
                ('2026-06-04 00:00:00', 'SH', 1);
            INSERT INTO stock_info VALUES ('600001', 'SH', '2020-01-01');
            INSERT INTO stock_daily_prices VALUES
                ('600001', '2026-06-03 15:00:00', 'SH'),
                ('600001', '2026-06-04', 'SH'),
                ('600002', '2026-06-05', 'SH');
            """
        )
        conn.row_factory = sqlite3.Row
        conn.executed.clear()

        health = local_skill.stock_daily_history_health(conn, "2026-06-04")

        assert health["start_date"] == "2026-06-03"
        assert health["end_date"] == "2026-06-04"
        assert health["trading_days"] == 2
        assert health["target_market_counts"] == {"SH": 1}
        source_queries = [
            (sql, parameters)
            for sql, parameters in conn.executed
            if "FROM stock_daily_prices" in sql and "WHERE" in sql
        ]
        assert source_queries
        for sql, parameters in source_queries:
            plan = conn.execute("EXPLAIN QUERY PLAN " + sql, parameters).fetchall()
            detail = " | ".join(str(row[3]) for row in plan)
            assert "SEARCH stock_daily_prices USING INDEX idx_stock_daily_trade_date" in detail
    finally:
        conn.close()


def test_status_history_handles_open_calendar_with_no_price_rows(tmp_path):
    db_path = tmp_path / "empty_history.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE trading_calendar (trade_date TEXT, is_open INTEGER);
            CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT);
            INSERT INTO trading_calendar VALUES ('2026-06-04 00:00:00', 1);
            """
        )

        health = local_skill.stock_daily_history_health(conn, "2026-06-04")

    assert health["ok"] is False
    assert health["zero_row_date_count"] == 1
    assert health["target_market_counts"] == {}


def test_status_anomaly_queries_use_source_trade_date_indexes(tmp_path):
    db_path = tmp_path / "anomaly_query_plan.db"
    conn = sqlite3.connect(db_path, factory=_RecordingConnection)
    try:
        conn.executescript(
            """
            CREATE TABLE quant_feature_rps (
                symbol TEXT,
                trade_date TEXT,
                rps_250 REAL
            );
            CREATE INDEX idx_rps_trade_date ON quant_feature_rps(trade_date);
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                pct_chg REAL
            );
            CREATE INDEX idx_stock_daily_trade_date ON stock_daily_prices(trade_date);
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                total_mv REAL,
                circ_mv REAL
            );
            CREATE INDEX idx_valuation_trade_date ON stock_valuation_daily(trade_date);
            INSERT INTO quant_feature_rps VALUES
                ('000001', '2026-07-09 15:00:00', 10),
                ('000001', '2026-07-10 15:00:00', 95),
                ('000001', '2026-07-11', 1);
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-07-09 15:00:00', 10, 10, 10, 10, 1000, 10000, 1),
                ('000001', '2026-07-10 15:00:00', 11, 11, 11, 11, 1000, 11000, NULL),
                ('000002', '2026-07-11', 999, 999, 999, 999, 9999, 9999, NULL);
            INSERT INTO stock_valuation_daily VALUES
                ('000001', '2026-07-10 15:00:00', 0, 0),
                ('000002', '2026-07-09', 0, 0);
            """
        )
        conn.row_factory = sqlite3.Row
        conn.executed.clear()

        health = local_skill.data_anomaly_health(conn, "2026-07-10", lookback_days=10)

        assert health["rps_250_large_jump_count"] == 1
        assert health["pct_chg_null_with_prev_count"] == 1
        assert health["bad_valuation_snapshot_count"] == 1
        expected_indexes = {
            "quant_feature_rps": "idx_rps_trade_date",
            "stock_daily_prices": "idx_stock_daily_trade_date",
            "stock_valuation_daily": "idx_valuation_trade_date",
        }
        for table_name, index_name in expected_indexes.items():
            source_queries = [
                (sql, parameters)
                for sql, parameters in conn.executed
                if f"FROM {table_name}" in sql and "WHERE" in sql
            ]
            assert source_queries, table_name
            for sql, parameters in source_queries:
                plan = conn.execute("EXPLAIN QUERY PLAN " + sql, parameters).fetchall()
                detail = " | ".join(str(row[3]) for row in plan)
                assert f"SEARCH {table_name} USING INDEX {index_name}" in detail
    finally:
        conn.close()


def test_workflow_liveness_reports_long_active_stage(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE pipeline_run_log (
                run_id TEXT PRIMARY KEY,
                mode TEXT,
                status TEXT,
                started_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO pipeline_run_log VALUES ('daily_test', 'daily', 'running', '2026-06-05 08:00:00')"
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        monkeypatch.setattr(local_skill, "process_exists", lambda pid: True)

        liveness = local_skill.workflow_liveness(
            conn,
            lock_metadata={
                "pid": 12345,
                "run_id": "daily_test",
                "mode": "daily",
                "target_trade_date": "2026-06-05",
                "stage": "rps_features",
                "stage_started_at": "2026-06-05 10:00:00",
                "heartbeat_at": "2026-06-05 10:00:00",
            },
            now=datetime(2026, 6, 5, 11, 31, 0),
        )

    assert liveness["active"] is True
    assert liveness["stage"] == "rps_features"
    assert liveness["stage_elapsed_seconds"] == 5460.0
    assert liveness["total_elapsed_seconds"] == 12660.0
    assert liveness["long_running_warning"] is True


def test_stock_info_list_date_records_and_apply(tmp_path):
    sh = pd.DataFrame(
        [
            {"证券代码": "600000", "证券简称": "浦发银行", "上市日期": "1999-11-10"},
            {"证券代码": "600004", "证券简称": "白云机场", "上市日期": "2003-04-28"},
        ]
    )
    sz = pd.DataFrame(
        [{"A股代码": "000001", "A股简称": "平安银行", "A股上市日期": "1991-04-03"}]
    )
    bse = pd.DataFrame(
        [{"证券代码": "920000", "证券简称": "安徽凤凰", "上市日期": "2020-12-23"}]
    )
    records = (
        list_dates.listing_records_from_frame(sh, market="SH", source="sh")
        + list_dates.listing_records_from_frame(sz, market="SZ", source="sz")
        + list_dates.listing_records_from_frame(bse, market="BSE", source="bse")
    )

    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                list_date TEXT,
                market TEXT,
                industry TEXT
            );
            INSERT INTO stock_info VALUES
                ('600000', '浦发银行旧名', NULL, 'SZ', '银行'),
                ('000001', '平安银行', '', 'SZ', '银行'),
                ('920000', '安徽凤凰', '2020-12-23', 'BSE', '汽车');
            """
        )

    summary = list_dates.apply_listing_records(db_path, records, insert_missing=True)

    with sqlite3.connect(db_path) as conn:
        rows = {
            row[0]: row
            for row in conn.execute(
                "SELECT symbol, name, list_date, market, industry FROM stock_info ORDER BY symbol"
            )
        }
    assert summary["records"] == 4
    assert summary["inserted"] == 1
    assert summary["updated_list_date"] == 2
    assert summary["updated_market"] == 1
    assert rows["600000"] == ("600000", "浦发银行旧名", "1999-11-10", "SH", "银行")
    assert rows["000001"][2] == "1991-04-03"
    assert rows["600004"] == ("600004", "白云机场", "2003-04-28", "SH", None)


def test_fetch_listing_records_includes_star_and_delisted_sources(monkeypatch):
    calls = []

    def stock_info_sh_name_code(*, symbol):
        calls.append(("stock_info_sh_name_code", symbol))
        code = "688001" if symbol == "科创板" else "600000"
        return pd.DataFrame(
            [{"证券代码": code, "证券简称": code, "上市日期": "2019-07-22"}]
        )

    def stock_info_sh_delist(*, symbol):
        calls.append(("stock_info_sh_delist", symbol))
        return pd.DataFrame(
            [{"公司代码": "600355", "公司简称": "*ST精伦", "上市日期": "2002-06-13"}]
        )

    def stock_info_sz_name_code(*, symbol):
        calls.append(("stock_info_sz_name_code", symbol))
        return pd.DataFrame(
            [{"A股代码": "000001", "A股简称": "平安银行", "A股上市日期": "1991-04-03"}]
        )

    def stock_info_sz_delist(*, symbol):
        calls.append(("stock_info_sz_delist", symbol))
        return pd.DataFrame(
            [{"证券代码": "000638", "证券简称": "*ST万方", "上市日期": "1996-11-26"}]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_info_sh_name_code=stock_info_sh_name_code,
            stock_info_sh_delist=stock_info_sh_delist,
            stock_info_sz_name_code=stock_info_sz_name_code,
            stock_info_sz_delist=stock_info_sz_delist,
        ),
    )

    records, errors = list_dates.fetch_listing_records(["SH", "SZ"])

    assert errors == []
    assert {record.symbol for record in records} == {
        "600000",
        "688001",
        "600355",
        "000001",
        "000638",
    }
    assert calls == [
        ("stock_info_sh_name_code", "主板A股"),
        ("stock_info_sh_name_code", "科创板"),
        ("stock_info_sh_delist", "全部"),
        ("stock_info_sz_name_code", "A股列表"),
        ("stock_info_sz_delist", "终止上市公司"),
    ]


def test_stock_info_list_date_dry_run_is_readonly_when_table_missing(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")

    summary = list_dates.apply_listing_records(
        db_path,
        [
            list_dates.ListingRecord(
                symbol="600000",
                name="浦发银行",
                list_date="1999-11-10",
                market="SH",
                source="test",
            )
        ],
        dry_run=True,
        insert_missing=True,
    )

    with sqlite3.connect(db_path) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stock_info'"
        ).fetchone()
    assert summary["inserted"] == 1
    assert table is None


def test_stock_info_list_date_ignores_unknown_symbols_by_default(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE stock_info (symbol TEXT PRIMARY KEY, name TEXT, list_date TEXT, market TEXT)"
        )

    summary = list_dates.apply_listing_records(
        db_path,
        [
            list_dates.ListingRecord(
                symbol="600001",
                name="历史退市股票",
                list_date="1990-01-01",
                market="SH",
                source="test",
            )
        ],
    )

    with sqlite3.connect(db_path) as conn:
        row_count = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
    assert summary["inserted"] == 0
    assert summary["ignored_missing"] == 1
    assert row_count == 0


def test_critical_feature_stale_detects_sector_rps_and_mrgc(tmp_path):
    db_path = tmp_path / "local_quant.db"
    _create_status_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM quant_feature_sector_rps")
        conn.execute("INSERT INTO quant_feature_sector_rps VALUES ('银行', '2026-06-03')")
        conn.execute("DELETE FROM strategy_run_log")
        conn.commit()
        conn.row_factory = sqlite3.Row
        stale = local_skill.critical_feature_stale(
            conn,
            target="2026-06-04",
            stock_latest="2026-06-04",
        )

    assert stale == ["quant_feature_sector_rps", "mrgc_v1"]


def test_early_verification_skips_rps_and_strategy_requirements(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    _create_status_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM quant_feature_rps")
        conn.execute("DELETE FROM strategy_run_log")
        conn.commit()
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)

    result = local_skill.verify_after_run(
        "2026-06-04",
        require_rps=False,
        require_strategy=False,
    )

    assert result.ok
    assert "RPS freshness skipped for early mode" in result.messages
    assert "MRGC audit skipped for early mode" in result.messages


def test_verification_accepts_repaired_sector_target_when_newer_data_exists(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT);
            CREATE TABLE quant_feature_rps (symbol TEXT, trade_date TEXT);
            CREATE TABLE quant_feature_sector_rps_v2 (sector_name TEXT, trade_date TEXT);
            CREATE TABLE sector_composite_daily (
                sector_name TEXT, trade_date TEXT, method_version TEXT
            );
            CREATE TABLE data_quality_report (
                trade_date TEXT, module TEXT, market TEXT, status TEXT, created_at TEXT
            );
            CREATE TABLE strategy_run_log (
                strategy_name TEXT, trade_date TEXT, status TEXT, finished_at TEXT
            );

            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-06-04'), ('000001', '2026-06-05');
            INSERT INTO quant_feature_rps VALUES ('000001', '2026-06-04');
            INSERT INTO quant_feature_sector_rps_v2 VALUES ('银行', '2026-06-04');
            INSERT INTO sector_composite_daily VALUES
                ('银行', '2026-06-04', 'ew_qfq_v1'),
                ('银行', '2026-06-05', 'ew_qfq_v1');
            INSERT INTO data_quality_report VALUES
                ('2026-06-04', 'stock_daily', 'SH', 'PASS', '2026-06-05 09:00:00'),
                ('2026-06-04', 'stock_daily', 'SZ', 'PASS', '2026-06-05 09:00:00'),
                ('2026-06-04', 'stock_daily', 'BSE', 'PASS', '2026-06-05 09:00:00'),
                ('2026-06-04', 'sector_composite', 'ALL', 'PASS', '2026-06-05 09:00:00');
            INSERT INTO strategy_run_log VALUES
                ('mrgc_v1', '2026-06-04', 'success', '2026-06-05 09:05:00');
            """
        )
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)

    result = local_skill.verify_after_run("2026-06-04")

    assert result.ok
    assert any("sector composite verification ok" in item for item in result.messages)

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE quant_feature_rps SET trade_date='2026-06-05'")
    missing_target = local_skill.verify_after_run("2026-06-04")
    assert not missing_target.ok
    assert any("RPS freshness failed" in item for item in missing_target.messages)


def test_mark_stale_pipeline_runs_only_after_expiry(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    _create_status_db(db_path)
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)

    marked = local_skill.mark_stale_pipeline_runs(
        now=datetime(2026, 6, 5, 12, 30, 0),
        stale_hours=12,
    )

    with sqlite3.connect(db_path) as conn:
        statuses = dict(conn.execute("SELECT run_id, status FROM pipeline_run_log").fetchall())
    assert marked == 1
    assert statuses["old_run"] == "stale_abandoned"
    assert statuses["new_run"] == "running"


def test_pipeline_run_records_wrapper_phase_timings(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE pipeline_run_log (
                run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO pipeline_run_log VALUES "
            "('morning_test', 'morning', 'success', '2026-07-15 09:00:01')"
        )
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)

    updated = local_skill.record_pipeline_phase_timings(
        mode="morning",
        wrapper_started_at="2026-07-15 09:00:00",
        preflight_seconds=1.2,
        backup_seconds=2.3,
        workflow_seconds=30.4,
        verification_seconds=3.5,
        total_elapsed_seconds=37.4,
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT preflight_seconds, backup_seconds, workflow_seconds,
                   verification_seconds, total_elapsed_seconds
            FROM pipeline_run_log WHERE run_id = 'morning_test'
            """
        ).fetchone()
    assert updated == "morning_test"
    assert row == (1.2, 2.3, 30.4, 3.5, 37.4)


def test_lightweight_health_reports_stale_runs_without_mutating_db(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    status_dir = tmp_path / "status"
    _create_status_db(db_path)
    monkeypatch.setattr(local_skill, "DB_PATH", db_path)
    monkeypatch.setattr(local_skill, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(local_skill, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(local_skill, "STATUS_DIR", status_dir)
    monkeypatch.setattr(local_skill, "LATEST_STATUS_JSON", status_dir / "latest.json")
    monkeypatch.setattr(local_skill, "LATEST_STATUS_MD", status_dir / "latest.md")
    monkeypatch.setattr(local_skill, "DEEP_HEALTH_CACHE_PATH", status_dir / "deep-health.json")
    monkeypatch.setattr(local_skill, "latest_completed_trading_date", lambda: "2026-06-04")
    monkeypatch.setattr(local_skill, "workflow_lock_is_available", lambda: True)
    monkeypatch.setattr(local_skill, "check_disk_space", lambda min_free_gb: None)

    code = local_skill.write_lightweight_health_status("2026-06-04", min_free_gb=0)

    with sqlite3.connect(db_path) as conn:
        statuses = dict(conn.execute("SELECT run_id, status FROM pipeline_run_log").fetchall())
    payload = json.loads((status_dir / "latest.json").read_text())
    assert code == 0
    assert statuses["old_run"] == "running"
    assert payload["stale_pipeline_runs"][0]["run_id"] == "old_run"


def test_strategy_run_log_records_zero_signal_success(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'strategy.db'}")

    record_strategy_run(
        engine,
        run_id="strategy_mrgc_v1_2026-05-28_test",
        strategy_name="mrgc_v1",
        trade_date="2026-05-28",
        status="success",
        signal_count=0,
        error_message=None,
        started_at="2026-05-28 16:10:00",
        finished_at="2026-05-28 16:10:01",
    )

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT strategy_name, trade_date, status, signal_count, error_message
            FROM strategy_run_log
        """)).mappings().one()

    assert row["strategy_name"] == "mrgc_v1"
    assert row["trade_date"] == "2026-05-28"
    assert row["status"] == "success"
    assert row["signal_count"] == 0
    assert row["error_message"] is None


def test_three_group_delivery_only_current_verified_automatic_session(monkeypatch):
    now = datetime(2026, 9, 30, 16, 0)
    monkeypatch.setattr(local_skill, "is_a_share_session", lambda day: True)
    command = local_skill.build_three_group_post_command("daily", "2026-09-30", now=now)
    assert "quant_engine.watch.three_group_daily" in command
    assert command[-3:] == ["--mode", "deliver", "--send"]
    for mode in ("morning", "catchup", "health"):
        assert local_skill.build_three_group_post_command(mode, "2026-09-30", now=now) == []
    assert local_skill.build_three_group_post_command("daily", "2026-09-29", now=now) == []
    assert local_skill.build_three_group_post_command("daily", "2026-09-30", explicit_target=True, now=now) == []
    assert local_skill.build_three_group_post_command("daily", "2026-09-30", skip_notifications=True, now=now) == []
    monkeypatch.setattr(local_skill, "is_a_share_session", lambda day: False)
    assert local_skill.build_three_group_post_command("retry", "2026-09-30", now=now) == []
