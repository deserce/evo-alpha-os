#!/usr/bin/env python3
"""Bounded, local-only SQLite storage maintenance for EvoAlpha.

The script is intentionally dry-run by default.  ``--apply`` must be run
under the existing Data Core lock, after the market session, and creates a
fresh rollback snapshot before touching the database.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime, time as wall_time
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
LOCK_PATH = BACKEND_DIR / "data" / ".daily_data_workflow.lock"
DEFAULT_DERIVED_TRADING_DAYS = 500
DEFAULT_BATCH_SIZE = 10_000
SHANGHAI = ZoneInfo("Asia/Shanghai")

# These indexes are exact duplicates of the PRIMARY KEY/UNIQUE auto-index
# created by SQLite.  Removing them does not change query semantics.
DUPLICATE_INDEXES = (
    "idx_industry_date_sector",
    "idx_market_date_index",
    "idx_sector_composite_sector_date",
    "idx_sector_composite_ohlc_sector_date",
    "idx_stock_daily_prices_date",
    "idx_notification_delivery_dedupe",
    "idx_watch_events_dedupe",
    "idx_enrich_flow_symbol_date",
    "idx_enrich_holder_symbol_date",
    "idx_lifecycle_date_symbol",
    "idx_enrich_margin_symbol_date",
    "idx_structure_date_symbol",
)

# The table mapping lets us re-check the schema immediately before a drop.
# A named index is removed only when another index on the same table has the
# same key order and is the PRIMARY KEY/UNIQUE index, except for the two
# stock_daily_prices names which are an exact non-unique pair.
DUPLICATE_INDEX_TABLES = {
    "idx_industry_date_sector": "industry_strength_daily",
    "idx_market_date_index": "market_strength_daily",
    "idx_sector_composite_sector_date": "sector_composite_daily",
    "idx_sector_composite_ohlc_sector_date": "sector_composite_ohlc_daily",
    "idx_stock_daily_prices_date": "stock_daily_prices",
    "idx_stock_daily_prices_trade_date": "stock_daily_prices",
    "idx_notification_delivery_dedupe": "notification_deliveries",
    "idx_watch_events_dedupe": "watch_events",
    "idx_enrich_flow_symbol_date": "stock_fund_flow_daily",
    "idx_enrich_holder_symbol_date": "stock_holder_num",
    "idx_lifecycle_date_symbol": "stock_lifecycle_state",
    "idx_enrich_margin_symbol_date": "stock_margin_trading",
    "idx_structure_date_symbol": "structure_health_daily",
}

# Dense derived features are reproducible from the retained raw K-line data.
# Keep the canonical sector RPS table in the list even when it is shorter than
# the window today, so the rule remains stable as the table grows.
DERIVED_TABLES = (
    "quant_feature_rps",
    "quant_feature_sector_rps",
    "quant_feature_sector_rps_v2",
    "quant_feature_etf_rps",
    "market_strength_daily",
    "industry_strength_daily",
    "sector_composite_daily",
    "sector_composite_ohlc_daily",
    "sector_daily_prices_rebuild",
    "stock_lifecycle_state",
    "structure_health_daily",
    "timing_signals_daily",
)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row is not None


def _index_signature(conn: sqlite3.Connection, index_name: str) -> tuple[tuple[str, int], ...]:
    """Return ordered key columns and sort direction for a trusted index name."""
    escaped = index_name.replace('"', '""')
    return tuple(
        (row[2], int(row[3]))
        for row in conn.execute(f'PRAGMA index_xinfo("{escaped}")').fetchall()
        if row[2] is not None and int(row[5]) == 1
    )


def _is_safe_duplicate_index(conn: sqlite3.Connection, index_name: str) -> bool:
    """Verify a candidate is redundant in the current SQLite schema."""
    table_name = DUPLICATE_INDEX_TABLES.get(index_name)
    if table_name is None or not _table_exists(conn, table_name):
        return False
    target_signature = _index_signature(conn, index_name)
    if not target_signature:
        return False

    # These two indexes are the same ordinary (trade_date) key.  Neither is
    # the primary-key auto-index, so compare them directly.
    if index_name in {"idx_stock_daily_prices_date", "idx_stock_daily_prices_trade_date"}:
        peer = (
            "idx_stock_daily_prices_trade_date"
            if index_name == "idx_stock_daily_prices_date"
            else "idx_stock_daily_prices_date"
        )
        return _index_exists(conn, peer) and _index_signature(conn, peer) == target_signature

    escaped_table = table_name.replace('"', '""')
    for row in conn.execute(f'PRAGMA index_list("{escaped_table}")').fetchall():
        peer_name, is_unique, origin = row[1], bool(row[2]), row[3]
        if peer_name == index_name:
            continue
        same_key = _index_signature(conn, peer_name) == target_signature
        if same_key and (is_unique or origin in {"pk", "u"}):
            return True
    return False


def compute_cutoff_trade_date(conn: sqlite3.Connection, derived_trading_days: int) -> str | None:
    """Return the first date in the retained completed-trading-day window."""
    if derived_trading_days <= 0:
        raise ValueError("derived_trading_days must be positive")

    # Prefer the canonical calendar when present.  It has one row per market,
    # so DISTINCT is required; cap it at the latest raw K-line date to avoid a
    # preloaded future calendar moving the retention window forward.
    if _table_exists(conn, "trading_calendar"):
        row = conn.execute(
            """
            SELECT MIN(trade_date)
            FROM (
                SELECT DISTINCT DATE(calendar.trade_date) AS trade_date
                FROM trading_calendar AS calendar
                WHERE calendar.is_open = 1
                  AND DATE(calendar.trade_date) <= (
                      SELECT MAX(DATE(trade_date)) FROM stock_daily_prices
                  )
                ORDER BY DATE(calendar.trade_date) DESC
                LIMIT ?
            )
            """,
            (int(derived_trading_days),),
        ).fetchone()
        if row and row[0]:
            return str(row[0])

    row = conn.execute(
        """
        SELECT MIN(trade_date)
        FROM (
            SELECT DISTINCT DATE(trade_date) AS trade_date
            FROM stock_daily_prices
            WHERE trade_date IS NOT NULL
            ORDER BY DATE(trade_date) DESC
            LIMIT ?
        )
        """,
        (int(derived_trading_days),),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def inspect_storage(
    conn: sqlite3.Connection,
    derived_trading_days: int = DEFAULT_DERIVED_TRADING_DAYS,
) -> dict[str, object]:
    """Report reclaimable derived rows without changing the database."""
    cutoff = compute_cutoff_trade_date(conn, derived_trading_days)
    tables: dict[str, dict[str, object]] = {}
    for table_name in DERIVED_TABLES:
        if not _table_exists(conn, table_name):
            continue
        total = int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0)
        old = 0
        if cutoff:
            old = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE trade_date < ?",
                    (cutoff,),
                ).fetchone()[0]
                or 0
            )
        tables[table_name] = {
            "total_rows": total,
            "rows_before_cutoff": old,
            "min_trade_date": conn.execute(
                f"SELECT MIN(trade_date) FROM {table_name}"
            ).fetchone()[0],
            "max_trade_date": conn.execute(
                f"SELECT MAX(trade_date) FROM {table_name}"
            ).fetchone()[0],
        }
    return {
        "cutoff_trade_date": cutoff,
        "derived_trading_days": int(derived_trading_days),
        "tables": tables,
        "duplicate_indexes": [
            index_name
            for index_name in DUPLICATE_INDEXES
            if _is_safe_duplicate_index(conn, index_name)
        ],
    }


def apply_storage_cleanup(
    conn: sqlite3.Connection,
    derived_trading_days: int = DEFAULT_DERIVED_TRADING_DAYS,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, object]:
    """Drop exact duplicate indexes and delete old derived rows in batches."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    cutoff = compute_cutoff_trade_date(conn, derived_trading_days)
    dropped_indexes: list[str] = []
    for index_name in DUPLICATE_INDEXES:
        if _is_safe_duplicate_index(conn, index_name):
            conn.execute(f"DROP INDEX IF EXISTS {index_name}")
            dropped_indexes.append(index_name)
    conn.commit()

    deleted_rows: dict[str, int] = {}
    if cutoff:
        for table_name in DERIVED_TABLES:
            if not _table_exists(conn, table_name):
                continue
            deleted = 0
            while True:
                rowids = conn.execute(
                    f"SELECT rowid FROM {table_name} WHERE trade_date < ? LIMIT ?",
                    (cutoff, int(batch_size)),
                ).fetchall()
                if not rowids:
                    break
                conn.executemany(
                    f"DELETE FROM {table_name} WHERE rowid = ?",
                    rowids,
                )
                conn.commit()
                deleted += len(rowids)
            if deleted:
                deleted_rows[table_name] = deleted

    return {
        "cutoff_trade_date": cutoff,
        "dropped_indexes": dropped_indexes,
        "deleted_rows": deleted_rows,
    }


