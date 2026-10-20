"""Idempotent schema foundation for Data Core v0.1."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from data_core.symbols import market_case_sql


CORE_TABLES = (
    "trading_calendar",
    "data_source_log",
    "data_quality_report",
    "stock_daily_gap_exception",
)

DAILY_METADATA_COLUMNS = {
    "source": "TEXT DEFAULT 'tdx_mootdx_qfq'",
    "quality_flag": "TEXT DEFAULT 'legacy_ok'",
    "run_id": "TEXT",
    "updated_at": "TEXT",
    "adjustment_mode": "TEXT DEFAULT 'qfq'",
    "market": "TEXT",
}

VALUATION_METADATA_COLUMNS = {
    "source": "TEXT DEFAULT 'valuation_legacy'",
    "quality_flag": "TEXT DEFAULT 'legacy_ok'",
    "run_id": "TEXT",
    "updated_at": "TEXT",
    "adjustment_mode": "TEXT",
    "market": "TEXT",
}

STOCK_INFO_METADATA_COLUMNS = {
    "market": "TEXT",
}

DATA_CORE_INDEXES = (
    (
        "stock_info",
        "idx_stock_info_market",
        "CREATE INDEX IF NOT EXISTS idx_stock_info_market ON stock_info (market)",
    ),
    (
        "stock_daily_prices",
        "idx_stock_daily_prices_market_date",
        "CREATE INDEX IF NOT EXISTS idx_stock_daily_prices_market_date ON stock_daily_prices (market, trade_date)",
    ),
    (
        "stock_daily_prices",
        "idx_stock_daily_prices_trade_date",
        "CREATE INDEX IF NOT EXISTS idx_stock_daily_prices_trade_date ON stock_daily_prices (trade_date)",
    ),
    (
        "stock_valuation_daily",
        "idx_stock_valuation_daily_market_date",
        "CREATE INDEX IF NOT EXISTS idx_stock_valuation_daily_market_date ON stock_valuation_daily (market, trade_date)",
    ),
)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def add_missing_columns(
    conn: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> list[str]:
    """Add missing columns to an existing SQLite table."""
    if not table_exists(conn, table_name):
        return []

    existing = table_columns(conn, table_name)
    added: list[str] = []
    for column_name, column_type in columns.items():
        if column_name in existing:
            continue
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        added.append(column_name)
    return added


def create_core_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trading_calendar (
            trade_date TEXT NOT NULL,
            market TEXT NOT NULL,
            is_open INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'local_history',
            run_id TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, market)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS data_source_log (
            run_id TEXT NOT NULL,
            trade_date TEXT,
            source TEXT NOT NULL,
            api_name TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT 'ALL',
            status TEXT NOT NULL,
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            error_message TEXT,
            start_time TEXT,
            end_time TEXT,
            duration REAL,
            detail_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, source, api_name, market)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS data_quality_report (
            run_id TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            module TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT 'ALL',
            expected_count INTEGER DEFAULT 0,
            actual_count INTEGER DEFAULT 0,
            missing_count INTEGER DEFAULT 0,
            abnormal_count INTEGER DEFAULT 0,
            duplicate_count INTEGER DEFAULT 0,
            quality_score REAL,
            status TEXT NOT NULL,
            detail_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id, trade_date, module, market)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_daily_gap_exception (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            market TEXT NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'tdx_mootdx_qfq',
            attempts INTEGER DEFAULT 0,
            first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'accepted_no_raw_data',
            detail_json TEXT,
            PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_quality_report_lookup "
        "ON data_quality_report (trade_date, module, market, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_source_log_lookup "
        "ON data_source_log (trade_date, source, api_name, market, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_daily_gap_exception_lookup "
        "ON stock_daily_gap_exception (trade_date, market, status)"
    )


def create_data_core_indexes(conn: sqlite3.Connection) -> list[str]:
    """Create compatibility indexes for Data Core tables when their columns exist."""
    created: list[str] = []
    for table_name, index_name, sql in DATA_CORE_INDEXES:
        if not table_exists(conn, table_name):
            continue
        try:
            conn.execute(sql)
            created.append(index_name)
        except sqlite3.OperationalError:
            # Older partial schemas may be missing the referenced metadata column
            # before add_missing_columns runs. A later idempotent migration can add it.
            continue
    return created


def refresh_trading_calendar_from_history(conn: sqlite3.Connection) -> int:
    """Populate only dates at or after the latest known calendar date."""
    if not table_exists(conn, "stock_daily_prices"):
        return 0

    before = conn.execute("SELECT COUNT(*) FROM trading_calendar").fetchone()[0]
    latest_calendar_date = conn.execute(
        "SELECT MAX(trade_date) FROM trading_calendar"
    ).fetchone()[0]
    earliest_history_value = conn.execute(
        "SELECT MIN(trade_date) FROM stock_daily_prices WHERE trade_date IS NOT NULL"
    ).fetchone()[0]
    latest_history_value = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_prices WHERE trade_date IS NOT NULL"
    ).fetchone()[0]
    if earliest_history_value is None or latest_history_value is None:
        return 0

    earliest_history_date = datetime.fromisoformat(str(earliest_history_value)[:10]).date()
    latest_history_date = datetime.fromisoformat(str(latest_history_value)[:10]).date()
    start_date = (
        datetime.fromisoformat(str(latest_calendar_date)[:10]).date()
        if latest_calendar_date
        else earliest_history_date
    )
    end_date = latest_history_date + timedelta(days=1)
    if start_date >= end_date:
        return 0

    conn.execute(
        f"""
        INSERT OR IGNORE INTO trading_calendar (trade_date, market, is_open, source)
        SELECT DISTINCT SUBSTR(trade_date, 1, 10),
                        {market_case_sql('symbol')}, 1, 'stock_daily_prices'
        FROM stock_daily_prices
        WHERE trade_date IS NOT NULL
          AND trade_date >= ? AND trade_date < ?
          AND {market_case_sql('symbol')} IN ('SH', 'SZ', 'BSE')
        """,
        (start_date.isoformat(), end_date.isoformat()),
    )
    after = conn.execute("SELECT COUNT(*) FROM trading_calendar").fetchone()[0]
    return int(after - before)


def backfill_market_metadata(conn: sqlite3.Connection) -> None:
    """Backfill market where it is missing. This is optional and can be slow."""
    if table_exists(conn, "stock_info") and "market" in table_columns(conn, "stock_info"):
        conn.execute(
            f"""
            UPDATE stock_info
            SET market = {market_case_sql('symbol')}
            WHERE market IS NULL OR market = ''
            """
        )

    if table_exists(conn, "stock_daily_prices") and "market" in table_columns(conn, "stock_daily_prices"):
        conn.execute(
            f"""
            UPDATE stock_daily_prices
            SET market = {market_case_sql('symbol')}
            WHERE market IS NULL OR market = ''
            """
        )

    if table_exists(conn, "stock_valuation_daily") and "market" in table_columns(conn, "stock_valuation_daily"):
        conn.execute(
            f"""
            UPDATE stock_valuation_daily
            SET market = {market_case_sql('code')}
            WHERE market IS NULL OR market = ''
            """
        )


def ensure_data_core_schema(
    db_path: str | Path,
    *,
    backfill_legacy_metadata: bool = False,
) -> dict[str, object]:
    """Create Data Core metadata tables and add compatibility columns."""
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)

    result: dict[str, object] = {
        "db_path": str(db),
        "created_tables": list(CORE_TABLES),
        "added_columns": {},
        "created_indexes": [],
        "calendar_rows_added": 0,
        "backfilled_market": backfill_legacy_metadata,
    }

    with sqlite3.connect(db) as conn:
        create_core_tables(conn)
        added: dict[str, list[str]] = {}
        added["stock_daily_prices"] = add_missing_columns(
            conn,
            "stock_daily_prices",
            DAILY_METADATA_COLUMNS,
        )
        added["stock_valuation_daily"] = add_missing_columns(
            conn,
            "stock_valuation_daily",
            VALUATION_METADATA_COLUMNS,
        )
        added["stock_info"] = add_missing_columns(
            conn,
            "stock_info",
            STOCK_INFO_METADATA_COLUMNS,
        )
        result["added_columns"] = added
        result["created_indexes"] = create_data_core_indexes(conn)
        result["calendar_rows_added"] = refresh_trading_calendar_from_history(conn)
        if backfill_legacy_metadata:
            backfill_market_metadata(conn)
        conn.commit()

    return result


def log_data_source_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    trade_date: str | None,
    source: str,
    api_name: str,
    market: str,
    status: str,
    success_count: int = 0,
    fail_count: int = 0,
    error_message: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    duration: float | None = None,
    detail_json: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO data_source_log (
            run_id, trade_date, source, api_name, market, status,
            success_count, fail_count, error_message, start_time, end_time,
            duration, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            trade_date,
            source,
            api_name,
            market,
            status,
            success_count,
            fail_count,
            error_message,
            start_time,
            end_time,
            duration,
            detail_json,
        ),
    )
