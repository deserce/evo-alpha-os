#!/usr/bin/env python3
"""Run bounded daily Portfolio exit reminders after trusted market close."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Sequence


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from portfolio.accounting import PortfolioAccountingError
from portfolio.exit_engine import PortfolioExitEngine, PortfolioExitError
from portfolio.schema import (
    DEFAULT_BUSY_TIMEOUT_MS,
    MAX_BUSY_TIMEOUT_MS,
    PortfolioSchemaError,
    ensure_portfolio_schema,
)


logger = logging.getLogger(__name__)


class MarketCalendarError(RuntimeError):
    """Raised when the requested trading date cannot be verified read-only."""


class NotObservedTradingDayError(RuntimeError):
    """Raised when the calendar exists but has no row for the requested date."""


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
        description="Run daily Portfolio exit reminders for active accounts"
    )
    parser.add_argument(
        "--trade-date",
        required=True,
        type=_iso_date,
        help="observed market trading date in YYYY-MM-DD",
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
    conn = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
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
    """Check the calendar before schema validation or any other write."""
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
        raise NotObservedTradingDayError(
            "date is not an observed market trading day"
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


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _failure(
    trade_date: str,
    error_code: str,
    *,
    account_id: int | None = None,
    accounts: list[dict[str, object]] | None = None,
) -> int:
    error: dict[str, object] = {"code": error_code}
    if account_id is not None:
        error["account_id"] = account_id
    completed = accounts or []
    _emit(
        {
            "ok": False,
            "trade_date": trade_date,
            "processed_account_count": len(completed),
            "accounts": completed,
            "error": error,
        }
    )
    return 1


def run(
    *,
    db_path: str | Path,
    trade_date: str,
    busy_timeout_ms: int,
) -> int:
    """Run accounts serially, with each account owning one short write cycle."""
    database = Path(db_path).expanduser()
    try:
        _require_observed_trading_day(database, trade_date, busy_timeout_ms)
    except NotObservedTradingDayError:
        logger.error("Portfolio exit date is not an observed trading day")
        return _failure(trade_date, "NOT_TRADING_DAY")
    except MarketCalendarError:
        logger.error("Portfolio exit market calendar is unavailable")
        return _failure(trade_date, "MARKET_CALENDAR_UNAVAILABLE")
    except sqlite3.OperationalError:
        logger.error("Portfolio exit calendar read is busy")
        return _failure(trade_date, "DATABASE_BUSY")
    except sqlite3.DatabaseError:
        logger.error("Portfolio exit calendar database is invalid")
        return _failure(trade_date, "DATABASE_ERROR")

    try:
        ensure_portfolio_schema(database, busy_timeout_ms=busy_timeout_ms)
        account_ids = _active_account_ids(database, busy_timeout_ms)
    except sqlite3.OperationalError:
        logger.error("Portfolio exit schema or account read is busy")
        return _failure(trade_date, "DATABASE_BUSY")
    except (PortfolioSchemaError, sqlite3.DatabaseError):
        logger.error("Portfolio exit schema validation failed")
        return _failure(trade_date, "SCHEMA_ERROR")

    engine = PortfolioExitEngine(
        database,
        busy_timeout_ms=busy_timeout_ms,
    )
    accounts: list[dict[str, object]] = []
    for account_id in account_ids:
        try:
            result = engine.run_daily(trade_date, account_id=account_id)
        except sqlite3.OperationalError:
            logger.error(
                "Portfolio exit database busy for account_id=%s",
                account_id,
            )
            return _failure(
                trade_date,
                "DATABASE_BUSY",
                account_id=account_id,
                accounts=accounts,
            )
        except (
            PortfolioAccountingError,
            PortfolioExitError,
            sqlite3.DatabaseError,
        ):
            logger.error(
                "Portfolio exit evaluation failed for account_id=%s",
                account_id,
            )
            return _failure(
                trade_date,
                "EXIT_ENGINE_ERROR",
                account_id=account_id,
                accounts=accounts,
            )
        except Exception:
            logger.error(
                "Unexpected Portfolio exit failure for account_id=%s",
                account_id,
            )
            return _failure(
                trade_date,
                "UNEXPECTED_ERROR",
                account_id=account_id,
                accounts=accounts,
            )
        if len(result.accounts) != 1:
            logger.error(
                "Portfolio exit result cardinality mismatch for account_id=%s",
                account_id,
            )
            return _failure(
                trade_date,
                "EXIT_ENGINE_ERROR",
                account_id=account_id,
                accounts=accounts,
            )
        account_result = result.accounts[0]
        accounts.append(
            {
                "account_id": account_result.account_id,
                "evaluated_positions": account_result.evaluated_positions,
                "blocked_positions": account_result.blocked_positions,
                "created_event_count": len(account_result.created_event_ids),
            }
        )

    _emit(
        {
            "ok": True,
            "trade_date": trade_date,
            "active_account_count": len(account_ids),
            "evaluated_position_count": sum(
                int(row["evaluated_positions"]) for row in accounts
            ),
            "blocked_position_count": sum(
                int(row["blocked_positions"]) for row in accounts
            ),
            "created_event_count": sum(
                int(row["created_event_count"]) for row in accounts
            ),
            "accounts": accounts,
        }
    )
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
