#!/usr/bin/env python3
"""Local no-LLM runner for EvoAlpha Data Core daily automation."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.run_daily_data_workflow import (
    latest_completed_trading_date, latest_table_date, stock_kline_freshness,
)
from data_core.market_calendar import is_a_share_session
from scripts.repair_stock_daily_gaps import scan_stock_daily_gaps
from data_job.common.network_utils import sanitize_direct_env
from data_core.quality import (
    MODULE_SECTOR_COMPOSITE,
    MODULE_SECTOR_RPS,
    MODULE_SECTOR_VENDOR,
    SECTOR_COMPOSITE_TABLE,
    SECTOR_RPS_TABLES,
    SECTOR_VENDOR_TABLE,
    _sector_universe_count,
)


DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
LOCK_PATH = BACKEND_DIR / "data" / ".daily_data_workflow.lock"
BACKUP_DIR = BACKEND_DIR / "data" / "backups"
STATUS_DIR = BACKEND_DIR / "logs" / "data_core_status"
LATEST_STATUS_JSON = STATUS_DIR / "latest.json"
LATEST_STATUS_MD = STATUS_DIR / "DATA_CORE_DAILY_STATUS.md"
DEEP_HEALTH_CACHE_PATH = STATUS_DIR / "deep_health_cache.json"
TDX_SERVER_STATE_PATH = (
    BACKEND_DIR / "data" / "collection_progress" / "tdx_server_state.json"
)
MIN_FREE_GB = 5
BACKUP_RETENTION = 1
# Keep one directly restorable local rollback snapshot across automatic and
# manual labels.  Git history is a separate code rollback mechanism.
BACKUP_TOTAL_RETENTION = 1
MANUAL_BACKUP_RETENTION_DAYS = 7
MANUAL_BACKUP_RETENTION = 1
BACKUP_GZIP_COMPRESSLEVEL = 1
WORKFLOW_LOG_COMPRESS_DAYS = 7
WORKFLOW_LOG_DELETE_DAYS = 30
STALE_PIPELINE_HOURS = 12
LAUNCHD_LOG_MAX_BYTES = 50 * 1024 * 1024
LAUNCHD_LOG_TAIL_BYTES = 5 * 1024 * 1024
STALE_LOCK_SECONDS = 20 * 60
WORKFLOW_TIMEOUT_EXIT_CODE = 124
HISTORY_COVERAGE_WARNING_RATIO = 0.70
WORKFLOW_STAGE_WARN_SECONDS = 30 * 60
WORKFLOW_TOTAL_WARN_SECONDS = 3 * 60 * 60
DEEP_HEALTH_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
AUTO_BACKUP_RE = re.compile(
    r"^local_quant_(?P<target>\d{4}-\d{2}-\d{2})_\d{8}_\d{6}\.db(?:\.gz)?$"
)

DEFAULT_WORKFLOW_TIMEOUT_MINUTES = {
    "morning": 25,
    "early": 120,
    "daily": 240,
    "retry": 150,
    "catchup": 360,
}

MODEL_ENV_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GLM_API_KEY",
    "BIGMODEL_API_KEY",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "QWEN_API_KEY",
)

MUTATING_MODES = {"morning", "early", "daily", "retry", "catchup"}


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    messages: tuple[str, ...]


@dataclass(frozen=True)
class BackupResult:
    path: Path
    reused: bool
    method: str


class WorkflowLockBusy(RuntimeError):
    def __init__(self, metadata: dict[str, object]):
        self.metadata = metadata
        super().__init__(f"daily data workflow lock is already held: {metadata or '-'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run EvoAlpha Data Core locally without LLM calls")
    parser.add_argument(
        "--mode",
        choices=["morning", "early", "daily", "retry", "health", "catchup"],
        default="daily",
        help=(
            "morning/early/daily/retry/catchup run the workflow; "
            "health writes a lightweight status report"
        ),
    )
    parser.add_argument("--target", help="target completed trading date, e.g. 2026-05-28")
    parser.add_argument(
        "--pending-days",
        type=int,
        default=7,
        help="recent trading dates inspected by morning repair",
    )
    parser.add_argument(
        "--time-budget-minutes",
        type=int,
        default=25,
        help="morning task-boundary time budget",
    )
    parser.add_argument(
        "--deep-health",
        action="store_true",
        help="run weekly SQLite/history/retention checks without data workflow writes",
    )
    parser.add_argument("--dry-run", action="store_true", help="print checks and command without running")
    parser.add_argument(
        "--skip-notifications", action="store_true",
        help="run data and calculations without notification dispatch or retries",
    )
    parser.add_argument("--min-free-gb", type=int, default=MIN_FREE_GB, help="minimum free disk space")
    parser.add_argument(
        "--lock-wait-minutes",
        type=int,
        help="minutes to wait for an existing workflow lock; retry defaults to 45",
    )
    parser.add_argument(
        "--backup-retention",
        type=int,
        default=BACKUP_RETENTION,
        help="number of target-date auto database backups to keep",
    )
    parser.add_argument(
        "--manual-backup-retention-days",
        type=int,
        default=MANUAL_BACKUP_RETENTION_DAYS,
        help="days to keep manual/repair database backups",
    )
    parser.add_argument(
        "--manual-backup-retention",
        type=int,
        default=MANUAL_BACKUP_RETENTION,
        help="maximum manual/repair database backups to keep after age pruning",
    )
    parser.add_argument(
        "--workflow-timeout-minutes",
        type=int,
        help="maximum workflow runtime for mutating modes; 0 disables the timeout",
    )
    return parser


def local_only_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a subprocess environment that cannot call configured model APIs."""
    env = sanitize_direct_env(dict(os.environ if base_env is None else base_env))
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(BACKEND_DIR) if not pythonpath else f"{BACKEND_DIR}:{pythonpath}"
    env["EVOALPHA_LOCAL_ONLY"] = "1"
    env["EVOALPHA_DISABLE_LLM"] = "1"
    env["APP_DEBUG"] = "false"
    for key in MODEL_ENV_KEYS:
        env.pop(key, None)
    return env


def resolve_target(target: str | None) -> str:
    return target or latest_completed_trading_date()


def validate_target_is_completed(target: str) -> None:
    try:
        target_day = date.fromisoformat(target)
    except ValueError as exc:
        raise RuntimeError(f"invalid target date: {target}") from exc
    if not is_a_share_session(target_day):
        raise RuntimeError(f"target {target} is not an A-share trading date")
    latest_completed = latest_completed_trading_date()
    if target > latest_completed:
        raise RuntimeError(
            f"target {target} is not a completed trading date yet; latest_completed={latest_completed}"
        )


def check_database(*, quick_check: bool = True) -> None:
    if not DB_PATH.exists():
        raise RuntimeError(f"database not found: {DB_PATH}")

    if not quick_check:
        return

    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        result = conn.execute("PRAGMA quick_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {result[0] if result else 'no result'}")


def check_disk_space(min_free_gb: int) -> None:
    free_bytes = shutil.disk_usage(BACKEND_DIR).free
    free_gb = free_bytes / (1024**3)
    if free_gb < min_free_gb:
        raise RuntimeError(f"free disk space too low: {free_gb:.2f}GB < {min_free_gb}GB")


