#!/usr/bin/env python3
"""Build bounded, sequential Portfolio snapshots for one trading day."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Sequence


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from portfolio.performance import (
    InvalidSnapshotDateError,
    PortfolioPerformanceError,
    PortfolioPerformanceService,
    SnapshotConsistencyError,
)
from portfolio.schema import (
    DEFAULT_BUSY_TIMEOUT_MS,
    MAX_BUSY_TIMEOUT_MS,
    PortfolioSchemaError,
    ensure_portfolio_schema,
)


logger = logging.getLogger(__name__)


class MarketCalendarError(RuntimeError):
    """Raised when the requested date cannot be validated without writing."""


def _bounded_timeout(value: str) -> int:
    try:
        timeout = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 0 <= timeout <= MAX_BUSY_TIMEOUT_MS:
        raise argparse.ArgumentTypeError(
            f"must be between 0 and {MAX_BUSY_TIMEOUT_MS}"
        )
    return timeout


def _iso_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("must be YYYY-MM-DD")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build daily Portfolio snapshots for all active accounts"
    )
    parser.add_argument(
        "--trade-date",
        required=True,
        type=_iso_date,
        help="observed market trading date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--start-date",
        type=_iso_date,
        help="optional first trading date for an atomic historical rebuild",
    )
    parser.add_argument(
        "--db",
        default=settings.LOCAL_DB_PATH,
        help="target SQLite database path",
    )
    parser.add_argument(
        "--busy-timeout-ms",
        type=_bounded_timeout,
        default=DEFAULT_BUSY_TIMEOUT_MS,
        help=f"SQLite lock wait in milliseconds (0-{MAX_BUSY_TIMEOUT_MS})",
    )
    return parser


def _connect_read_only(
    db_path: Path,
    busy_timeout_ms: int,
) -> sqlite3.Connection:
    database_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(
        database_uri,
        uri=True,
        timeout=busy_timeout_ms / 1_000,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
    except Exception:
        conn.close()
        raise
    return conn


def _require_observed_trading_day(
    db_path: Path,
    trade_date: str,
    busy_timeout_ms: int,
) -> None:
    """Validate the calendar before any schema or snapshot write is attempted."""
    try:
        with closing(_connect_read_only(db_path, busy_timeout_ms)) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM stock_daily_prices
                WHERE trade_date = ?
                LIMIT 1
                """,
                (trade_date,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise MarketCalendarError("market calendar is unavailable") from exc
        raise
    if row is None:
        raise InvalidSnapshotDateError(
            f"{trade_date} is not an observed market trading day"
        )


def _active_account_ids(
    db_path: Path,
    busy_timeout_ms: int,
) -> tuple[int, ...]:
    with closing(_connect_read_only(db_path, busy_timeout_ms)) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM portfolio_accounts
            WHERE status = 'ACTIVE'
            ORDER BY id
            """
        ).fetchall()
    return tuple(int(row["id"]) for row in rows)


def _ledger_revision_as_of(
    conn: sqlite3.Connection,
    account_id: int,
    as_of_date: str,
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(transaction_row.id), 0)
        FROM portfolio_transactions AS transaction_row
        WHERE transaction_row.status = 'POSTED'
          AND (
                (
                    (
                        transaction_row.account_id = ?
                        OR transaction_row.counterparty_account_id = ?
                    )
                    AND transaction_row.trade_date <= ?
                )
                OR EXISTS (
                    SELECT 1
                    FROM portfolio_transactions AS original
                    WHERE original.id = transaction_row.reversal_of
                      AND original.status = 'POSTED'
                      AND original.trade_date <= ?
                      AND (
                            original.account_id = ?
                            OR original.counterparty_account_id = ?
                      )
                )
          )
        """,
        (
            account_id,
            account_id,
            as_of_date,
            as_of_date,
            account_id,
            account_id,
        ),
    ).fetchone()
    return int(row[0])


