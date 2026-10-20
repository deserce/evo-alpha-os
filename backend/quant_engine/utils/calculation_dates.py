"""Shared date selection for dense daily calculation tables."""

from collections.abc import Callable, Sequence
from datetime import datetime
import sqlite3


DENSE_TABLE_MIN_COVERAGE = {
    "industry_strength_daily": 0.90,
    "market_strength_daily": 0.90,
    "quant_feature_rps": 0.90,
    "quant_feature_sector_rps": 0.90,
    "quant_feature_etf_rps": 0.90,
    "stock_lifecycle_state": 0.90,
    "structure_health_daily": 0.90,
}


def normalize_target_dates(values: Sequence[str] | None, limit: int = 7) -> list[str]:
    dates: set[str] = set()
    for value in values or ():
        normalized = str(value)[:10]
        try:
            datetime.strptime(normalized, "%Y-%m-%d")
        except ValueError:
            continue
        dates.add(normalized)
    return sorted(dates)[-max(1, int(limit)) :]


def table_has_complete_date(
    conn: sqlite3.Connection, table_name: str, trade_date: str
) -> bool:
    """Return whether a date exists and dense outputs have normal row coverage."""
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM {table_name}
        WHERE trade_date >= ?
          AND trade_date < DATE(?, '+1 day')
        """,
        (trade_date, trade_date),
    ).fetchone()
    actual_count = int(row[0] or 0) if row else 0
    if actual_count == 0:
        return False

    minimum_coverage = DENSE_TABLE_MIN_COVERAGE.get(table_name)
    if minimum_coverage is None:
        return True
    peak_row = conn.execute(
        f"""
        SELECT MAX(row_count)
        FROM (
            SELECT COUNT(*) AS row_count
            FROM {table_name}
            WHERE trade_date <= ?
              AND trade_date >= DATE(?, '-45 day')
            GROUP BY substr(trade_date, 1, 10)
        )
        """,
        (trade_date, trade_date),
    ).fetchone()
    peak_count = int(peak_row[0] or 0) if peak_row else 0
    if peak_count == 0:
        return False
    return actual_count >= peak_count * minimum_coverage


def resolve_dense_output_dates(
    conn: sqlite3.Connection,
    *,
    target_dates: Sequence[str] | None,
    fallback: Callable[[], list[str]],
    target_tables: Sequence[str],
    missing_only: bool,
    limit: int = 7,
) -> list[str]:
    """Resolve explicit source dates and optionally retain only missing outputs."""
    requested = normalize_target_dates(target_dates, limit=limit)
    dates = requested or [str(value)[:10] for value in fallback() if value]
    if requested:
        dates = [
            trade_date
            for trade_date in dates
            if table_has_complete_date(conn, "stock_daily_prices", trade_date)
        ]
    if missing_only:
        dates = [
            trade_date
            for trade_date in dates
            if any(
                not table_has_complete_date(conn, table_name, trade_date)
                for table_name in target_tables
            )
        ]
    return dates