@contextmanager
def held_workflow_lock(target_trade_date: str) -> Iterator[None]:
    """Hold the existing Data Core lock for the whole maintenance operation."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("EvoAlpha Data Core workflow is already running") from exc
        metadata = {
            "pid": os.getpid(),
            "run_id": datetime.now().strftime("storage_%Y%m%d_%H%M%S"),
            "mode": "storage_maintenance",
            "target_trade_date": target_trade_date,
            "stage": "storage_maintenance",
            "stage_started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "heartbeat_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        lock_file.seek(0)
        lock_file.truncate()
        json.dump(metadata, lock_file, ensure_ascii=False)
        lock_file.flush()
        try:
            yield
        finally:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.flush()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def create_safety_backup(target_trade_date: str):
    """Create the required pre-mutation snapshot through the local scheduler."""
    from scripts.run_local_data_skill import ensure_database_backup

    return ensure_database_backup(
        target_trade_date,
        force_fresh=True,
        manual_label="storage_maintenance",
        total_retention=2,
    )


def _quick_check(db_path: Path) -> str:
    with sqlite3.connect(db_path, timeout=60) as conn:
        result = conn.execute("PRAGMA quick_check").fetchone()
    return str(result[0]) if result else "no_result"


def _today_is_open(conn: sqlite3.Connection, today: date) -> bool:
    if _table_exists(conn, "trading_calendar"):
        row = conn.execute(
            """
            SELECT 1
            FROM trading_calendar
            WHERE DATE(trade_date) = ? AND is_open = 1
            LIMIT 1
            """,
            (today.isoformat(),),
        ).fetchone()
        if row is not None:
            return True
        known = conn.execute(
            "SELECT 1 FROM trading_calendar WHERE DATE(trade_date) = ? LIMIT 1",
            (today.isoformat(),),
        ).fetchone()
        if known is not None:
            return False
    return today.weekday() < 5


def _database_holders(db_path: Path) -> list[str]:
    """Return non-self processes with the DB or SQLite sidecars open."""
    try:
        completed = subprocess.run(
            [
                "/usr/sbin/lsof",
                "-F",
                "pcfn",
                str(db_path),
                f"{db_path}-wal",
                f"{db_path}-shm",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if line.startswith("p"):
            if current:
                records.append(current)
            current = {"pid": line[1:]}
        elif line.startswith("c"):
            current["command"] = line[1:]
        elif line.startswith("f"):
            current["fd"] = line[1:]
        elif line.startswith("n"):
            current["path"] = line[1:]
    if current:
        records.append(current)
    self_pid = str(os.getpid())
    return sorted(
        {
            f"{record.get('pid', '?')}:{record.get('command', '?')}"
            for record in records
            if record.get("pid") not in {self_pid, "?"}
        }
    )


def ensure_maintenance_window(db_path: Path) -> None:
    """Reject live-session or live-connection maintenance before any backup."""
    now = datetime.now(SHANGHAI)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as conn:
        is_before_close_guard = now.time().replace(tzinfo=None) < wall_time(15, 20)
        if _today_is_open(conn, now.date()) and is_before_close_guard:
            raise RuntimeError(
                "storage maintenance is allowed only after the 15:20 market-close guard"
            )
    holders = _database_holders(db_path)
    if holders:
        raise RuntimeError("database is still open by another process: " + ", ".join(holders[:8]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded EvoAlpha SQLite storage maintenance")
    parser.add_argument("--apply", action="store_true", help="apply cleanup; default is dry-run")
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="rewrite the database after cleanup; only valid together with --apply",
    )
    parser.add_argument(
        "--derived-trading-days",
        type=int,
        default=DEFAULT_DERIVED_TRADING_DAYS,
        help="retain this many completed trading days in derived feature tables",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="row batch size for derived-table deletion",
    )
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--lock-file", type=Path, default=LOCK_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    global DB_PATH, LOCK_PATH

    args = build_parser().parse_args(argv)
    if args.vacuum and not args.apply:
        raise SystemExit("--vacuum requires --apply")
    if args.derived_trading_days <= 0 or args.batch_size <= 0:
        raise SystemExit("derived trading days and batch size must be positive")
    args.database = args.database.expanduser().resolve()
    args.lock_file = args.lock_file.expanduser().resolve()
    if not args.database.exists():
        raise SystemExit(f"database not found: {args.database}")

    DB_PATH = args.database
    LOCK_PATH = args.lock_file

    if not args.apply:
        with sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as conn:
            report = inspect_storage(conn, args.derived_trading_days)
        report["mode"] = "dry_run"
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    from scripts.run_daily_data_workflow import latest_completed_trading_date

    try:
        ensure_maintenance_window(args.database)
    except RuntimeError as exc:
        print(f"storage maintenance blocked: {exc}", file=sys.stderr)
        return 2
    target_trade_date = latest_completed_trading_date()
    with held_workflow_lock(target_trade_date):
        backup = create_safety_backup(target_trade_date)
        backup_quick_check = _quick_check(backup.path)
        if backup_quick_check != "ok":
            raise RuntimeError(f"safety backup quick_check failed: {backup_quick_check}")
        with sqlite3.connect(args.database, timeout=120) as conn:
            before = inspect_storage(conn, args.derived_trading_days)
            result = apply_storage_cleanup(
                conn,
                args.derived_trading_days,
                batch_size=args.batch_size,
            )
            after_cleanup = inspect_storage(conn, args.derived_trading_days)
        vacuum_status = "skipped"
        if args.vacuum:
            with sqlite3.connect(args.database, timeout=120) as conn:
                conn.execute("VACUUM")
            vacuum_status = "completed"
        quick_check = _quick_check(args.database)
        if quick_check != "ok":
            raise RuntimeError(f"database quick_check failed after cleanup: {quick_check}")
        from scripts.run_local_data_skill import prune_backups

        backup_retention = prune_backups(total_retention=1)

    report = {
        "mode": "apply",
        "target_trade_date": target_trade_date,
        "backup": {
            "path": str(backup.path),
            "method": backup.method,
            "reused": backup.reused,
            "quick_check": backup_quick_check,
        },
        "before": before,
        "result": result,
        "after_cleanup": after_cleanup,
        "vacuum": vacuum_status,
        "quick_check": quick_check,
        "backup_retention": backup_retention,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if quick_check == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
