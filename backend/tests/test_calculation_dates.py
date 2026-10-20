import sqlite3

from quant_engine.utils.calculation_dates import resolve_dense_output_dates


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT)")
    conn.execute("CREATE TABLE industry_strength_daily (sector_name TEXT, trade_date TEXT)")
    return conn


def _insert_rows(
    conn: sqlite3.Connection,
    table: str,
    entity_column: str,
    trade_date: str,
    count: int,
) -> None:
    conn.executemany(
        f"INSERT INTO {table} ({entity_column}, trade_date) VALUES (?, ?)",
        [(f"entity-{index}", trade_date) for index in range(count)],
    )


def test_dense_date_with_partial_rows_is_still_missing() -> None:
    conn = _connection()
    _insert_rows(conn, "stock_daily_prices", "symbol", "2026-07-06", 10)
    _insert_rows(conn, "industry_strength_daily", "sector_name", "2026-07-03", 100)
    _insert_rows(conn, "industry_strength_daily", "sector_name", "2026-07-06", 20)

    dates = resolve_dense_output_dates(
        conn,
        target_dates=["2026-07-06"],
        fallback=lambda: [],
        target_tables=("industry_strength_daily",),
        missing_only=True,
    )

    assert dates == ["2026-07-06"]


def test_dense_date_above_coverage_threshold_is_complete() -> None:
    conn = _connection()
    _insert_rows(conn, "stock_daily_prices", "symbol", "2026-07-06", 10)
    _insert_rows(conn, "industry_strength_daily", "sector_name", "2026-07-03", 100)
    _insert_rows(conn, "industry_strength_daily", "sector_name", "2026-07-06", 95)

    dates = resolve_dense_output_dates(
        conn,
        target_dates=["2026-07-06"],
        fallback=lambda: [],
        target_tables=("industry_strength_daily",),
        missing_only=True,
    )

    assert dates == []


def test_sparse_table_keeps_exists_semantics() -> None:
    conn = _connection()
    conn.execute("CREATE TABLE optional_events (event_id TEXT, trade_date TEXT)")
    _insert_rows(conn, "stock_daily_prices", "symbol", "2026-07-06", 10)
    conn.execute(
        "INSERT INTO optional_events (event_id, trade_date) VALUES ('one', '2026-07-06')"
    )

    dates = resolve_dense_output_dates(
        conn,
        target_dates=["2026-07-06"],
        fallback=lambda: [],
        target_tables=("optional_events",),
        missing_only=True,
    )

    assert dates == []