def _required_rebuild_start(
    db_path: Path,
    account_id: int,
    trade_date: str,
    busy_timeout_ms: int,
) -> str | None:
    """Find the earliest stale or missing closed snapshot before a daily build."""
    with closing(_connect_read_only(db_path, busy_timeout_ms)) as conn:
        snapshot_rows = conn.execute(
            """
            SELECT snapshot_date, ledger_revision, performance_status
            FROM portfolio_account_daily_snapshots
            WHERE account_id = ? AND snapshot_date <= ?
            ORDER BY snapshot_date
            """,
            (account_id, trade_date),
        ).fetchall()
        if not snapshot_rows:
            return None
        first_date = str(snapshot_rows[0]["snapshot_date"])
        calendar_rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM stock_daily_prices
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date
            """,
            (first_date, trade_date),
        ).fetchall()
        snapshots = {
            str(row["snapshot_date"]): row for row in snapshot_rows
        }
        for calendar_row in calendar_rows:
            snapshot_date = str(calendar_row["trade_date"])
            snapshot = snapshots.get(snapshot_date)
            if snapshot is None:
                if snapshot_date == trade_date:
                    continue
                return snapshot_date
            if str(snapshot["performance_status"]) == "GAP":
                return snapshot_date
            if int(snapshot["ledger_revision"]) != _ledger_revision_as_of(
                conn,
                account_id,
                snapshot_date,
            ):
                return snapshot_date
    return None


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _failure(
    trade_date: str,
    error_code: str,
    *,
    account_id: int | None = None,
    snapshot_count: int = 0,
) -> int:
    error: dict[str, object] = {"code": error_code}
    if account_id is not None:
        error["account_id"] = account_id
    _emit(
        {
            "ok": False,
            "trade_date": trade_date,
            "snapshot_count": snapshot_count,
            "error": error,
        }
    )
    return 1


def run(
    *,
    db_path: str | Path,
    trade_date: str,
    busy_timeout_ms: int,
    start_date: str | None = None,
) -> int:
    """Execute one bounded single-writer snapshot pass."""
    database = Path(db_path).expanduser()
    if start_date is not None and start_date > trade_date:
        logger.error("Portfolio snapshot start date is after target date")
        return _failure(trade_date, "INVALID_DATE_RANGE")
    try:
        _require_observed_trading_day(
            database,
            trade_date,
            busy_timeout_ms,
        )
        if start_date is not None:
            _require_observed_trading_day(
                database,
                start_date,
                busy_timeout_ms,
            )
    except InvalidSnapshotDateError:
        logger.error("Portfolio snapshot date is not an observed trading day")
        return _failure(trade_date, "NOT_TRADING_DAY")
    except MarketCalendarError:
        logger.error("Portfolio snapshot market calendar is unavailable")
        return _failure(trade_date, "MARKET_CALENDAR_UNAVAILABLE")
    except sqlite3.OperationalError:
        logger.error("Portfolio snapshot calendar read failed")
        return _failure(trade_date, "DATABASE_BUSY")
    except sqlite3.DatabaseError:
        logger.error("Portfolio snapshot calendar database is invalid")
        return _failure(trade_date, "DATABASE_ERROR")

    try:
        ensure_portfolio_schema(
            database,
            busy_timeout_ms=busy_timeout_ms,
        )
        account_ids = _active_account_ids(database, busy_timeout_ms)
    except sqlite3.OperationalError:
        logger.error("Portfolio snapshot schema or account read is busy")
        return _failure(trade_date, "DATABASE_BUSY")
    except (PortfolioSchemaError, sqlite3.DatabaseError):
        logger.error("Portfolio snapshot schema validation failed")
        return _failure(trade_date, "SCHEMA_ERROR")

    service = PortfolioPerformanceService(
        database,
        busy_timeout_ms=busy_timeout_ms,
    )
    results: list[dict[str, object]] = []
    status_counts: Counter[str] = Counter()
    rebuilt_snapshot_count = 0
    for account_id in account_ids:
        try:
            detected_start = (
                _required_rebuild_start(
                    database,
                    account_id,
                    trade_date,
                    busy_timeout_ms,
                )
                if start_date is None
                else None
            )
            range_start = start_date or detected_start
            if range_start is None:
                account_results = (
                    service.build_account_snapshot(account_id, trade_date),
                )
            else:
                account_results = service.rebuild_account_snapshots(
                    account_id,
                    range_start,
                    trade_date,
                )
            result = account_results[-1]
        except SnapshotConsistencyError:
            logger.error(
                "Portfolio snapshot consistency failure for account_id=%s",
                account_id,
            )
            return _failure(
                trade_date,
                "CONSISTENCY_ERROR",
                account_id=account_id,
                snapshot_count=len(results),
            )
        except InvalidSnapshotDateError:
            logger.error(
                "Portfolio snapshot trading calendar changed for account_id=%s",
                account_id,
            )
            return _failure(
                trade_date,
                "NOT_TRADING_DAY",
                account_id=account_id,
                snapshot_count=len(results),
            )
        except sqlite3.OperationalError:
            logger.error(
                "Portfolio snapshot database busy for account_id=%s",
                account_id,
            )
            return _failure(
                trade_date,
                "DATABASE_BUSY",
                account_id=account_id,
                snapshot_count=len(results),
            )
        except (PortfolioPerformanceError, sqlite3.DatabaseError):
            logger.error(
                "Portfolio snapshot build failed for account_id=%s",
                account_id,
            )
            return _failure(
                trade_date,
                "SNAPSHOT_ERROR",
                account_id=account_id,
                snapshot_count=len(results),
            )
        status = result.account.performance_status
        status_counts[status] += 1
        account_payload: dict[str, object] = {
            "account_id": account_id,
            "status": status,
        }
        if range_start is not None:
            account_payload["rebuilt_snapshot_count"] = len(account_results)
            rebuilt_snapshot_count += len(account_results)
        results.append(account_payload)

    payload: dict[str, object] = {
        "ok": True,
        "trade_date": trade_date,
        "active_account_count": len(account_ids),
        "snapshot_count": len(results),
        "status_counts": dict(sorted(status_counts.items())),
        "accounts": results,
    }
    if start_date is not None:
        payload["start_date"] = start_date
    if rebuilt_snapshot_count:
        payload["rebuilt_snapshot_count"] = rebuilt_snapshot_count
    _emit(payload)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    args = build_parser().parse_args(argv)
    return run(
        db_path=args.db,
        trade_date=args.trade_date,
        busy_timeout_ms=args.busy_timeout_ms,
        start_date=args.start_date,
    )


if __name__ == "__main__":
    raise SystemExit(main())