def read_lock_metadata() -> dict[str, object]:
    if not LOCK_PATH.exists():
        return {}
    try:
        text = LOCK_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def process_exists(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def heartbeat_age_seconds(metadata: dict[str, object]) -> float | None:
    heartbeat = metadata.get("heartbeat_at")
    if not isinstance(heartbeat, str) or not heartbeat:
        return None
    try:
        timestamp = datetime.strptime(heartbeat, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return (datetime.now() - timestamp).total_seconds()


def _metadata_timestamp_age_seconds(
    metadata: dict[str, object],
    key: str,
    *,
    now: datetime | None = None,
) -> float | None:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        timestamp = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return ((now or datetime.now()) - timestamp).total_seconds()


def metadata_is_stale(metadata: dict[str, object], stale_seconds: int = STALE_LOCK_SECONDS) -> bool:
    if not metadata:
        return False
    if process_exists(metadata.get("pid")):
        return False
    age = heartbeat_age_seconds(metadata)
    return age is None or age >= stale_seconds


def clear_stale_lock_file(lock_file) -> bool:
    try:
        lock_file.seek(0)
        text = lock_file.read().strip()
        metadata = json.loads(text) if text else {}
        if not isinstance(metadata, dict) or not metadata_is_stale(metadata):
            return False
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.flush()
        return True
    except Exception:
        return False


def workflow_lock_is_available() -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            clear_stale_lock_file(lock_file)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return True
        except BlockingIOError as exc:
            return False


def wait_for_workflow_lock(wait_seconds: int) -> None:
    deadline = time.time() + max(0, wait_seconds)
    while True:
        if workflow_lock_is_available():
            return
        metadata = read_lock_metadata()
        if time.time() >= deadline:
            raise WorkflowLockBusy(metadata)
        print(f"workflow_lock=busy wait_seconds=60 metadata={metadata or '-'}", flush=True)
        time.sleep(min(60, max(1, deadline - time.time())))


def default_lock_wait_minutes(mode: str) -> int:
    if mode in {"morning", "early"}:
        return 5
    if mode == "daily":
        return 10
    if mode == "retry":
        return 45
    return 0


def default_workflow_timeout_minutes(mode: str) -> int:
    return DEFAULT_WORKFLOW_TIMEOUT_MINUTES.get(mode, 0)


def run_preflight(
    target: str,
    min_free_gb: int,
    lock_wait_seconds: int,
    require_lock_available: bool = True,
    quick_database_check: bool = False,
) -> None:
    validate_target_is_completed(target)
    check_database(quick_check=quick_database_check)
    check_disk_space(min_free_gb)
    if require_lock_available:
        wait_for_workflow_lock(lock_wait_seconds)


def checkpoint_wal(db_path: Path) -> bool:
    """Checkpoint WAL only when no writer is holding the database."""
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            row = conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
        return bool(row and int(row[0]) == 0)
    except sqlite3.Error:
        return False


def try_apfs_clone(source: Path, target: Path) -> bool:
    """Create an APFS clonefile using the native cp implementation."""
    completed = subprocess.run(
        ["/bin/cp", "-c", str(source), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and target.exists()


def sqlite_online_backup(source: Path, target: Path) -> None:
    source_uri = f"file:{source}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=30) as source_conn:
        with sqlite3.connect(target) as target_conn:
            source_conn.backup(target_conn, pages=4096, sleep=0.05)


def create_database_snapshot(source: Path, target: Path) -> str:
    """Create one consistent snapshot and return the method used."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(".tmp")
    temp_path.unlink(missing_ok=True)
    checkpoint_ok = checkpoint_wal(source)
    try:
        if checkpoint_ok and try_apfs_clone(source, temp_path):
            method = "apfs_clone"
        else:
            temp_path.unlink(missing_ok=True)
            sqlite_online_backup(source, temp_path)
            method = "sqlite_online_backup"
        temp_path.replace(target)
        return method
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _latest_auto_backup(target: str, backup_dir: Path = BACKUP_DIR) -> Path | None:
    matches = sorted(
        [
            path
            for path in backup_dir.glob(f"local_quant_{target}_*.db*")
            if AUTO_BACKUP_RE.match(path.name)
        ]
        if backup_dir.exists()
        else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def ensure_database_backup(
    target: str,
    retention: int = BACKUP_RETENTION,
    *,
    total_retention: int | None = BACKUP_TOTAL_RETENTION,
    manual_retention_days: int = MANUAL_BACKUP_RETENTION_DAYS,
    manual_retention: int = MANUAL_BACKUP_RETENTION,
    force_fresh: bool = False,
    manual_label: str | None = None,
) -> BackupResult:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    existing = None if force_fresh or manual_label else _latest_auto_backup(target, BACKUP_DIR)
    if existing is not None:
        return BackupResult(path=existing, reused=True, method="existing")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if manual_label:
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", manual_label)
        backup_path = BACKUP_DIR / f"local_quant_before_{safe_label}_{target}_{timestamp}.db"
    else:
        backup_path = BACKUP_DIR / f"local_quant_{target}_{timestamp}.db"
    method = create_database_snapshot(DB_PATH, backup_path)
    prune_backups(
        retention,
        total_retention=total_retention,
        manual_retention_days=manual_retention_days,
        manual_retention=manual_retention,
    )
    return BackupResult(path=backup_path, reused=False, method=method)


def backup_database(
    target: str,
    retention: int = BACKUP_RETENTION,
    *,
    manual_retention_days: int = MANUAL_BACKUP_RETENTION_DAYS,
    manual_retention: int = MANUAL_BACKUP_RETENTION,
) -> Path:
    """Backward-compatible path-only wrapper for repair callers."""
    return ensure_database_backup(
        target,
        retention,
        manual_retention_days=manual_retention_days,
        manual_retention=manual_retention,
    ).path


def _auto_backup_group(path: Path) -> str:
    match = AUTO_BACKUP_RE.match(path.name)
    return match.group("target") if match else path.name


def prune_backups(
    retention: int = BACKUP_RETENTION,
    *,
    backup_dir: Path = BACKUP_DIR,
    total_retention: int | None = None,
    manual_retention_days: int = MANUAL_BACKUP_RETENTION_DAYS,
    manual_retention: int = MANUAL_BACKUP_RETENTION,
    now: datetime | None = None,
) -> dict[str, int]:
    """Prune snapshots by normal labels and, optionally, by a global cap.

    ``total_retention`` is used by production maintenance so ad-hoc repair
    names such as ``local_quant_volume_repair_*.db`` cannot escape pruning.
    The default remains ``None`` for callers that need the legacy split policy.
    """
    summary = {"auto_deleted": 0, "manual_deleted": 0}
    if total_retention is not None:
        summary["total_deleted"] = 0
    if not backup_dir.exists():
        return summary

    if retention > 0:
        auto_backups = [
            path
            for path in backup_dir.glob("local_quant_*.db*")
            if path.is_file() and AUTO_BACKUP_RE.match(path.name)
        ]
        latest_by_group: dict[str, Path] = {}
        for backup in auto_backups:
            group = _auto_backup_group(backup)
            current = latest_by_group.get(group)
            if current is None or backup.stat().st_mtime > current.stat().st_mtime:
                latest_by_group[group] = backup

        retained_groups = sorted(
            latest_by_group.items(),
            key=lambda item: item[1].stat().st_mtime,
            reverse=True,
        )[:retention]
        retained_paths = {path for _, path in retained_groups}

        for backup in auto_backups:
            if backup in retained_paths:
                continue
            backup.unlink(missing_ok=True)
            summary["auto_deleted"] += 1

    if manual_retention_days > 0 or manual_retention > 0:
        manual_backups = sorted(
            [
                path
                for path in {
                    *backup_dir.glob("local_quant_before_*.db*"),
                    *backup_dir.glob("local_quant.*.db*"),
                }
                if path.name.endswith((".db", ".db.gz"))
            ],
            key=lambda path: path.stat().st_mtime,
        )
        retained_manual: list[Path] = []
        cutoff = (now or datetime.now()) - timedelta(days=manual_retention_days)
        for backup in manual_backups:
            if manual_retention_days > 0 and datetime.fromtimestamp(backup.stat().st_mtime) < cutoff:
                backup.unlink(missing_ok=True)
                summary["manual_deleted"] += 1
            else:
                retained_manual.append(backup)

        if manual_retention > 0:
            for backup in retained_manual[:-manual_retention]:
                backup.unlink(missing_ok=True)
                summary["manual_deleted"] += 1

    if total_retention is not None:
        keep_count = max(1, int(total_retention))
        all_backups = _database_backup_files(backup_dir)
        retained_paths = set(all_backups[-keep_count:])
        for backup in all_backups:
            if backup in retained_paths:
                continue
            backup.unlink(missing_ok=True)
            summary["total_deleted"] += 1
            if AUTO_BACKUP_RE.match(backup.name):
                summary["auto_deleted"] += 1
            else:
                summary["manual_deleted"] += 1

        # SQLite snapshots are self-contained; sidecars from interrupted
        # copies are never needed for restore and only consume disk.
        for pattern in ("local_quant*.db-wal", "local_quant*.db-shm", "local_quant*.tmp"):
            for sidecar in backup_dir.glob(pattern):
                if sidecar.is_file():
                    sidecar.unlink(missing_ok=True)
                    summary["total_deleted"] += 1
    return summary


def _database_backup_files(backup_dir: Path) -> list[Path]:
    if not backup_dir.exists():
        return []
    return sorted(
        {
            *backup_dir.glob("local_quant_*.db"),
            *backup_dir.glob("local_quant_*.db.gz"),
            *backup_dir.glob("local_quant.*.db"),
            *backup_dir.glob("local_quant.*.db.gz"),
        },
        key=lambda path: path.stat().st_mtime,
    )


def compress_archival_backups(
    *,
    backup_dir: Path = BACKUP_DIR,
    compresslevel: int = BACKUP_GZIP_COMPRESSLEVEL,
) -> dict[str, int]:
    """Keep the newest snapshot directly restorable and gzip older retained snapshots."""
    summary = {"compressed": 0, "bytes_freed": 0}
    backups = _database_backup_files(backup_dir)
    if len(backups) < 2:
        return summary

    newest = backups[-1]
    for path in backups:
        if path == newest or path.suffix == ".gz":
            continue

        source_stat = path.stat()
        gz_path = path.with_suffix(path.suffix + ".gz")
        temp_path = gz_path.with_suffix(gz_path.suffix + ".tmp")
        temp_path.unlink(missing_ok=True)
        try:
            with path.open("rb") as source, gzip.open(
                temp_path,
                "wb",
                compresslevel=max(1, min(9, int(compresslevel))),
            ) as target:
                shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
            with gzip.open(temp_path, "rb") as archived:
                if archived.read(16) != b"SQLite format 3\x00":
                    raise ValueError(f"compressed backup has invalid SQLite header: {path}")
            temp_path.replace(gz_path)
            os.utime(gz_path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            path.unlink()
            summary["compressed"] += 1
            summary["bytes_freed"] += max(0, source_stat.st_size - gz_path.stat().st_size)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    return summary


def prune_workflow_logs(
    *,
    log_dir: Path = BACKEND_DIR / "logs",
    now: datetime | None = None,
    compress_days: int = WORKFLOW_LOG_COMPRESS_DAYS,
    delete_days: int = WORKFLOW_LOG_DELETE_DAYS,
) -> dict[str, int]:
    summary = {"compressed": 0, "deleted": 0}
    if not log_dir.exists():
        return summary

    current = now or datetime.now()
    compress_cutoff = current - timedelta(days=compress_days)
    delete_cutoff = current - timedelta(days=delete_days)

    for path in log_dir.glob("daily_data_workflow_*.log"):
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if mtime < delete_cutoff:
            path.unlink(missing_ok=True)
            summary["deleted"] += 1
            continue
        if mtime < compress_cutoff:
            gz_path = path.with_suffix(path.suffix + ".gz")
            with path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            path.unlink(missing_ok=True)
            summary["compressed"] += 1

    for path in log_dir.glob("daily_data_workflow_*.log.gz"):
        if datetime.fromtimestamp(path.stat().st_mtime) < delete_cutoff:
            path.unlink(missing_ok=True)
            summary["deleted"] += 1
    return summary


def trim_launchd_logs_if_idle(
    *,
    launchd_log_dir: Path = BACKEND_DIR / "logs" / "launchd",
    is_idle: bool,
    max_bytes: int = LAUNCHD_LOG_MAX_BYTES,
    tail_bytes: int = LAUNCHD_LOG_TAIL_BYTES,
) -> dict[str, int]:
    summary = {"trimmed": 0}
    if not is_idle or not launchd_log_dir.exists():
        return summary

    marker = b"[... truncated by EvoAlpha retention maintenance ...]\n"
    for path in launchd_log_dir.glob("com.evoalpha.datacore.*.log"):
        if path.stat().st_size <= max_bytes:
            continue
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - tail_bytes))
            tail = handle.read()
        with path.open("wb") as handle:
            handle.write(marker)
            handle.write(tail)
        summary["trimmed"] += 1
    return summary


def run_retention_maintenance(
    retention: int = BACKUP_RETENTION,
    *,
    manual_retention_days: int = MANUAL_BACKUP_RETENTION_DAYS,
    manual_retention: int = MANUAL_BACKUP_RETENTION,
) -> dict[str, object]:
    backup_summary = prune_backups(
        retention,
        total_retention=BACKUP_TOTAL_RETENTION,
        manual_retention_days=manual_retention_days,
        manual_retention=manual_retention,
    )
    return {
        "backups": backup_summary,
        "backup_compression": compress_archival_backups(),
        "workflow_logs": prune_workflow_logs(),
        "launchd_logs": trim_launchd_logs_if_idle(is_idle=workflow_lock_is_available()),
    }


def sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def sqlite_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not sqlite_table_exists(conn, table_name):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def stale_pipeline_runs(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    stale_hours: int = STALE_PIPELINE_HOURS,
) -> list[dict[str, object]]:
    if not sqlite_table_exists(conn, "pipeline_run_log"):
        return []

    cutoff = (now or datetime.now()) - timedelta(hours=stale_hours)
    rows = conn.execute(
        """
        SELECT run_id, mode, started_at, log_path
        FROM pipeline_run_log
        WHERE status = 'running'
          AND datetime(started_at) <= datetime(?)
        ORDER BY started_at
        """,
        (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()
    return [dict(row) for row in rows]


def workflow_liveness(
    conn: sqlite3.Connection,
    *,
    lock_metadata: dict[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    metadata = dict(lock_metadata if lock_metadata is not None else read_lock_metadata())
    active = bool(metadata and process_exists(metadata.get("pid")))
    heartbeat_age = _metadata_timestamp_age_seconds(metadata, "heartbeat_at", now=now)
    stage_elapsed = _metadata_timestamp_age_seconds(
        metadata,
        "stage_started_at" if metadata.get("stage_started_at") else "heartbeat_at",
        now=now,
    )
    started_at = None
    total_elapsed = None
    run_id = metadata.get("run_id")
    if run_id and sqlite_table_exists(conn, "pipeline_run_log"):
        row = conn.execute(
            """
            SELECT started_at
            FROM pipeline_run_log
            WHERE run_id = ?
            LIMIT 1
            """,
            (str(run_id),),
        ).fetchone()
        if row and row[0]:
            started_at = str(row[0])
            try:
                total_elapsed = ((now or datetime.now()) - datetime.strptime(
                    started_at,
                    "%Y-%m-%d %H:%M:%S",
                )).total_seconds()
            except ValueError:
                total_elapsed = None

    stale_lock = bool(metadata and not active and metadata_is_stale(metadata))
    long_running = bool(
        active
        and (
            (stage_elapsed is not None and stage_elapsed >= WORKFLOW_STAGE_WARN_SECONDS)
            or (total_elapsed is not None and total_elapsed >= WORKFLOW_TOTAL_WARN_SECONDS)
        )
    )
    return {
        "active": active,
        "pid": metadata.get("pid"),
        "run_id": metadata.get("run_id"),
        "mode": metadata.get("mode"),
        "target_trade_date": metadata.get("target_trade_date"),
        "stage": metadata.get("stage"),
        "stage_started_at": metadata.get("stage_started_at"),
        "heartbeat_at": metadata.get("heartbeat_at"),
        "heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
        "stage_elapsed_seconds": round(stage_elapsed, 1) if stage_elapsed is not None else None,
        "started_at": started_at,
        "total_elapsed_seconds": round(total_elapsed, 1) if total_elapsed is not None else None,
        "stale_lock": stale_lock,
        "long_running_warning": long_running,
    }


def mark_stale_pipeline_runs(
    *,
    now: datetime | None = None,
    stale_hours: int = STALE_PIPELINE_HOURS,
) -> int:
    current = now or datetime.now()
    cutoff = current - timedelta(hours=stale_hours)
    with sqlite3.connect(DB_PATH) as conn:
        if not sqlite_table_exists(conn, "pipeline_run_log"):
            return 0
        cursor = conn.execute(
            """
            UPDATE pipeline_run_log
            SET status = 'stale_abandoned',
                finished_at = ?,
                elapsed_seconds = ROUND((julianday(?) - julianday(started_at)) * 86400, 2),
                critical_failed_tasks = COALESCE(critical_failed_tasks, ''),
                failed_tasks = CASE
                    WHEN failed_tasks IS NULL OR failed_tasks = ''
                    THEN 'stale_abandoned'
                    ELSE failed_tasks || ',stale_abandoned'
                END
            WHERE status = 'running'
              AND datetime(started_at) <= datetime(?)
            """,
            (
                current.strftime("%Y-%m-%d %H:%M:%S"),
                current.strftime("%Y-%m-%d %H:%M:%S"),
                cutoff.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    return int(cursor.rowcount or 0)


def record_pipeline_phase_timings(
    *,
    mode: str,
    wrapper_started_at: str,
    preflight_seconds: float,
    backup_seconds: float,
    workflow_seconds: float,
    verification_seconds: float,
    total_elapsed_seconds: float,
) -> str | None:
    """Attach wrapper phase timings to the workflow row created by the child process."""
    timing_columns = {
        "preflight_seconds": "REAL",
        "backup_seconds": "REAL",
        "workflow_seconds": "REAL",
        "verification_seconds": "REAL",
        "total_elapsed_seconds": "REAL",
    }
    with sqlite3.connect(DB_PATH) as conn:
        if not sqlite_table_exists(conn, "pipeline_run_log"):
            return None
        existing = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(pipeline_run_log)").fetchall()
        }
        for column, ddl_type in timing_columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE pipeline_run_log ADD COLUMN {column} {ddl_type}")
        row = conn.execute(
            """
            SELECT run_id
            FROM pipeline_run_log
            WHERE mode = ? AND started_at >= ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (mode, wrapper_started_at),
        ).fetchone()
        if not row:
            return None
        run_id = str(row[0])
        conn.execute(
            """
            UPDATE pipeline_run_log
            SET preflight_seconds = ?, backup_seconds = ?, workflow_seconds = ?,
                verification_seconds = ?, total_elapsed_seconds = ?
            WHERE run_id = ?
            """,
            (
                round(preflight_seconds, 2),
                round(backup_seconds, 2),
                round(workflow_seconds, 2),
                round(verification_seconds, 2),
                round(total_elapsed_seconds, 2),
                run_id,
            ),
        )
        conn.commit()
    return run_id


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def storage_summary() -> dict[str, object]:
    logs_dir = BACKEND_DIR / "logs"
    launchd_log_dir = logs_dir / "launchd"
    backup_count = len(_database_backup_files(BACKUP_DIR))
    workflow_log_count = len(list(logs_dir.glob("daily_data_workflow_*.log")))
    values = {
        "database_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "backup_dir_bytes": _directory_size(BACKUP_DIR),
        "logs_dir_bytes": _directory_size(logs_dir),
        "launchd_logs_bytes": _directory_size(launchd_log_dir),
        "backup_count": backup_count,
        "workflow_log_count": workflow_log_count,
    }
    values["database"] = _human_size(int(values["database_bytes"]))
    values["backup_dir"] = _human_size(int(values["backup_dir_bytes"]))
    values["logs_dir"] = _human_size(int(values["logs_dir_bytes"]))
    values["launchd_logs"] = _human_size(int(values["launchd_logs_bytes"]))
    return values


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def _historical_health_not_checked() -> dict[str, object]:
    return {
        "status": "not_checked",
        "source": "deep_health",
        "target": None,
        "checked_at": None,
        "stale": False,
        "stale_reason": None,
    }


def _read_deep_health_cache(target: str) -> tuple[dict[str, object] | None, dict[str, object]]:
    """Load the last deep-health result without touching SQLite.

    A cache is deliberately considered stale when it belongs to another target
    date or is older than the bounded retention window.  The status report keeps
    the cached payload while exposing that it is not a fresh check.
    """
    try:
        cache = json.loads(DEEP_HEALTH_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None, _historical_health_not_checked()

    if not isinstance(cache, dict):
        return None, _historical_health_not_checked()
    checked_at = cache.get("checked_at")
    cached_target = cache.get("target")
    if not isinstance(checked_at, str) or not isinstance(cached_target, str):
        return None, _historical_health_not_checked()
    if not isinstance(cache.get("stock_daily_history_health"), dict):
        return None, _historical_health_not_checked()
    if not isinstance(cache.get("data_anomaly_health"), dict):
        return None, _historical_health_not_checked()

    checked_datetime = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            checked_datetime = datetime.strptime(checked_at, fmt)
            break
        except ValueError:
            continue
    if checked_datetime is None:
        return None, _historical_health_not_checked()

    age_seconds = max(0, int((datetime.now() - checked_datetime).total_seconds()))
    stale_reason = None
    if cached_target != target:
        stale_reason = "target_changed"
    elif age_seconds > DEEP_HEALTH_CACHE_MAX_AGE_SECONDS:
        stale_reason = "cache_expired"
    stale = stale_reason is not None
    metadata = {
        "status": "stale" if stale else "cached",
        "source": "deep_health",
        "target": cached_target,
        "checked_at": checked_at,
        "stale": stale,
        "stale_reason": stale_reason,
        "age_seconds": age_seconds,
    }
    return cache, metadata


def _history_cache_input_signature(
    conn: sqlite3.Connection,
    target: str,
) -> dict[str, object]:
    """Return a cheap fingerprint for data that can change coverage results."""
    latest = None
    target_rows = 0
    if sqlite_table_exists(conn, "stock_daily_prices"):
        latest_row = conn.execute(
            "SELECT MAX(DATE(trade_date)) FROM stock_daily_prices"
        ).fetchone()
        latest = str(latest_row[0]) if latest_row and latest_row[0] else None
        target_start, target_end = _iso_day_bounds(target)
        target_rows = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM stock_daily_prices
                WHERE trade_date >= ? AND trade_date < ?
                """,
                (target_start, target_end),
            ).fetchone()[0]
            or 0
        )
    return {
        "target": target,
        "stock_daily_latest": latest,
        "stock_daily_target_rows": target_rows,
    }


def _clear_stale_historical_payload(payload: dict[str, object]) -> None:
    payload["stock_daily_history_health"] = None
    payload["history_ok"] = None
    payload["list_date_missing_symbols"] = []
    payload["data_anomaly_health"] = None
    payload["data_anomaly_ok"] = None


def _iso_day_bounds(value: str) -> tuple[str, str]:
    day = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    return day.strftime("%Y-%m-%d"), (day + timedelta(days=1)).strftime("%Y-%m-%d")


def _target_coverage(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    entity_column: str,
    date_column: str,
    expected: int,
    target: str,
) -> dict[str, object]:
    actual = 0
    if sqlite_table_exists(conn, table_name):
        actual = conn.execute(
            f"""
            SELECT COUNT(DISTINCT {entity_column})
            FROM {table_name}
            WHERE DATE({date_column}) = DATE(?)
            """,
            (target,),
        ).fetchone()[0]
    missing = max(int(expected) - int(actual or 0), 0)
    return {
        "actual": int(actual or 0),
        "expected": int(expected),
        "missing": missing,
        "ok": missing == 0,
    }


def support_coverage(conn: sqlite3.Connection, target: str) -> dict[str, dict[str, object]]:
    etf_expected = 0
    if sqlite_table_exists(conn, "etf_info"):
        etf_expected = conn.execute("SELECT COUNT(DISTINCT symbol) FROM etf_info").fetchone()[0]
    return {
        "index_daily": _target_coverage(
            conn,
            table_name="index_daily",
            entity_column="index_code",
            date_column="trade_date",
            expected=8,
            target=target,
        ),
        "etf_daily_prices": _target_coverage(
            conn,
            table_name="etf_daily_prices",
            entity_column="symbol",
            date_column="trade_date",
            expected=etf_expected,
            target=target,
        ),
        "quant_feature_etf_rps": _target_coverage(
            conn,
            table_name="quant_feature_etf_rps",
            entity_column="symbol",
            date_column="trade_date",
            expected=etf_expected,
            target=target,
        ),
    }


def stock_daily_history_health(
    conn: sqlite3.Connection,
    target: str,
    *,
    min_market_ratio: float = HISTORY_COVERAGE_WARNING_RATIO,
    sample_limit: int = 10,
) -> dict[str, object]:
    """Return a lightweight historical K-line coverage summary.

    This intentionally avoids a full date x symbol cross join. Daily health
    should surface suspicious historical holes without turning into a slow
    full repair scan.
    """
    if not sqlite_table_exists(conn, "stock_daily_prices"):
        return {"ok": False, "reason": "stock_daily_prices_missing"}

    target_start, target_end = _iso_day_bounds(target)
    calendar_table_exists = sqlite_table_exists(conn, "trading_calendar")
    if calendar_table_exists:
        start_date, end_date, trading_days = conn.execute(
            """
            SELECT MIN(DATE(trade_date)), MAX(DATE(trade_date)), COUNT(DISTINCT DATE(trade_date))
            FROM trading_calendar
            WHERE is_open = 1
              AND trade_date < ?
            """,
            (target_end,),
        ).fetchone()
    else:
        start_date, end_date, trading_days = conn.execute(
            """
            SELECT MIN(DATE(trade_date)), MAX(DATE(trade_date)), COUNT(DISTINCT DATE(trade_date))
            FROM stock_daily_prices
            WHERE trade_date < ?
            """,
            (target_end,),
        ).fetchone()

    if not start_date or not end_date:
        return {"ok": False, "reason": "no_historical_trading_dates"}

    stock_daily_columns = sqlite_table_columns(conn, "stock_daily_prices")
    inferred_market_expr = (
        "CASE "
        "WHEN symbol LIKE '6%' THEN 'SH' "
        "WHEN symbol LIKE '8%' OR symbol LIKE '9%' THEN 'BSE' "
        "ELSE 'SZ' "
        "END"
    )
    market_expr = (
        f"COALESCE(market, {inferred_market_expr})"
        if "market" in stock_daily_columns
        else inferred_market_expr
    )

    info_by_market: dict[str, dict[str, int]] = {}
    list_date_missing_total = 0
    stock_info_total = 0
    list_date_missing_symbols: list[dict[str, object]] = []
    stock_info_columns: set[str] = set()
    symbol_column = "symbol"
    if sqlite_table_exists(conn, "stock_info"):
        stock_info_columns = sqlite_table_columns(conn, "stock_info")
        symbol_column = "symbol" if "symbol" in stock_info_columns else "code"
        missing_expr = (
            "SUM(CASE WHEN list_date IS NULL OR list_date = '' THEN 1 ELSE 0 END)"
            if "list_date" in stock_info_columns
            else "COUNT(*)"
        )
        for market, total, missing in conn.execute(
            f"""
            SELECT COALESCE(market, 'UNKNOWN') AS market,
                   COUNT(*) AS total,
                   {missing_expr} AS missing
            FROM stock_info
            GROUP BY COALESCE(market, 'UNKNOWN')
            ORDER BY market
            """
        ):
            total_int = int(total or 0)
            missing_int = int(missing or 0)
            stock_info_total += total_int
            list_date_missing_total += missing_int
            info_by_market[str(market)] = {
                "symbols": total_int,
                "list_date_missing": missing_int,
            }
        if symbol_column in stock_info_columns and "list_date" in stock_info_columns:
            name_expr = "name" if "name" in stock_info_columns else "NULL"
            market_expr_for_sample = "market" if "market" in stock_info_columns else "'UNKNOWN'"
            list_date_missing_symbols = [
                {
                    "symbol": str(row[0]).zfill(6),
                    "name": row[1],
                    "market": row[2],
                }
                for row in conn.execute(
                    f"""
                    SELECT {symbol_column}, {name_expr}, {market_expr_for_sample}
                    FROM stock_info
                    WHERE list_date IS NULL OR list_date = ''
                    ORDER BY {market_expr_for_sample}, {symbol_column}
                    LIMIT ?
                    """,
                    (sample_limit,),
                ).fetchall()
            ]

    target_counts = {
        str(market): int(count or 0)
        for market, count in conn.execute(
            f"""
            SELECT {market_expr} AS market,
                   COUNT(*) AS count
            FROM stock_daily_prices
            WHERE trade_date >= ?
              AND trade_date < ?
            GROUP BY {market_expr}
            """,
            (target_start, target_end),
        )
    }
    if not target_counts:
        latest_date = conn.execute(
            "SELECT MAX(DATE(trade_date)) FROM stock_daily_prices WHERE trade_date < ?",
            (target_end,),
        ).fetchone()[0]
        if latest_date:
            latest_start, latest_end = _iso_day_bounds(str(latest_date))
            target_counts = {
                str(market): int(count or 0)
                for market, count in conn.execute(
                    f"""
                    SELECT {market_expr} AS market,
                           COUNT(*) AS count
                    FROM stock_daily_prices
                    WHERE trade_date >= ?
                      AND trade_date < ?
                    GROUP BY {market_expr}
                    """,
                    (latest_start, latest_end),
                )
            }

    _, history_end = _iso_day_bounds(str(end_date))
    rows = conn.execute(
        f"""
        SELECT DATE(trade_date) AS trade_date,
               {market_expr} AS market,
               COUNT(*) AS actual
        FROM stock_daily_prices
        WHERE trade_date >= ?
          AND trade_date < ?
        GROUP BY DATE(trade_date), {market_expr}
        """,
        (start_date, history_end),
    ).fetchall()

    observed_dates = {str(row[0]) for row in rows if row[0]}
    if calendar_table_exists:
        calendar_dates = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT DATE(trade_date)
                FROM trading_calendar
                WHERE is_open = 1
                  AND trade_date >= ?
                  AND trade_date < ?
                """,
                (start_date, history_end),
            )
            if row[0]
        }
    else:
        calendar_dates = observed_dates

    zero_row_dates = sorted(calendar_dates - observed_dates)
    calendar_date_list = sorted(calendar_dates)
    actual_counts = {
        (str(trade_date), str(market)): int(actual or 0)
        for trade_date, market, actual in rows
    }
    expected_counts: dict[tuple[str, str], int] = {}
    if sqlite_table_exists(conn, "stock_info") and symbol_column in stock_info_columns:
        market_select = (
            "market"
            if "market" in stock_info_columns
            else "NULL"
        )
        list_date_select = (
            "list_date"
            if "list_date" in stock_info_columns
            else "NULL"
        )
        stock_rows = conn.execute(
            f"""
            SELECT {symbol_column}, {market_select}, {list_date_select}
            FROM stock_info
            """
        ).fetchall()

        def _parse_list_date(value: object) -> datetime.date | None:
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            if len(text) == 8 and "-" not in text:
                text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
            try:
                return datetime.strptime(text[:10], "%Y-%m-%d").date()
            except ValueError:
                return None

        def _infer_market(symbol: object, market: object) -> str:
            text = str(market or "").strip().upper()
            if text in {"SH", "SZ", "BSE"}:
                return text
            symbol_text = str(symbol or "").zfill(6)
            if symbol_text.startswith("6"):
                return "SH"
            if symbol_text.startswith(("4", "8", "9")):
                return "BSE"
            return "SZ"

        stock_rows_normalized = [
            (
                _infer_market(symbol, market),
                _parse_list_date(list_date),
            )
            for symbol, market, list_date in stock_rows
        ]
        for trade_date in calendar_date_list:
            try:
                trade_day = datetime.strptime(trade_date, "%Y-%m-%d").date()
            except ValueError:
                continue
            for market, list_date in stock_rows_normalized:
                if list_date is None or list_date <= trade_day:
                    key = (trade_date, market)
                    expected_counts[key] = expected_counts.get(key, 0) + 1

    if expected_counts:
        target_expected_counts = {
            market: count
            for (trade_date, market), count in expected_counts.items()
            if trade_date == target
        }
        if not target_expected_counts:
            target_expected_counts = {
                market: count
                for (trade_date, market), count in expected_counts.items()
                if trade_date == end_date
            }
        if target_expected_counts:
            target_counts = target_expected_counts

    low_rows: list[dict[str, object]] = []
    min_by_market: dict[str, dict[str, object]] = {}
    low_date_set: set[str] = set()
    if expected_counts:
        coverage_items = (
            (trade_date, market, actual_counts.get((trade_date, market), 0), expected)
            for (trade_date, market), expected in expected_counts.items()
        )
    else:
        coverage_items = (
            (
                str(trade_date),
                str(market),
                int(actual or 0),
                int(target_counts.get(str(market), 0) or 0),
            )
            for trade_date, market, actual in rows
        )

    for trade_date, market_key, actual_int, baseline in coverage_items:
        baseline = int(baseline or 0)
        if baseline <= 0:
            continue
        actual_int = int(actual_int or 0)
        ratio = actual_int / baseline
        current_min = min_by_market.get(market_key)
        if current_min is None or ratio < float(current_min["ratio"]):
            min_by_market[market_key] = {
                "trade_date": str(trade_date),
                "actual": actual_int,
                "baseline": baseline,
                "ratio": round(ratio, 4),
            }
        if ratio < min_market_ratio:
            low_date_set.add(str(trade_date))
            low_rows.append(
                {
                    "trade_date": str(trade_date),
                    "market": market_key,
                    "actual": actual_int,
                    "baseline": baseline,
                    "ratio": round(ratio, 4),
                }
            )

    low_rows.sort(key=lambda item: (float(item["ratio"]), item["trade_date"], item["market"]))
    metadata_ok = stock_info_total == 0 or list_date_missing_total == 0
    coverage_ok = not zero_row_dates and not low_rows
    return {
        "ok": bool(metadata_ok and coverage_ok),
        "coverage_ok": bool(coverage_ok),
        "metadata_ok": bool(metadata_ok),
        "method": "daily_market_row_count_vs_target_baseline",
        "warning_ratio": min_market_ratio,
        "start_date": start_date,
        "end_date": end_date,
        "trading_days": int(trading_days or len(calendar_dates)),
        "target_market_counts": target_counts,
        "stock_info_symbols": stock_info_total,
        "stock_info_list_date_missing": list_date_missing_total,
        "list_date_missing_symbols": list_date_missing_symbols,
        "stock_info_by_market": info_by_market,
        "zero_row_date_count": len(zero_row_dates),
        "zero_row_date_samples": zero_row_dates[:sample_limit],
        "low_coverage_date_count": len(low_date_set),
        "low_coverage_samples": low_rows[:sample_limit],
        "min_coverage_by_market": min_by_market,
    }


def accepted_gap_exceptions(conn: sqlite3.Connection, target: str) -> list[dict[str, object]]:
    if not sqlite_table_exists(conn, "stock_daily_gap_exception"):
        return []
    rows = conn.execute(
        """
        SELECT market, reason, source, status, COUNT(*) AS count
        FROM stock_daily_gap_exception
        WHERE DATE(trade_date) = DATE(?)
          AND status = 'accepted_no_raw_data'
        GROUP BY market, reason, source, status
        ORDER BY count DESC, market
        """,
        (target,),
    ).fetchall()
    return [dict(row) for row in rows]


def data_anomaly_health(
    conn: sqlite3.Connection,
    target: str,
    *,
    lookback_days: int = 90,
    sample_limit: int = 10,
) -> dict[str, object]:
    """Surface data-shape anomalies that freshness checks alone cannot catch."""
    result: dict[str, object] = {
        "ok": True,
        "lookback_days": lookback_days,
        "rps_250_large_jump_count": 0,
        "rps_250_large_jump_samples": [],
        "duplicate_high_price_ohlcv_count": 0,
        "duplicate_high_price_ohlcv_samples": [],
        "pct_chg_null_with_prev_count": 0,
        "pct_chg_null_with_prev_samples": [],
        "bad_valuation_snapshot_count": 0,
        "bad_valuation_snapshot_samples": [],
        "legacy_sector_row_count": 0,
    }
    target_day = datetime.strptime(str(target)[:10], "%Y-%m-%d")
    target_start, target_end = _iso_day_bounds(target)
    window_start = (target_day - timedelta(days=lookback_days + 10)).strftime("%Y-%m-%d")
    result_start = (target_day - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    rps_columns = sqlite_table_columns(conn, "quant_feature_rps")
    if {"symbol", "trade_date", "rps_250"}.issubset(rps_columns):
        rps_jump_query = """
            WITH r AS (
                SELECT symbol,
                       DATE(trade_date) AS trade_date,
                       rps_250,
                       LAG(rps_250) OVER (
                           PARTITION BY symbol
                           ORDER BY DATE(trade_date)
                       ) AS prev_rps_250
                FROM quant_feature_rps
                WHERE trade_date >= ?
                  AND trade_date < ?
            )
            SELECT symbol,
                   trade_date,
                   ROUND(prev_rps_250, 2) AS prev_rps_250,
                   ROUND(rps_250, 2) AS rps_250,
                   ROUND(rps_250 - prev_rps_250, 2) AS jump
            FROM r
            WHERE trade_date >= ?
              AND prev_rps_250 IS NOT NULL
              AND ABS(rps_250 - prev_rps_250) >= 50
        """
        count_query = f"SELECT COUNT(*) FROM ({rps_jump_query})"
        result["rps_250_large_jump_count"] = int(
            conn.execute(count_query, (window_start, target_end, result_start)).fetchone()[0]
            or 0
        )
        rows = conn.execute(
            f"""
            {rps_jump_query}
            ORDER BY ABS(jump) DESC, trade_date DESC, symbol
            LIMIT ?
            """,
            (window_start, target_end, result_start, sample_limit),
        ).fetchall()
        result["rps_250_large_jump_samples"] = [dict(row) for row in rows]

    stock_daily_columns = sqlite_table_columns(conn, "stock_daily_prices")
    if {
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    }.issubset(stock_daily_columns):
        duplicate_query = """
            SELECT DATE(trade_date) AS trade_date,
                   ROUND(open, 4) AS open,
                   ROUND(high, 4) AS high,
                   ROUND(low, 4) AS low,
                   ROUND(close, 4) AS close,
                   ROUND(volume, 4) AS volume,
                   ROUND(amount, 4) AS amount,
                   COUNT(*) AS count,
                   GROUP_CONCAT(symbol) AS symbols
            FROM stock_daily_prices
            WHERE trade_date >= ?
              AND trade_date < ?
              AND close >= 100
              AND COALESCE(volume, 0) >= 1000
            GROUP BY DATE(trade_date),
                     ROUND(open, 4),
                     ROUND(high, 4),
                     ROUND(low, 4),
                     ROUND(close, 4),
                     ROUND(volume, 4),
                     ROUND(amount, 4)
            HAVING COUNT(*) >= 2
        """
        result["duplicate_high_price_ohlcv_count"] = int(
            conn.execute(
                f"SELECT COUNT(*) FROM ({duplicate_query})",
                (result_start, target_end),
            ).fetchone()[0]
            or 0
        )
        rows = conn.execute(
            f"""
            {duplicate_query}
            ORDER BY trade_date DESC, count DESC
            LIMIT ?
            """,
            (result_start, target_end, sample_limit),
        ).fetchall()
        result["duplicate_high_price_ohlcv_samples"] = [dict(row) for row in rows]

    if {"symbol", "trade_date", "close", "pct_chg"}.issubset(stock_daily_columns):
        pct_null_query = """
            WITH ordered AS (
                SELECT symbol,
                       DATE(trade_date) AS trade_date,
                       close,
                       pct_chg,
                       LAG(close) OVER (
                           PARTITION BY symbol
                           ORDER BY DATE(trade_date)
                       ) AS prev_close
                FROM stock_daily_prices
                WHERE trade_date >= ?
                  AND trade_date < ?
                  AND close IS NOT NULL
                  AND close > 0
            )
            SELECT symbol,
                   trade_date,
                   close,
                   prev_close
            FROM ordered
            WHERE trade_date >= ?
              AND trade_date <> '2026-07-03'
              AND pct_chg IS NULL
              AND prev_close IS NOT NULL
              AND prev_close > 0
        """
        result["pct_chg_null_with_prev_count"] = int(
            conn.execute(
                f"SELECT COUNT(*) FROM ({pct_null_query})",
                (window_start, target_end, result_start),
            ).fetchone()[0]
            or 0
        )
        rows = conn.execute(
            f"""
            {pct_null_query}
            ORDER BY trade_date DESC, symbol
            LIMIT ?
            """,
            (window_start, target_end, result_start, sample_limit),
        ).fetchall()
        result["pct_chg_null_with_prev_samples"] = [dict(row) for row in rows]

    valuation_columns = sqlite_table_columns(conn, "stock_valuation_daily")
    if {"trade_date", "total_mv", "circ_mv"}.issubset(valuation_columns):
        valuation_query = """
            SELECT DATE(trade_date) AS trade_date,
                   COUNT(*) AS row_count,
                   SUM(
                       CASE
                           WHEN COALESCE(total_mv, 0) = 0
                            AND COALESCE(circ_mv, 0) = 0
                           THEN 1 ELSE 0
                       END
                   ) AS bad_rows
            FROM stock_valuation_daily
            WHERE trade_date >= ?
            GROUP BY DATE(trade_date)
            HAVING bad_rows > 0
               AND bad_rows >= row_count * 0.95
        """
        result["bad_valuation_snapshot_count"] = int(
            conn.execute(f"SELECT COUNT(*) FROM ({valuation_query})", (target_start,)).fetchone()[0]
            or 0
        )
        rows = conn.execute(
            f"""
            {valuation_query}
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (target_start, sample_limit),
        ).fetchall()
        result["bad_valuation_snapshot_samples"] = [dict(row) for row in rows]

    sector_daily_columns = sqlite_table_columns(conn, "sector_daily_prices")
    sector_list_columns = sqlite_table_columns(conn, "sector_list")
    if (
        "sector_name" in sector_daily_columns
        and {"sector_name", "sector_type"}.issubset(sector_list_columns)
    ):
        result["legacy_sector_row_count"] = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM sector_daily_prices p
                LEFT JOIN sector_list s
                  ON s.sector_name = p.sector_name
                 AND s.sector_type = 'Industry'
                WHERE s.sector_name IS NULL
                """
            ).fetchone()[0]
            or 0
        )

    anomaly_keys = (
        "rps_250_large_jump_count",
        "duplicate_high_price_ohlcv_count",
        "pct_chg_null_with_prev_count",
        "bad_valuation_snapshot_count",
        "legacy_sector_row_count",
    )
    result["ok"] = all(int(result.get(key) or 0) == 0 for key in anomaly_keys)
    return result


def critical_feature_stale(
    conn: sqlite3.Connection,
    *,
    target: str,
    stock_latest: str | None,
) -> list[str]:
    stale: list[str] = []
    if not stock_latest:
        stale.append("stock_daily_prices")
        return stale

    sector_rps_table = canonical_sector_rps_table(conn)
    has_sector_migration_tables = sqlite_table_exists(conn, SECTOR_COMPOSITE_TABLE) or sqlite_table_exists(
        conn, SECTOR_RPS_TABLES[0]
    )
    feature_tables = [
        "quant_feature_rps",
        sector_rps_table,
        "market_strength_daily",
        "industry_strength_daily",
        "structure_health_daily",
        "stock_lifecycle_state",
    ]
    if has_sector_migration_tables:
        feature_tables.insert(2, SECTOR_COMPOSITE_TABLE)
    for table_name in feature_tables:
        latest = _latest_date(conn, table_name, "trade_date")
        if not latest or str(latest) < str(stock_latest):
            stale.append(table_name)

    if not _strategy_audit_exists(conn, target):
        stale.append("mrgc_v1")
    return stale


def recent_pending_dates(
    conn: sqlite3.Connection,
    target: str,
    *,
    limit: int = 7,
) -> list[dict[str, object]]:
    dates: list[str] = []
    if sqlite_table_exists(conn, "trading_calendar"):
        dates = [
            str(row[0])[:10]
            for row in conn.execute(
                """
                SELECT trade_date
                FROM (
                    SELECT DISTINCT trade_date
                    FROM trading_calendar
                    WHERE is_open = 1 AND trade_date <= ?
                    ORDER BY trade_date DESC
                    LIMIT ?
                )
                ORDER BY trade_date
                """,
                (target, max(1, limit)),
            ).fetchall()
        ]
    if not dates and sqlite_table_exists(conn, "stock_daily_prices"):
        dates = [
            str(row[0])[:10]
            for row in conn.execute(
                """
                SELECT trade_date
                FROM (
                    SELECT DISTINCT trade_date
                    FROM stock_daily_prices
                    WHERE trade_date <= ?
                    ORDER BY trade_date DESC
                    LIMIT ?
                )
                ORDER BY trade_date
                """,
                (target, max(1, limit)),
            ).fetchall()
        ]
    if target not in dates:
        dates = [*dates, target]
    dates = sorted(set(dates))[-max(1, limit):]

    sector_rps_table = canonical_sector_rps_table(conn)
    required = [
        ("stock_daily_prices", "trade_date"),
        ("stock_valuation_daily", "trade_date"),
        ("quant_feature_rps", "trade_date"),
        (sector_rps_table, "trade_date"),
        ("quant_feature_etf_rps", "trade_date"),
        ("market_strength_daily", "trade_date"),
        ("industry_strength_daily", "trade_date"),
        ("structure_health_daily", "trade_date"),
        ("stock_lifecycle_state", "trade_date"),
    ]
    if sqlite_table_exists(conn, SECTOR_COMPOSITE_TABLE) or sqlite_table_exists(conn, SECTOR_RPS_TABLES[0]):
        required.insert(4, (SECTOR_COMPOSITE_TABLE, "trade_date"))
    pending: list[dict[str, object]] = []
    for trade_date in dates:
        missing = []
        for table_name, date_column in required:
            if not sqlite_table_exists(conn, table_name):
                missing.append(table_name)
                continue
            row = conn.execute(
                f"""
                SELECT 1 FROM {table_name}
                WHERE {date_column} >= ?
                  AND {date_column} < DATE(?, '+1 day')
                LIMIT 1
                """,
                (trade_date, trade_date),
            ).fetchone()
            if row is None:
                missing.append(table_name)
        if not _strategy_audit_exists(conn, trade_date):
            missing.append("mrgc_v1")
        if missing:
            pending.append({"trade_date": trade_date, "missing": missing})
    return pending


def morning_repair_summary(conn: sqlite3.Connection) -> dict[str, object] | None:
    if not sqlite_table_exists(conn, "pipeline_run_log"):
        return None
    row = conn.execute(
        """
        SELECT run_id, status, started_at, finished_at, elapsed_seconds,
               critical_failed_tasks, failed_tasks
        FROM pipeline_run_log
        WHERE mode = 'morning'
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    summary = dict(row)
    summary["task_status"] = {}
    if sqlite_table_exists(conn, "pipeline_task_log"):
        task_rows = conn.execute(
            """
            SELECT status, COUNT(*)
            FROM pipeline_task_log
            WHERE run_id = ?
            GROUP BY status
            ORDER BY status
            """,
            (summary["run_id"],),
        ).fetchall()
        summary["task_status"] = {str(item[0]): int(item[1]) for item in task_rows}
    return summary


def known_legacy_anomalies(conn: sqlite3.Connection) -> list[dict[str, object]]:
    definitions = [
        {
            "trade_date": "2026-07-03",
            "table": "stock_daily_prices",
            "issue": "pct_chg_null_at_incremental_seed_boundary",
        },
        {
            "trade_date": "2026-06-12",
            "table": "stock_valuation_daily",
            "issue": "legacy_zero_valuation_snapshot",
        },
    ]
    for item in definitions:
        table_name = str(item["table"])
        item["status"] = "accepted_legacy_not_auto_repaired"
        item["row_count"] = 0
        if not sqlite_table_exists(conn, table_name):
            continue
        columns = sqlite_table_columns(conn, table_name)
        if table_name == "stock_daily_prices":
            if "pct_chg" not in columns:
                continue
            query = "SELECT COUNT(*) FROM stock_daily_prices WHERE trade_date = ? AND pct_chg IS NULL"
        else:
            if not {"total_mv", "circ_mv"}.issubset(columns):
                continue
            query = (
                "SELECT COUNT(*) FROM stock_valuation_daily WHERE trade_date = ? "
                "AND (COALESCE(total_mv, 0) = 0 OR COALESCE(circ_mv, 0) = 0)"
            )
        item["row_count"] = int(conn.execute(query, (item["trade_date"],)).fetchone()[0] or 0)
    return definitions


def tdx_server_state() -> dict[str, object]:
    try:
        payload = json.loads(TDX_SERVER_STATE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def build_workflow_command(
    mode: str,
    target: str,
    *,
    pending_days: int = 7,
    time_budget_minutes: int = 25,
    skip_notifications: bool = False,
) -> list[str]:
    if mode == "health":
        return []

    command = [
        sys.executable,
        "scripts/run_daily_data_workflow.py",
        "--kline-end",
        target,
    ]
    if skip_notifications:
        command.append("--skip-notifications")

    if mode == "early":
        command.extend(["--skip-gap-repair", "--skip-rps", "--skip-p0", "--skip-p1"])
        return command

    if mode == "morning":
        command.extend(
            [
                "--include-strategy",
                "--morning",
                "--pending-days",
                str(max(1, pending_days)),
                "--time-budget-minutes",
                str(max(1, time_budget_minutes)),
            ]
        )
        return command

    command.append("--include-strategy")
    if mode == "retry":
        command.append("--retry")
    if mode in {"daily", "retry"}:
        command.append("--skip-gap-repair")
    if mode == "catchup":
        command.append("--force-kline")
    return command


def build_watch_post_command(
    mode: str, target: str, *, skip_notifications: bool = False,
) -> list[str]:
    """Return the best-effort post-workflow email command for this scheduler mode."""
    base = [sys.executable, "-m", "quant_engine.watch.dispatcher"]
    if skip_notifications:
        return []
    if mode == "retry":
        return [*base, "--mode", "retry"]
    return []



def build_three_group_post_command(
    mode: str, target: str, *, skip_notifications: bool = False,
    explicit_target: bool = False, now: datetime | None = None,
) -> list[str]:
    """Deliver frozen rule facts only after verified automatic current-day runs."""
    current = now or datetime.now()
    if (mode not in {"daily", "retry"} or skip_notifications or explicit_target
            or target != current.date().isoformat()
            or not is_a_share_session(current.date()) or current.hour < 15):
        return []
    return [sys.executable, "-m", "quant_engine.watch.three_group_daily",
            "--trade-date", target, "--mode", "deliver", "--send"]


def _sector_table_health(
    conn: sqlite3.Connection,
    table_name: str,
    target: str,
    *,
    module: str,
) -> dict[str, object]:
    """Return bounded, read-only health facts for one sector data track."""
    if not sqlite_table_exists(conn, table_name):
        return {
            "table": table_name,
            "available": False,
            "latest": None,
            "target": target,
            "target_rows": 0,
            "coverage_ratio": 0.0,
            "method_versions": [],
            "quality_status": None,
        }

    columns = sqlite_table_columns(conn, table_name)
    latest = _latest_date(conn, table_name, "trade_date") if "trade_date" in columns else None
    target_rows = 0
    method_versions: list[str] = []
    if {"sector_name", "trade_date"}.issubset(columns):
        target_rows = int(
            conn.execute(
                f"""
                SELECT COUNT(DISTINCT TRIM(sector_name))
                FROM {table_name}
                WHERE DATE(trade_date) = DATE(?)
                  AND sector_name IS NOT NULL
                  AND TRIM(sector_name) <> ''
                """,
                (target,),
            ).fetchone()[0]
            or 0
        )
    if "method_version" in columns:
        method_versions = [
            str(row[0])
            for row in conn.execute(
                f"""
                SELECT DISTINCT TRIM(method_version)
                FROM {table_name}
                WHERE DATE(trade_date) = DATE(?)
                  AND method_version IS NOT NULL AND TRIM(method_version) <> ''
                ORDER BY TRIM(method_version)
                """,
                (target,),
            ).fetchall()
        ]
    expected = _sector_universe_count(conn)
    coverage_ratio = target_rows / expected if expected else 0.0
    quality_status = None
    if sqlite_table_exists(conn, "data_quality_report"):
        row = conn.execute(
            """
            SELECT status
            FROM data_quality_report
            WHERE DATE(trade_date) = DATE(?) AND module = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (target, module),
        ).fetchone()
        quality_status = str(row[0]) if row and row[0] is not None else None
    return {
        "table": table_name,
        "available": True,
        "latest": latest,
        "target": target,
        "target_rows": target_rows,
        "expected_rows": expected,
        "coverage_ratio": round(coverage_ratio, 6),
        "method_versions": method_versions,
        "quality_status": quality_status,
    }


def sector_data_health(conn: sqlite3.Connection, target: str) -> dict[str, object]:
    """Summarize composite/vendor/RPS status for lightweight and deep health reports."""
    rps_table = next(
        (table_name for table_name in SECTOR_RPS_TABLES if sqlite_table_exists(conn, table_name)),
        SECTOR_RPS_TABLES[0],
    )
    return {
        "composite": _sector_table_health(
            conn,
            SECTOR_COMPOSITE_TABLE,
            target,
            module=MODULE_SECTOR_COMPOSITE,
        ),
        "vendor": _sector_table_health(
            conn,
            SECTOR_VENDOR_TABLE,
            target,
            module=MODULE_SECTOR_VENDOR,
        ),
        "rps": _sector_table_health(
            conn,
            rps_table,
            target,
            module=MODULE_SECTOR_RPS,
        ),
    }


def canonical_sector_rps_table(conn: sqlite3.Connection) -> str:
    """Choose v2 for production health, falling back only during migration."""
    if sqlite_table_exists(conn, SECTOR_RPS_TABLES[0]):
        return SECTOR_RPS_TABLES[0]
    return SECTOR_RPS_TABLES[1]


def run_command(
    command: Sequence[str],
    env: dict[str, str],
    timeout_seconds: int | None = None,
) -> int:
    print("$ " + " ".join(command), flush=True)
    process = subprocess.Popen(
        command,
        cwd=BACKEND_DIR,
        env=env,
        start_new_session=True,
    )
    try:
        return int(process.wait(timeout=timeout_seconds))
    except subprocess.TimeoutExpired:
        print(f"workflow_timeout=expired seconds={timeout_seconds}", flush=True)
        _terminate_process_group(process)
        return WORKFLOW_TIMEOUT_EXIT_CODE


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        pass

    try:
        process.wait(timeout=30)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def write_status_report(
    *,
    mode: str,
    target: str,
    exit_code: int,
    preflight: str,
    workflow_returncode: int | None = None,
    verification: VerificationResult | None = None,
    lock_metadata: dict[str, object] | None = None,
    include_gap_summary: bool = False,
    deep_health: bool = False,
) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "target": target,
        "exit_code": exit_code,
        "preflight": preflight,
        "workflow_returncode": workflow_returncode,
        "lock_metadata": lock_metadata or {},
        "verification": list(verification.messages) if verification else [],
        "workflow_failed": bool(
            exit_code != 0
            or (workflow_returncode is not None and workflow_returncode != 0)
        ),
    }
    if deep_health:
        payload["historical_health"] = {
            "status": "not_checked",
            "source": "deep_health",
            "target": target,
            "checked_at": None,
            "stale": False,
        }
        payload["stock_daily_history_health"] = None
        payload["history_ok"] = None
        payload["list_date_missing_symbols"] = []
        payload["data_anomaly_health"] = None
        payload["data_anomaly_ok"] = None
    else:
        cached_health, health_metadata = _read_deep_health_cache(target)
        payload["historical_health"] = health_metadata
        cache_usable = bool(cached_health and not health_metadata.get("stale"))
        payload["stock_daily_history_health"] = (
            cached_health.get("stock_daily_history_health") if cache_usable else None
        )
        history_health = payload["stock_daily_history_health"]
        payload["history_ok"] = (
            bool(history_health.get("ok")) if isinstance(history_health, dict) else None
        )
        payload["list_date_missing_symbols"] = (
            history_health.get("list_date_missing_symbols", [])
            if isinstance(history_health, dict)
            else []
        )
        payload["data_anomaly_health"] = (
            cached_health.get("data_anomaly_health") if cache_usable else None
        )
        anomaly_health = payload["data_anomaly_health"]
        payload["data_anomaly_ok"] = (
            bool(anomaly_health.get("ok")) if isinstance(anomaly_health, dict) else None
        )

    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            payload["stock_daily_latest"] = _latest_date(conn, "stock_daily_prices", "trade_date")
            history_input_signature = _history_cache_input_signature(conn, target)
            if not deep_health and cached_health and not health_metadata.get("stale"):
                cached_signature = cached_health.get("input_signature")
                if not isinstance(cached_signature, dict):
                    payload["historical_health"].update(
                        {
                            "status": "stale",
                            "stale": True,
                            "stale_reason": "cache_schema_outdated",
                        }
                    )
                    _clear_stale_historical_payload(payload)
                elif cached_signature != history_input_signature:
                    payload["historical_health"].update(
                        {
                            "status": "stale",
                            "stale": True,
                            "stale_reason": "stock_daily_changed",
                            "current_input_signature": history_input_signature,
                        }
                    )
                    _clear_stale_historical_payload(payload)
            payload["valuation_latest"] = _latest_date(conn, "stock_valuation_daily", "trade_date")
            payload["rps_latest"] = _latest_date(conn, "quant_feature_rps", "trade_date")
            sector_rps_table = canonical_sector_rps_table(conn)
            payload["sector_rps_table"] = sector_rps_table
            payload["sector_rps_latest"] = _latest_date(
                conn,
                sector_rps_table,
                "trade_date",
            )
            payload["legacy_sector_rps_latest"] = _latest_date(
                conn,
                "quant_feature_sector_rps",
                "trade_date",
            )
            payload["sector_data_health"] = sector_data_health(conn, target)
            payload["sector_rps_v2_latest"] = payload["sector_data_health"]["rps"].get("latest")
            sector_health = payload["sector_data_health"]
            support_latest = {
                "index_daily": _latest_date(conn, "index_daily", "trade_date"),
                "etf_daily_prices": _latest_date(conn, "etf_daily_prices", "trade_date"),
                "quant_feature_etf_rps": _latest_date(conn, "quant_feature_etf_rps", "trade_date"),
            }
            payload["support_latest"] = support_latest
            coverage = support_coverage(conn, target)
            payload["support_coverage"] = coverage
            payload["stock_daily_quality"] = _latest_stock_daily_reports(conn, target)
            if deep_health:
                history_health = stock_daily_history_health(conn, target)
                payload["stock_daily_history_health"] = history_health
                payload["history_ok"] = bool(history_health.get("ok"))
                payload["list_date_missing_symbols"] = history_health.get(
                    "list_date_missing_symbols", []
                )
                checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                payload["historical_health"] = {
                    "status": "fresh",
                    "source": "deep_health",
                    "target": target,
                    "checked_at": checked_at,
                    "stale": False,
                }
            payload["stock_daily_gap_summary"] = (
                _recent_gap_summary(target) if include_gap_summary and deep_health else None
            )
            payload["accepted_gap_exceptions"] = accepted_gap_exceptions(conn, target)
            if deep_health:
                anomaly_health = data_anomaly_health(conn, target)
                payload["data_anomaly_health"] = anomaly_health
                payload["data_anomaly_ok"] = bool(anomaly_health.get("ok"))
                try:
                    _atomic_write_text(
                        DEEP_HEALTH_CACHE_PATH,
                        json.dumps(
                            {
                                "version": 2,
                                "target": target,
                                "checked_at": payload["historical_health"]["checked_at"],
                                "input_signature": history_input_signature,
                                "stock_daily_history_health": history_health,
                                "data_anomaly_health": anomaly_health,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                except OSError as exc:
                    payload["historical_health"]["cache_write_error"] = str(exc)
            payload["stale_pipeline_runs"] = stale_pipeline_runs(conn)
            payload["workflow_liveness"] = workflow_liveness(
                conn,
                lock_metadata=lock_metadata,
            )
            row = None
            if sqlite_table_exists(conn, "strategy_run_log"):
                row = conn.execute(
                    """
                    SELECT strategy_name, trade_date, status, signal_count, finished_at
                    FROM strategy_run_log
                    WHERE strategy_name = 'mrgc_v1'
                      AND DATE(trade_date) = DATE(?)
                    ORDER BY finished_at DESC
                    LIMIT 1
                    """,
                    (target,),
                ).fetchone()
            payload["latest_strategy_run"] = dict(row) if row else None
            stock_latest = payload.get("stock_daily_latest")
            valuation_latest = payload.get("valuation_latest")
            rps_latest = payload.get("rps_latest")
            sector_rps_latest = payload.get("sector_rps_latest")
            reports = payload.get("stock_daily_quality") or {}
            strategy = payload.get("latest_strategy_run") or {}
            feature_stale = critical_feature_stale(
                conn,
                target=target,
                stock_latest=str(stock_latest) if stock_latest else None,
            )
            pending = recent_pending_dates(conn, target, limit=7)
            recent_market_missing = {
                missing
                for item in pending
                for missing in item.get("missing", [])
                if missing in {"stock_daily_prices", "stock_valuation_daily"}
            }
            recent_feature_missing = {
                missing
                for item in pending
                for missing in item.get("missing", [])
                if missing in {"quant_feature_rps", sector_rps_table, SECTOR_COMPOSITE_TABLE, "mrgc_v1"}
            }
            recent_feature_missing.update(
                missing
                for item in pending
                for missing in item.get("missing", [])
                if missing
                in {
                    "market_strength_daily",
                    "industry_strength_daily",
                    "structure_health_daily",
                    "stock_lifecycle_state",
                }
            )
            if sector_health["composite"].get("available"):
                composite = sector_health["composite"]
                sector_rps = sector_health["rps"]
                if composite.get("latest") is None or composite.get("quality_status") != "PASS":
                    recent_feature_missing.add("sector_composite_daily")
                if sector_rps.get("latest") is None or sector_rps.get("quality_status") == "FAIL":
                    recent_feature_missing.add(sector_rps_table)
            feature_stale = list(dict.fromkeys([*feature_stale, *sorted(recent_feature_missing)]))
            payload["critical_feature_stale"] = feature_stale
            market_data_ready = bool(
                stock_latest
                and str(stock_latest) >= target
                and valuation_latest
                and str(valuation_latest) >= str(stock_latest)
                and all(reports.get(market) == "PASS" for market in ("SH", "SZ", "BSE"))
                and not recent_market_missing
            )
            analytics_ready = bool(
                stock_latest
                and rps_latest
                and str(rps_latest) >= str(stock_latest)
                and sector_rps_latest
                and str(sector_rps_latest) >= str(stock_latest)
                and strategy.get("trade_date") == target
                and strategy.get("status") == "success"
                and not feature_stale
                and not recent_feature_missing
            )
            sector_core_ready = True
            if sector_health["composite"].get("available"):
                sector_core_ready = bool(
                    sector_health["composite"].get("latest")
                    and sector_health["composite"].get("quality_status") == "PASS"
                    and sector_health["rps"].get("latest")
                    and str(sector_health["rps"].get("latest")) >= str(stock_latest)
                    and sector_health["rps"].get("quality_status") in {"PASS", None}
                )
                analytics_ready = analytics_ready and sector_core_ready
            elif sqlite_table_exists(conn, SECTOR_COMPOSITE_TABLE) or sqlite_table_exists(
                conn, SECTOR_RPS_TABLES[0]
            ):
                sector_core_ready = False
                analytics_ready = False
            payload["sector_core_ready"] = sector_core_ready
            payload["sector_migration_pending"] = not bool(
                sector_health["composite"].get("available")
            )
            payload["market_data_ready"] = market_data_ready
            payload["analytics_ready"] = analytics_ready
            payload["recent_pending_dates"] = pending
            payload["previous_day_complete"] = not any(
                item.get("trade_date") == target for item in pending
            )
            payload["recent_window_complete"] = not pending
            payload["core_window_complete"] = not recent_market_missing and not recent_feature_missing
            payload["morning_repair_summary"] = morning_repair_summary(conn)
            payload["known_legacy_anomalies"] = known_legacy_anomalies(conn)
            payload["core_ok"] = bool(market_data_ready and analytics_ready)
            support_stale = []
            for name, latest in support_latest.items():
                coverage_ok = bool(coverage.get(name, {}).get("ok"))
                date_stale = bool(stock_latest and (not latest or str(latest) < str(stock_latest)))
                if date_stale or not coverage_ok:
                    support_stale.append(name)
            if any(
                "quant_feature_etf_rps" in item.get("missing", [])
                for item in pending
            ) and "quant_feature_etf_rps" not in support_stale:
                support_stale.append("quant_feature_etf_rps")
            payload["support_stale"] = support_stale
    except Exception as exc:
        payload["db_status_error"] = str(exc)

    payload["tdx_server_state"] = tdx_server_state()
    payload["storage_summary"] = storage_summary()

    _atomic_write_text(LATEST_STATUS_JSON, json.dumps(payload, ensure_ascii=False, indent=2))
    lines = [
        "# EvoAlpha Data Core Daily Status",
        "",
        f"- created_at: {payload['created_at']}",
        f"- mode: {mode}",
        f"- target: {target}",
        f"- exit_code: {exit_code}",
        f"- preflight: {preflight}",
        f"- workflow_returncode: {workflow_returncode}",
        f"- workflow_failed: {payload.get('workflow_failed')}",
        f"- core_ok: {payload.get('core_ok')}",
        f"- previous_day_complete: {payload.get('previous_day_complete')}",
        f"- recent_window_complete: {payload.get('recent_window_complete')}",
        f"- core_window_complete: {payload.get('core_window_complete')}",
        f"- recent_pending_dates: {payload.get('recent_pending_dates')}",
        f"- morning_repair_summary: {payload.get('morning_repair_summary')}",
        f"- market_data_ready: {payload.get('market_data_ready')}",
        f"- analytics_ready: {payload.get('analytics_ready')}",
        f"- stock_daily_latest: {payload.get('stock_daily_latest')}",
        f"- valuation_latest: {payload.get('valuation_latest')}",
        f"- rps_latest: {payload.get('rps_latest')}",
        f"- sector_rps_latest: {payload.get('sector_rps_latest')}",
        f"- sector_rps_v2_latest: {payload.get('sector_rps_v2_latest')}",
        f"- sector_data_health: {payload.get('sector_data_health')}",
        f"- sector_core_ready: {payload.get('sector_core_ready')}",
        f"- sector_migration_pending: {payload.get('sector_migration_pending')}",
        f"- support_latest: {payload.get('support_latest')}",
        f"- support_coverage: {payload.get('support_coverage')}",
        f"- support_stale: {payload.get('support_stale')}",
        f"- critical_feature_stale: {payload.get('critical_feature_stale')}",
        f"- workflow_liveness: {payload.get('workflow_liveness')}",
        f"- stock_daily_quality: {payload.get('stock_daily_quality')}",
        f"- historical_health: {payload.get('historical_health')}",
        f"- history_ok: {payload.get('history_ok')}",
        f"- stock_daily_history_health: {payload.get('stock_daily_history_health')}",
        f"- list_date_missing_symbols: {payload.get('list_date_missing_symbols')}",
        f"- stock_daily_gap_summary: {payload.get('stock_daily_gap_summary')}",
        f"- accepted_gap_exceptions: {payload.get('accepted_gap_exceptions')}",
        f"- data_anomaly_ok: {payload.get('data_anomaly_ok')}",
        f"- data_anomaly_health: {payload.get('data_anomaly_health')}",
        f"- known_legacy_anomalies: {payload.get('known_legacy_anomalies')}",
        f"- tdx_server_state: {payload.get('tdx_server_state')}",
        f"- stale_pipeline_runs: {payload.get('stale_pipeline_runs')}",
        f"- storage_summary: {payload.get('storage_summary')}",
        f"- latest_strategy_run: {payload.get('latest_strategy_run')}",
        f"- lock_metadata: {payload.get('lock_metadata')}",
        "",
        "## Verification",
        "",
    ]
    lines.extend(f"- {message}" for message in payload.get("verification", []))
    _atomic_write_text(LATEST_STATUS_MD, "\n".join(lines) + "\n")


def _latest_date(conn: sqlite3.Connection, table_name: str, date_column: str) -> str | None:
    if not sqlite_table_exists(conn, table_name):
        return None
    row = conn.execute(f"SELECT MAX({date_column}) FROM {table_name}").fetchone()
    return str(row[0])[:10] if row and row[0] else None


def _latest_stock_daily_reports(conn: sqlite3.Connection, target: str) -> dict[str, str]:
    if not sqlite_table_exists(conn, "data_quality_report"):
        return {}
    rows = conn.execute(
        """
        SELECT r.market, r.status
        FROM data_quality_report r
        JOIN (
            SELECT market, MAX(created_at) AS created_at
            FROM data_quality_report
            WHERE DATE(trade_date) = DATE(?)
              AND module = 'stock_daily'
            GROUP BY market
        ) latest
          ON latest.market = r.market
         AND latest.created_at = r.created_at
        WHERE DATE(r.trade_date) = DATE(?)
          AND r.module = 'stock_daily'
        """,
        (target, target),
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _recent_gap_summary(target: str, window_trading_days: int = 45) -> dict[str, object]:
    scan = scan_stock_daily_gaps(
        DB_PATH,
        end_date=target,
        window_trading_days=window_trading_days,
        readonly=True,
    )
    return {
        "window_trading_days": window_trading_days,
        "raw_missing_count": scan.raw_missing_count,
        "exception_count": scan.exception_count,
        "effective_missing_count": scan.effective_missing_count,
        "abnormal_count": scan.abnormal_count,
        "candidate_symbol_count": len(scan.candidate_symbols),
        "top_dates": list(scan.top_dates[:5]),
    }


def _strategy_audit_exists(conn: sqlite3.Connection, target: str) -> bool:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'strategy_run_log'"
    ).fetchone()
    if not table:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM strategy_run_log
        WHERE strategy_name = 'mrgc_v1'
          AND DATE(trade_date) = DATE(?)
          AND status = 'success'
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        (target,),
    ).fetchone()
    return row is not None


def verify_after_run(
    target: str,
    *,
    require_rps: bool = True,
    require_strategy: bool = True,
) -> VerificationResult:
    messages: list[str] = []
    ok = True

    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        stock_latest = latest_table_date(
            conn, "stock_daily_prices", "trade_date", on_or_before=target,
        )
        rps_latest = latest_table_date(
            conn, "quant_feature_rps", "trade_date", on_or_before=target,
        )
        sector_rps_table = canonical_sector_rps_table(conn)
        sector_rps_latest = latest_table_date(
            conn, sector_rps_table, "trade_date", on_or_before=target,
        )
        reports = _latest_stock_daily_reports(conn, target)

        missing_markets = [market for market in ("SH", "SZ", "BSE") if market not in reports]
        failed_markets = [
            f"{market}={status}"
            for market, status in sorted(reports.items())
            if status == "FAIL"
        ]
        if missing_markets or failed_markets:
            ok = False
            messages.append(
                "stock_daily quality failed: "
                f"missing={missing_markets or '-'}, failed={failed_markets or '-'}"
            )
        else:
            messages.append(
                "stock_daily quality ok: "
                + ", ".join(f"{market}={reports[market]}" for market in ("SH", "SZ", "BSE"))
            )

        if not stock_latest or stock_latest < target:
            ok = False
            messages.append(f"stock_daily freshness failed: latest={stock_latest}, target={target}")
        else:
            messages.append(f"stock_daily freshness ok: latest={stock_latest}")

        if not require_rps:
            messages.append("RPS freshness skipped for early mode")
        elif (
            not rps_latest
            or rps_latest != target
            or not sector_rps_latest
            or sector_rps_latest != target
        ):
            ok = False
            messages.append(
                "RPS freshness failed: "
                f"stock_rps={rps_latest}, sector_rps={sector_rps_latest} "
                f"table={sector_rps_table}, stock_latest={stock_latest}"
            )
        else:
            messages.append(
                "RPS freshness ok: "
                f"stock={rps_latest}, sector={sector_rps_latest} table={sector_rps_table}"
            )

        if not require_strategy:
            messages.append("MRGC audit skipped for early mode")
        elif not _strategy_audit_exists(conn, target):
            ok = False
            messages.append(f"MRGC audit missing for {target}")
        else:
            messages.append(f"MRGC audit ok for {target}")

        if not require_rps:
            messages.append("sector verification skipped for early mode")
        else:
            sector_health = sector_data_health(conn, target)
            composite = sector_health["composite"]
            sector_rps = sector_health["rps"]
            if not composite.get("available"):
                ok = False
                messages.append("sector core verification pending: sector_composite_daily not installed")
            elif (
                not composite.get("latest")
                or str(composite.get("latest")) < str(target)
                or int(composite.get("target_rows", 0) or 0) <= 0
                or composite.get("quality_status") != "PASS"
            ):
                ok = False
                messages.append(
                    "sector composite verification failed: "
                    f"latest={composite.get('latest')}, target_rows={composite.get('target_rows')}, "
                    f"quality={composite.get('quality_status')}, target={target}"
                )
            else:
                messages.append(
                    "sector composite verification ok: "
                    f"coverage={composite.get('coverage_ratio')}, method={composite.get('method_versions') or '-'}"
                )

            if not sector_rps.get("available"):
                ok = False
                messages.append("sector RPS v2 verification pending: table not installed")
            elif not sector_rps.get("latest") or str(sector_rps.get("latest")) < str(target):
                ok = False
                messages.append(
                    f"sector RPS v2 freshness failed: latest={sector_rps.get('latest')}, target={target}"
                )
            else:
                messages.append(f"sector RPS v2 freshness ok: latest={sector_rps.get('latest')}")

        messages.append("recent stock_daily gap scan skipped in wrapper")

    return VerificationResult(ok=ok, messages=tuple(messages))


def write_lightweight_health_status(
    target: str,
    min_free_gb: int,
    *,
    deep_health: bool = False,
) -> int:
    try:
        validate_target_is_completed(target)
        check_disk_space(min_free_gb)
        if workflow_lock_is_available():
            preflight = "ok"
            metadata: dict[str, object] = {}
            deep_health_allowed = deep_health
            check_database(quick_check=deep_health_allowed)
            if deep_health_allowed:
                retention_summary = run_retention_maintenance()
                print(f"retention_summary={retention_summary}")
        else:
            preflight = "workflow_running"
            metadata = read_lock_metadata()
            deep_health_allowed = False
        write_status_report(
            mode="health",
            target=target,
            exit_code=0,
            preflight=preflight,
            lock_metadata=metadata,
            include_gap_summary=deep_health_allowed,
            deep_health=deep_health_allowed,
        )
        print(f"preflight={preflight}")
        print(f"health_mode={'deep' if deep_health_allowed else 'lightweight'}")
        return 0
    except Exception as exc:
        print(f"preflight=fail error={exc}")
        write_status_report(
            mode="health",
            target=target,
            exit_code=2,
            preflight=f"failed: {exc}",
            include_gap_summary=False,
        )
        return 2


def main() -> int:
    total_started = time.perf_counter()
    wrapper_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    preflight_seconds = 0.0
    backup_seconds = 0.0
    workflow_seconds = 0.0
    verification_seconds = 0.0
    args = build_parser().parse_args()
    target = resolve_target(args.target)
    command = build_workflow_command(
        args.mode,
        target,
        pending_days=args.pending_days,
        time_budget_minutes=args.time_budget_minutes,
        skip_notifications=args.skip_notifications,
    )
    env = local_only_env()
    lock_wait_minutes = (
        default_lock_wait_minutes(args.mode)
        if args.lock_wait_minutes is None
        else max(0, args.lock_wait_minutes)
    )
    workflow_timeout_minutes = (
        default_workflow_timeout_minutes(args.mode)
        if args.workflow_timeout_minutes is None
        else max(0, args.workflow_timeout_minutes)
    )

    print(f"EvoAlpha local data skill started at {datetime.now():%F %T}")
    print(f"backend={BACKEND_DIR}")
    print(f"mode={args.mode} target={target} dry_run={args.dry_run}")
    print("execution_policy=local_code_only, model_env=masked")

    if args.dry_run:
        print("dry_run=true")
        print("$ " + " ".join(command) if command else "workflow_command=none")
        write_status_report(
            mode=args.mode,
            target=target,
            exit_code=0,
            preflight="dry_run",
            include_gap_summary=False,
        )
        return 0

    if args.mode == "health":
        return write_lightweight_health_status(
            target,
            args.min_free_gb,
            deep_health=args.deep_health,
        )

    if args.target is None and args.mode in {"daily", "retry"} and not is_a_share_session(
        datetime.now().date()
    ):
        print("market_closed=skip_download_and_backup")
        return write_lightweight_health_status(target, args.min_free_gb)

    preflight_started = time.perf_counter()
    try:
        run_preflight(
            target,
            args.min_free_gb,
            lock_wait_minutes * 60,
            require_lock_available=True,
        )
        preflight_seconds = time.perf_counter() - preflight_started
        print("preflight=ok")
    except WorkflowLockBusy as exc:
        print(f"preflight=fail error={exc}")
        write_status_report(
            mode=args.mode,
            target=target,
            exit_code=2,
            preflight="lock_busy",
            lock_metadata=exc.metadata,
            include_gap_summary=False,
        )
        return 2
    except Exception as exc:
        print(f"preflight=fail error={exc}")
        write_status_report(
            mode=args.mode,
            target=target,
            exit_code=2,
            preflight=f"failed: {exc}",
            include_gap_summary=False,
        )
        return 2

    if args.mode in MUTATING_MODES:
        backup_started = time.perf_counter()
        backup_result = ensure_database_backup(
            target,
            retention=args.backup_retention,
            manual_retention_days=args.manual_backup_retention_days,
            manual_retention=args.manual_backup_retention,
            force_fresh=args.mode == "catchup",
            manual_label="catchup" if args.mode == "catchup" else None,
        )
        print(
            f"database_backup={backup_result.path} "
            f"method={backup_result.method} reused={backup_result.reused}"
        )
        retention_summary = run_retention_maintenance(
            retention=args.backup_retention,
            manual_retention_days=args.manual_backup_retention_days,
            manual_retention=args.manual_backup_retention,
        )
        print(f"retention_summary={retention_summary}")
        stale_marked = mark_stale_pipeline_runs()
        print(f"stale_pipeline_runs_marked={stale_marked}")
        backup_seconds = time.perf_counter() - backup_started

    started = time.perf_counter()
    timeout_seconds = workflow_timeout_minutes * 60 if workflow_timeout_minutes > 0 else None
    returncode = run_command(command, env, timeout_seconds=timeout_seconds)
    workflow_seconds = time.perf_counter() - started
    print(f"workflow_returncode={returncode} elapsed={workflow_seconds:.1f}s")
    if returncode != 0:
        record_pipeline_phase_timings(
            mode=args.mode,
            wrapper_started_at=wrapper_started_at,
            preflight_seconds=preflight_seconds,
            backup_seconds=backup_seconds,
            workflow_seconds=workflow_seconds,
            verification_seconds=verification_seconds,
            total_elapsed_seconds=time.perf_counter() - total_started,
        )
        write_status_report(
            mode=args.mode,
            target=target,
            exit_code=returncode,
            preflight="ok",
            workflow_returncode=returncode,
        )
        return returncode

    verification_started = time.perf_counter()
    verification = verify_after_run(
        target,
        require_rps=args.mode != "early",
        require_strategy=args.mode != "early",
    )
    verification_seconds = time.perf_counter() - verification_started
    for message in verification.messages:
        print(f"verify: {message}")
    if not verification.ok:
        record_pipeline_phase_timings(
            mode=args.mode,
            wrapper_started_at=wrapper_started_at,
            preflight_seconds=preflight_seconds,
            backup_seconds=backup_seconds,
            workflow_seconds=workflow_seconds,
            verification_seconds=verification_seconds,
            total_elapsed_seconds=time.perf_counter() - total_started,
        )
        write_status_report(
            mode=args.mode,
            target=target,
            exit_code=3,
            preflight="ok",
            workflow_returncode=returncode,
            verification=verification,
        )
        return 3

    watch_post_command = build_watch_post_command(
        args.mode, target, skip_notifications=args.skip_notifications,
    )
    if watch_post_command:
        watch_returncode = run_command(watch_post_command, env, timeout_seconds=60)
        print(f"watch_post_returncode={watch_returncode}")

    report_post_command = build_three_group_post_command(
        args.mode, target, skip_notifications=args.skip_notifications,
        explicit_target=args.target is not None,
    )
    if report_post_command:
        report_returncode = run_command(report_post_command, env, timeout_seconds=180)
        print(f"three_group_delivery_returncode={report_returncode}")

    # F10 enqueues during selection are deferred by the Data Core lock.
    # Kick one bounded background batch only after verification and report delivery.
    if args.mode in {"daily", "retry"}:
        material_returncode = run_command(
            [sys.executable, "-m", "data_job.auto_f10", "--kick"],
            env, timeout_seconds=15,
        )
        print(f"material_queue_kick_returncode={material_returncode}")

    write_status_report(
        mode=args.mode,
        target=target,
        exit_code=0,
        preflight="ok",
        workflow_returncode=returncode,
        verification=verification,
    )
    record_pipeline_phase_timings(
        mode=args.mode,
        wrapper_started_at=wrapper_started_at,
        preflight_seconds=preflight_seconds,
        backup_seconds=backup_seconds,
        workflow_seconds=workflow_seconds,
        verification_seconds=verification_seconds,
        total_elapsed_seconds=time.perf_counter() - total_started,
    )
    print("EvoAlpha local data skill finished successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
