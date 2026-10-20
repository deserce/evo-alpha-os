#!/usr/bin/env python3
"""Bounded rebuild for the local equal-weight sector net-value series.

The date range is mandatory by design: a full historical rebuild touches many
rows in the large local stock table and must be an explicit operator action.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from data_core.sector_composite import (
    DEFAULT_MAX_ABS_RETURN,
    DEFAULT_MIN_BREADTH,
    DEFAULT_MIN_COVERAGE,
    METHOD_VERSION,
    OHLC_METHOD_VERSION,
    build_sector_composites,
    ensure_sector_composite_schema,
)
from data_core.sector_membership import capture_membership_snapshots
from data_core.schema import table_exists


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild EvoAlpha sector composites")
    parser.add_argument("--db", default=settings.LOCAL_DB_PATH, help="SQLite database path")
    parser.add_argument("--start-date", help="First trade date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="Last trade date (YYYY-MM-DD)")
    parser.add_argument(
        "--target-date",
        help="Build exactly one completed trading date; shorthand for start=end",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Keep the command idempotent; rebuild only when the target date is incomplete",
    )
    parser.add_argument(
        "--membership-status",
        default="current_snapshot_backfill",
        choices=("current_snapshot_backfill", "daily_snapshot"),
        help="Membership provenance label",
    )
    parser.add_argument("--method-version", default=METHOD_VERSION)
    parser.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    parser.add_argument("--min-breadth", type=int, default=DEFAULT_MIN_BREADTH)
    parser.add_argument("--max-abs-return", type=float, default=DEFAULT_MAX_ABS_RETURN)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the requested range without opening the database",
    )
    return parser


def _table_target_is_complete(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    target_date: str,
    expected_count: int,
    current_snapshot_ids: set[str],
    expected_method_version: str,
) -> bool:
    if not table_exists(conn, table_name):
        return False
    row = conn.execute(
        f"SELECT COUNT(DISTINCT sector_name) FROM {table_name} "
        "WHERE trade_date = ?",
        (target_date,),
    ).fetchone()
    actual_count = int(row[0] or 0) if row else 0
    if expected_count <= 0 or actual_count != expected_count:
        return False
    snapshot_ids = {
        str(value[0])
        for value in conn.execute(
            f"SELECT DISTINCT membership_snapshot_id FROM {table_name} "
            "WHERE trade_date = ? AND membership_snapshot_id IS NOT NULL",
            (target_date,),
        ).fetchall()
    }
    if snapshot_ids != current_snapshot_ids:
        return False
    method_versions = {
        str(value[0])
        for value in conn.execute(
            f"SELECT DISTINCT method_version FROM {table_name} "
            "WHERE trade_date = ?",
            (target_date,),
        ).fetchall()
    }
    return method_versions == {expected_method_version}


def _target_is_complete(
    conn: sqlite3.Connection,
    *,
    target_date: str,
    expected_count: int,
    current_snapshot_ids: set[str],
    net_method_version: str,
) -> bool:
    """Require both the canonical close series and display OHLC to be complete."""
    return _table_target_is_complete(
        conn,
        table_name="sector_composite_daily",
        target_date=target_date,
        expected_count=expected_count,
        current_snapshot_ids=current_snapshot_ids,
        expected_method_version=net_method_version,
    ) and _table_target_is_complete(
        conn,
        table_name="sector_composite_ohlc_daily",
        target_date=target_date,
        expected_count=expected_count,
        current_snapshot_ids=current_snapshot_ids,
        expected_method_version=OHLC_METHOD_VERSION,
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.target_date:
        if args.start_date and args.start_date != args.target_date:
            raise SystemExit("--target-date cannot be combined with a different --start-date")
        if args.end_date and args.end_date != args.target_date:
            raise SystemExit("--target-date cannot be combined with a different --end-date")
        start_date = end_date = args.target_date
    else:
        start_date = args.start_date
        end_date = args.end_date
    if not start_date or not end_date:
        raise SystemExit("必须提供 --target-date，或同时提供 --start-date 和 --end-date")
    if str(start_date)[:10] > str(end_date)[:10]:
        raise SystemExit("--start-date 不能晚于 --end-date")
    request = {
        "db_path": str(Path(args.db)),
        "start_date": str(start_date)[:10],
        "end_date": str(end_date)[:10],
        "membership_status": args.membership_status,
        "method_version": args.method_version,
        "min_coverage": args.min_coverage,
        "min_breadth": args.min_breadth,
        "max_abs_return": args.max_abs_return,
        "missing_only": bool(args.missing_only),
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **request}, ensure_ascii=False))
        return 0

    preserve_existing_net_values = False
    with sqlite3.connect(args.db) as conn:
        ensure_sector_composite_schema(conn)
        if args.missing_only and str(start_date)[:10] == str(end_date)[:10]:
            # A complete row set is a no-op only when it was built from the
            # current content-addressed membership and method.  This makes a
            # daily membership change rebuild the target date instead of
            # silently keeping yesterday's constituents.
            snapshots = capture_membership_snapshots(
                conn,
                effective_date=str(start_date)[:10],
                membership_status=args.membership_status,
            )
            expected = len(snapshots)
            current_snapshot_ids = {snapshot.snapshot_id for snapshot in snapshots.values()}
            preserve_existing_net_values = _table_target_is_complete(
                conn,
                table_name="sector_composite_daily",
                target_date=str(start_date)[:10],
                expected_count=expected,
                current_snapshot_ids=current_snapshot_ids,
                expected_method_version=args.method_version,
            )
            if _target_is_complete(
                conn,
                target_date=str(start_date)[:10],
                expected_count=expected,
                current_snapshot_ids=current_snapshot_ids,
                net_method_version=args.method_version,
            ):
                print(json.dumps({"skipped": True, "reason": "target_date_complete", **request}, ensure_ascii=False))
                return 0
        result = build_sector_composites(
            conn,
            start_date=start_date,
            end_date=end_date,
            membership_status=args.membership_status,
            method_version=args.method_version,
            min_coverage=args.min_coverage,
            min_breadth=args.min_breadth,
            max_abs_return=args.max_abs_return,
            preserve_existing_net_values=preserve_existing_net_values,
        )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
