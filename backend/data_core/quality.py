"""Data Core quality gates and report persistence."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from app.core.config import settings
from data_core.bse_codes import TDX_BSE_MARKET, TDX_STANDARD_BJ_SOURCE_NAME
from data_core.schema import ensure_data_core_schema, table_columns, table_exists
from data_core.symbols import MARKET_BSE
from data_core.symbols import P0_MARKETS, market_case_sql, market_where_sql


MODULE_STOCK_DAILY = "stock_daily"
MODULE_SECTOR_COMPOSITE = "sector_composite"
MODULE_SECTOR_VENDOR = "sector_vendor"
MODULE_SECTOR_RPS = "sector_rps"
STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"
GAP_EXCEPTION_TABLE = "stock_daily_gap_exception"
ACCEPTED_GAP_STATUS = "accepted_no_raw_data"
BSE_UNADJUSTED_SOURCES = (TDX_STANDARD_BJ_SOURCE_NAME, "tdx_exhq")

SECTOR_COMPOSITE_TABLE = "sector_composite_daily"
SECTOR_VENDOR_TABLE = "sector_vendor_daily"
SECTOR_RPS_TABLES = ("quant_feature_sector_rps_v2", "quant_feature_sector_rps")
SECTOR_MIN_COMPONENT_COVERAGE = 0.80
SECTOR_MIN_UNIVERSE_COVERAGE = 0.98


def _bse_unadjusted_exemption_sql() -> str:
    quoted_sources = ", ".join(f"'{source}'" for source in BSE_UNADJUSTED_SOURCES)
    return f"(source IN ({quoted_sources}) AND adjustment_mode = 'none')"


def _date(value: str) -> str:
    return datetime.fromisoformat(str(value)[:10]).date().isoformat()


def _day_bounds(value: str) -> tuple[str, str]:
    start = datetime.fromisoformat(str(value)[:10]).date()
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _scalar(conn: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    row = conn.execute(query, params).fetchone()
    if not row or row[0] is None:
        return 0
    return int(row[0])


def _stock_symbol_column(conn: sqlite3.Connection) -> str | None:
    if not table_exists(conn, "stock_info"):
        return None
    columns = table_columns(conn, "stock_info")
    if "symbol" in columns:
        return "symbol"
    if "code" in columns:
        return "code"
    return None


def _list_date_sql(column_name: str) -> str:
    return (
        "CASE "
        f"WHEN {column_name} IS NULL OR {column_name} = '' THEN NULL "
        f"WHEN LENGTH({column_name}) = 8 AND {column_name} NOT LIKE '%-%' "
        f"THEN SUBSTR({column_name}, 1, 4) || '-' || SUBSTR({column_name}, 5, 2) || '-' || SUBSTR({column_name}, 7, 2) "
        f"ELSE {column_name} END"
    )


def _stock_info_filters(
    conn: sqlite3.Connection,
    *,
    alias: str,
    symbol_column: str,
    market: str,
    trade_date: str | None,
) -> tuple[str, list[object]]:
    columns = table_columns(conn, "stock_info")
    qualified_symbol = f"{alias}.{symbol_column}"
    filters = [market_where_sql(market, qualified_symbol)]
    params: list[object] = []
    if trade_date and "list_date" in columns:
        list_date = _list_date_sql(f"{alias}.list_date")
        filters.append(f"({alias}.list_date IS NULL OR {alias}.list_date = '' OR DATE({list_date}) <= DATE(?))")
        params.append(trade_date)
    return " AND ".join(filters), params


def expected_stock_count(
    conn: sqlite3.Connection,
    market: str,
    trade_date: str | None = None,
) -> int:
    symbol_column = _stock_symbol_column(conn)
    if symbol_column:
        filters, params = _stock_info_filters(
            conn,
            alias="s",
            symbol_column=symbol_column,
            market=market,
            trade_date=trade_date,
        )
        return _scalar(
            conn,
            f"""
            SELECT COUNT(DISTINCT s.{symbol_column})
            FROM stock_info s
            WHERE {filters}
            """,
            tuple(params),
        )

    if not table_exists(conn, "stock_daily_prices"):
        return 0
    return _scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT symbol)
        FROM stock_daily_prices
        WHERE {market_where_sql(market, 'symbol')}
        """,
    )


def actual_stock_count(conn: sqlite3.Connection, trade_date: str, market: str) -> int:
    if not table_exists(conn, "stock_daily_prices"):
        return 0
    start_date, end_date = _day_bounds(trade_date)
    return _scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT symbol)
        FROM stock_daily_prices
        WHERE trade_date >= ? AND trade_date < ?
          AND {market_where_sql(market, 'symbol')}
        """,
        (start_date, end_date),
    )


def duplicate_stock_count(conn: sqlite3.Connection, trade_date: str, market: str) -> int:
    if not table_exists(conn, "stock_daily_prices"):
        return 0
    start_date, end_date = _day_bounds(trade_date)
    return _scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT symbol
            FROM stock_daily_prices
            WHERE trade_date >= ? AND trade_date < ?
              AND {market_where_sql(market, 'symbol')}
            GROUP BY symbol
            HAVING COUNT(*) > 1
        )
        """,
        (start_date, end_date),
    )


def abnormal_stock_count(conn: sqlite3.Connection, trade_date: str, market: str) -> int:
    if not table_exists(conn, "stock_daily_prices"):
        return 0

    columns = table_columns(conn, "stock_daily_prices")
    conditions: list[str] = []
    if {"high", "low"}.issubset(columns):
        conditions.append("high < low")
    if {"close", "high", "low"}.issubset(columns):
        conditions.append("close < low OR close > high")
    for column in ("open", "high", "low", "close"):
        if column in columns:
            conditions.append(f"{column} IS NULL OR {column} <= 0")
    for column in ("volume", "amount"):
        if column in columns:
            conditions.append(f"{column} IS NULL OR {column} < 0")
    if "turnover_rate" in columns:
        if market == MARKET_BSE and {"source", "adjustment_mode"}.issubset(columns):
            conditions.append(
                "turnover_rate IS NULL "
                f"AND NOT {_bse_unadjusted_exemption_sql()}"
            )
        else:
            conditions.append("turnover_rate IS NULL")

    if not conditions:
        return 0

    start_date, end_date = _day_bounds(trade_date)
    return _scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM stock_daily_prices
        WHERE trade_date >= ? AND trade_date < ?
          AND {market_where_sql(market, 'symbol')}
          AND ({' OR '.join(f'({condition})' for condition in conditions)})
        """,
        (start_date, end_date),
    )


def cross_symbol_clone_count(conn: sqlite3.Connection, trade_date: str, market: str) -> int:
    """Count rows in implausible active-trading OHLCV groups shared by symbols."""
    if not table_exists(conn, "stock_daily_prices"):
        return 0
    columns = table_columns(conn, "stock_daily_prices")
    required = {"symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"}
    if not required.issubset(columns):
        return 0

    start_date, end_date = _day_bounds(trade_date)
    return _scalar(
        conn,
        f"""
        SELECT COALESCE(SUM(symbol_count), 0)
        FROM (
            SELECT COUNT(DISTINCT symbol) AS symbol_count,
                   ROUND(close, 4) AS rounded_close
            FROM stock_daily_prices
            WHERE trade_date >= ? AND trade_date < ?
              AND {market_where_sql(market, 'symbol')}
              AND volume >= 1000
              AND amount > 0
            GROUP BY ROUND(open, 4), ROUND(high, 4), ROUND(low, 4),
                     ROUND(close, 4), ROUND(volume, 4), ROUND(amount, 4)
            HAVING (rounded_close >= 100 AND COUNT(DISTINCT symbol) >= 2)
                OR COUNT(DISTINCT symbol) >= 3
        )
        """,
        (start_date, end_date),
    )


def cross_day_pct_mismatch_count(conn: sqlite3.Connection, trade_date: str, market: str) -> int:
    """Detect a bar whose stored return belongs to a different price history."""
    if not table_exists(conn, "stock_daily_prices"):
        return 0
    required = {"symbol", "trade_date", "close", "pct_chg"}
    if not required.issubset(table_columns(conn, "stock_daily_prices")):
        return 0

    start_date, end_date = _day_bounds(trade_date)
    return _scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM stock_daily_prices AS current
        JOIN stock_daily_prices AS previous
          ON previous.symbol = current.symbol
         AND previous.trade_date = (
             SELECT MAX(prior.trade_date)
             FROM stock_daily_prices AS prior
             WHERE prior.symbol = current.symbol
               AND prior.trade_date < current.trade_date
         )
        WHERE current.trade_date >= ? AND current.trade_date < ?
          AND {market_where_sql(market, 'current.symbol')}
          AND current.close > 0 AND previous.close > 0
          AND current.pct_chg IS NOT NULL
          AND ABS((current.close / previous.close - 1.0) * 100.0 - current.pct_chg) > 2.0
        """,
        (start_date, end_date),
    )


def missing_stock_sample(
    conn: sqlite3.Connection,
    trade_date: str,
    market: str,
    limit: int = 50,
    *,
    exclude_accepted_exceptions: bool = False,
) -> list[str]:
    symbol_column = _stock_symbol_column(conn)
    if not symbol_column or not table_exists(conn, "stock_daily_prices"):
        return []
    filters, params = _stock_info_filters(
        conn,
        alias="s",
        symbol_column=symbol_column,
        market=market,
        trade_date=trade_date,
    )
    start_date, end_date = _day_bounds(trade_date)
    exception_params: list[object] = []
    exception_filter = ""
    if exclude_accepted_exceptions and table_exists(conn, GAP_EXCEPTION_TABLE):
        exception_filter = f"""
          AND NOT EXISTS (
              SELECT 1
              FROM {GAP_EXCEPTION_TABLE} e
              WHERE e.symbol = s.{symbol_column}
                AND e.trade_date >= ? AND e.trade_date < ?
                AND e.status = ?
          )
        """
        exception_params.extend([start_date, end_date, ACCEPTED_GAP_STATUS])
    rows = conn.execute(
        f"""
        SELECT s.{symbol_column}
        FROM stock_info s
        LEFT JOIN stock_daily_prices k
          ON k.symbol = s.{symbol_column}
         AND k.trade_date >= ? AND k.trade_date < ?
        WHERE {filters}
          AND k.symbol IS NULL
          {exception_filter}
        ORDER BY s.{symbol_column}
        LIMIT ?
        """,
        (start_date, end_date, *params, *exception_params, limit),
    ).fetchall()
    return [str(row[0]).zfill(6) for row in rows]


def accepted_gap_exception_count(
    conn: sqlite3.Connection,
    trade_date: str,
    market: str,
) -> int:
    symbol_column = _stock_symbol_column(conn)
    if (
        not symbol_column
        or not table_exists(conn, GAP_EXCEPTION_TABLE)
        or not table_exists(conn, "stock_daily_prices")
    ):
        return 0

    filters, params = _stock_info_filters(
        conn,
        alias="s",
        symbol_column=symbol_column,
        market=market,
        trade_date=trade_date,
    )
    start_date, end_date = _day_bounds(trade_date)
    return _scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT e.symbol)
        FROM {GAP_EXCEPTION_TABLE} e
        JOIN stock_info s ON s.{symbol_column} = e.symbol
        LEFT JOIN stock_daily_prices k
          ON k.symbol = e.symbol
         AND k.trade_date >= ? AND k.trade_date < ?
        WHERE e.trade_date >= ? AND e.trade_date < ?
          AND e.status = ?
          AND {filters}
          AND k.symbol IS NULL
        """,
        (
            start_date,
            end_date,
            start_date,
            end_date,
            ACCEPTED_GAP_STATUS,
            *params,
        ),
    )


def accepted_gap_exception_sample(
    conn: sqlite3.Connection,
    trade_date: str,
    market: str,
    limit: int = 50,
) -> list[str]:
    symbol_column = _stock_symbol_column(conn)
    if (
        not symbol_column
        or not table_exists(conn, GAP_EXCEPTION_TABLE)
        or not table_exists(conn, "stock_daily_prices")
    ):
        return []

    filters, params = _stock_info_filters(
        conn,
        alias="s",
        symbol_column=symbol_column,
        market=market,
        trade_date=trade_date,
    )
    start_date, end_date = _day_bounds(trade_date)
    rows = conn.execute(
        f"""
        SELECT DISTINCT e.symbol
        FROM {GAP_EXCEPTION_TABLE} e
        JOIN stock_info s ON s.{symbol_column} = e.symbol
        LEFT JOIN stock_daily_prices k
          ON k.symbol = e.symbol
         AND k.trade_date >= ? AND k.trade_date < ?
        WHERE e.trade_date >= ? AND e.trade_date < ?
          AND e.status = ?
          AND {filters}
          AND k.symbol IS NULL
        ORDER BY e.symbol
        LIMIT ?
        """,
        (
            start_date,
            end_date,
            start_date,
            end_date,
            ACCEPTED_GAP_STATUS,
            *params,
            limit,
        ),
    ).fetchall()
    return [str(row[0]).zfill(6) for row in rows]


def latest_source_failures(
    conn: sqlite3.Connection,
    trade_date: str | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    if not table_exists(conn, "data_source_log"):
        return []

    params: tuple = ()
    date_filter = ""
    if trade_date:
        date_filter = "AND (trade_date IS NULL OR DATE(trade_date) = DATE(?))"
        params = (trade_date,)

    rows = conn.execute(
        f"""
        SELECT source, api_name, market, status, fail_count, error_message, created_at
        FROM data_source_log
        WHERE status != 'PASS' AND status != 'OK' {date_filter}
        ORDER BY created_at DESC
        LIMIT {int(limit)}
        """,
        params,
    ).fetchall()
    return [
        {
            "source": row[0],
            "api_name": row[1],
            "market": row[2],
            "status": row[3],
            "fail_count": row[4],
            "error_message": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]


def value_distribution(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    market: str,
    column: str,
) -> dict[str, int]:
    if not table_exists(conn, "stock_daily_prices"):
        return {}
    columns = table_columns(conn, "stock_daily_prices")
    if column not in columns:
        return {}
    start_date, end_date = _day_bounds(trade_date)
    rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF({column}, ''), '-'), COUNT(*)
        FROM stock_daily_prices
        WHERE trade_date >= ? AND trade_date < ?
          AND {market_where_sql(market, 'symbol')}
        GROUP BY COALESCE(NULLIF({column}, ''), '-')
        ORDER BY COUNT(*) DESC
        """,
        (start_date, end_date),
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def latest_bse_capability(conn: sqlite3.Connection) -> dict[str, object] | None:
    if not table_exists(conn, "data_source_log"):
        return None
    row = conn.execute(
        """
        SELECT status, success_count, fail_count, error_message, detail_json, created_at
        FROM data_source_log
        WHERE source = ?
          AND api_name = 'bse_standard_capability_probe'
          AND market = 'BSE'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (TDX_STANDARD_BJ_SOURCE_NAME,),
    ).fetchone()
    if not row:
        row = conn.execute(
            """
            SELECT status, success_count, fail_count, error_message, detail_json, created_at
            FROM data_source_log
            WHERE source = 'tdx_exhq'
              AND api_name = 'bse_capability_probe'
              AND market = 'BSE'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None

    detail = {}
    if row[4]:
        try:
            detail = json.loads(row[4])
        except Exception:
            detail = {}
    best_server = detail.get("best_server") if isinstance(detail, dict) else None
    return {
        "status": row[0],
        "success_count": row[1],
        "fail_count": row[2],
        "error_message": row[3],
        "created_at": row[5],
        "success_rate": detail.get("success_rate") if isinstance(detail, dict) else None,
        "tdx_market": detail.get("tdx_market") if isinstance(detail, dict) else TDX_BSE_MARKET,
        "markets_seen": detail.get("markets_seen") if isinstance(detail, dict) else None,
        "best_server": best_server,
    }


def build_market_report(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    trade_date: str,
    market: str,
) -> dict[str, object]:
    expected = expected_stock_count(conn, market, trade_date)
    actual = actual_stock_count(conn, trade_date, market)
    raw_missing = max(expected - actual, 0)
    exception_count = min(raw_missing, accepted_gap_exception_count(conn, trade_date, market))
    effective_missing = max(raw_missing - exception_count, 0)
    effective_actual = max(0, expected - effective_missing)
    duplicate = duplicate_stock_count(conn, trade_date, market)
    row_abnormal = abnormal_stock_count(conn, trade_date, market)
    clone_count = cross_symbol_clone_count(conn, trade_date, market)
    cross_day_count = cross_day_pct_mismatch_count(conn, trade_date, market)
    abnormal = row_abnormal + clone_count + cross_day_count

    if expected == 0 and actual == 0:
        completeness = None
        status = STATUS_SKIP
        score = None
    else:
        completeness = effective_actual / expected if expected else 1.0
        score = max(0.0, min(100.0, completeness * 100.0 - duplicate * 0.5 - abnormal * 0.5))
        if duplicate > 0 or abnormal > 0 or completeness < 0.95:
            status = STATUS_FAIL
        elif completeness < 0.99:
            status = STATUS_WARNING
        else:
            status = STATUS_PASS

    detail = {
        "completeness": completeness,
        "raw_missing_count": raw_missing,
        "exception_count": exception_count,
        "effective_missing_count": effective_missing,
        "effective_completeness": completeness,
        "row_abnormal_count": row_abnormal,
        "cross_symbol_clone_count": clone_count,
        "cross_day_pct_mismatch_count": cross_day_count,
        "missing_symbols_sample": missing_stock_sample(conn, trade_date, market),
        "effective_missing_symbols_sample": missing_stock_sample(
            conn,
            trade_date,
            market,
            exclude_accepted_exceptions=True,
        ),
        "accepted_exception_symbols_sample": accepted_gap_exception_sample(conn, trade_date, market),
        "latest_source_failures": latest_source_failures(conn, trade_date),
        "source_distribution": value_distribution(
            conn,
            trade_date=trade_date,
            market=market,
            column="source",
        ),
        "adjustment_mode_distribution": value_distribution(
            conn,
            trade_date=trade_date,
            market=market,
            column="adjustment_mode",
        ),
    }
    if market == MARKET_BSE:
        detail["bse_capability"] = latest_bse_capability(conn)
        detail["tdx_market"] = TDX_BSE_MARKET

    return {
        "run_id": run_id,
        "trade_date": trade_date,
        "module": MODULE_STOCK_DAILY,
        "market": market,
        "expected_count": expected,
        "actual_count": actual,
        "missing_count": raw_missing,
        "abnormal_count": abnormal,
        "duplicate_count": duplicate,
        "quality_score": score,
        "status": status,
        "detail_json": json.dumps(detail, ensure_ascii=False),
    }


def persist_report(conn: sqlite3.Connection, report: dict[str, object]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO data_quality_report (
            run_id, trade_date, module, market, expected_count, actual_count,
            missing_count, abnormal_count, duplicate_count, quality_score,
            status, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report["run_id"],
            report["trade_date"],
            report["module"],
            report["market"],
            report["expected_count"],
            report["actual_count"],
            report["missing_count"],
            report["abnormal_count"],
            report["duplicate_count"],
            report["quality_score"],
            report["status"],
            report["detail_json"],
        ),
    )


def generate_stock_daily_quality_report(
    db_path: str | Path,
    *,
    trade_date: str,
    run_id: str | None = None,
    markets: Iterable[str] = P0_MARKETS,
    persist: bool = True,
) -> list[dict[str, object]]:
    """Generate P0 market quality reports for stock_daily_prices."""
    target_date = _date(trade_date)
    report_run_id = run_id or datetime.now().strftime("quality_%Y%m%d_%H%M%S")
    ensure_data_core_schema(db_path)

    reports: list[dict[str, object]] = []
    with sqlite3.connect(db_path) as conn:
        for market in markets:
            report = build_market_report(
                conn,
                run_id=report_run_id,
                trade_date=target_date,
                market=str(market).upper(),
            )
            reports.append(report)
            if persist:
                persist_report(conn, report)
        conn.commit()
    return reports


def report_allows_rps(reports: Iterable[dict[str, object]]) -> bool:
    """Return whether stock quality reports allow RPS calculation."""
    statuses = [str(report.get("status")) for report in reports]
    return bool(statuses) and STATUS_FAIL not in statuses


def _sector_universe_count(conn: sqlite3.Connection) -> int:
    """Return the expected Industry sector count without requiring a new schema."""
    if table_exists(conn, "sector_list"):
        columns = table_columns(conn, "sector_list")
        if {"sector_name", "sector_type"}.issubset(columns):
            return _scalar(
                conn,
                """
                SELECT COUNT(DISTINCT TRIM(sector_name))
                FROM sector_list
                WHERE sector_type = 'Industry'
                  AND sector_name IS NOT NULL
                  AND TRIM(sector_name) <> ''
                """,
            )

    if table_exists(conn, "stock_sector_map"):
        columns = table_columns(conn, "stock_sector_map")
        if {"sector_name", "sector_type"}.issubset(columns):
            return _scalar(
                conn,
                """
                SELECT COUNT(DISTINCT TRIM(sector_name))
                FROM stock_sector_map
                WHERE sector_type = 'Industry'
                  AND sector_name IS NOT NULL
                  AND TRIM(sector_name) <> ''
                """,
            )

    for table_name in (SECTOR_COMPOSITE_TABLE, SECTOR_VENDOR_TABLE, *SECTOR_RPS_TABLES):
        if table_exists(conn, table_name) and "sector_name" in table_columns(conn, table_name):
            return _scalar(
                conn,
                f"""
                SELECT COUNT(DISTINCT TRIM(sector_name))
                FROM {table_name}
                WHERE sector_name IS NOT NULL AND TRIM(sector_name) <> ''
                """,
            )
    return 0


def _sector_target_rows(
    conn: sqlite3.Connection,
    table_name: str,
    trade_date: str,
) -> tuple[list[str], int]:
    if not table_exists(conn, table_name):
        return [], 0
    columns = table_columns(conn, table_name)
    if not {"sector_name", "trade_date"}.issubset(columns):
        return [], 0
    rows = conn.execute(
        f"""
        SELECT TRIM(sector_name), COUNT(*)
        FROM {table_name}
        WHERE DATE(trade_date) = DATE(?)
          AND sector_name IS NOT NULL
          AND TRIM(sector_name) <> ''
        GROUP BY TRIM(sector_name)
        ORDER BY TRIM(sector_name)
        """,
        (trade_date,),
    ).fetchall()
    return [str(row[0]) for row in rows], sum(int(row[1] or 0) for row in rows)


def _sector_missing_columns(table_name: str, columns: set[str], required: set[str]) -> list[str]:
    return sorted(required - columns)


def _sector_report(
    *,
    module: str,
    table_name: str,
    run_id: str,
    trade_date: str,
    expected_count: int,
    actual_count: int,
    duplicate_count: int,
    abnormal_count: int,
    status: str,
    detail: dict[str, object],
) -> dict[str, object]:
    completeness = actual_count / expected_count if expected_count else None
    score = None if completeness is None else max(0.0, min(100.0, completeness * 100.0))
    report = {
        "run_id": run_id,
        "trade_date": trade_date,
        "module": module,
        "market": "ALL",
        "expected_count": expected_count,
        "actual_count": actual_count,
        "missing_count": max(expected_count - actual_count, 0),
        "abnormal_count": abnormal_count,
        "duplicate_count": duplicate_count,
        "quality_score": score,
        "status": status,
        "detail": detail,
    }
    report["detail_json"] = json.dumps(detail, ensure_ascii=False)
    return report


def build_sector_composite_quality_report(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    trade_date: str,
    min_component_coverage: float = SECTOR_MIN_COMPONENT_COVERAGE,
    min_universe_coverage: float = SECTOR_MIN_UNIVERSE_COVERAGE,
) -> dict[str, object]:
    """Validate the local equal-weight sector net-value input to sector RPS."""
    target_date = _date(trade_date)
    expected = _sector_universe_count(conn)
    if not table_exists(conn, SECTOR_COMPOSITE_TABLE):
        return _sector_report(
            module=MODULE_SECTOR_COMPOSITE,
            table_name=SECTOR_COMPOSITE_TABLE,
            run_id=run_id,
            trade_date=target_date,
            expected_count=expected,
            actual_count=0,
            duplicate_count=0,
            abnormal_count=1,
            status=STATUS_FAIL,
            detail={"reason": "table_missing", "table": SECTOR_COMPOSITE_TABLE},
        )

    columns = table_columns(conn, SECTOR_COMPOSITE_TABLE)
    required = {"sector_name", "trade_date", "net_value", "method_version"}
    missing_columns = _sector_missing_columns(SECTOR_COMPOSITE_TABLE, columns, required)
    if missing_columns:
        return _sector_report(
            module=MODULE_SECTOR_COMPOSITE,
            table_name=SECTOR_COMPOSITE_TABLE,
            run_id=run_id,
            trade_date=target_date,
            expected_count=expected,
            actual_count=0,
            duplicate_count=0,
            abnormal_count=1,
            status=STATUS_FAIL,
            detail={"reason": "required_columns_missing", "missing_columns": missing_columns},
        )

    names, row_count = _sector_target_rows(conn, SECTOR_COMPOSITE_TABLE, target_date)
    actual = len(names)
    duplicate = max(row_count - actual, 0)
    invalid_parts = [
        "net_value IS NULL OR net_value <= 0",
        "sector_name IS NULL OR TRIM(sector_name) = ''",
        "method_version IS NULL OR TRIM(method_version) = ''",
    ]
    if "quality_flag" in columns:
        invalid_parts.append("UPPER(COALESCE(quality_flag, '')) IN ('FAIL', 'BLOCKED', 'INVALID')")
    invalid_query = f"""
        SELECT COUNT(*) FROM {SECTOR_COMPOSITE_TABLE}
        WHERE DATE(trade_date) = DATE(?) AND ({' OR '.join(invalid_parts)})
    """
    abnormal = _scalar(conn, invalid_query, (target_date,))
    low_coverage = 0
    min_coverage = None
    weighted_coverage = None
    if "coverage_ratio" in columns:
        low_coverage = _scalar(
            conn,
            f"""
            SELECT COUNT(*) FROM {SECTOR_COMPOSITE_TABLE}
            WHERE DATE(trade_date) = DATE(?)
              AND (coverage_ratio IS NULL OR coverage_ratio < ?)
            """,
            (target_date, min_component_coverage),
        )
        min_coverage = conn.execute(
            f"SELECT MIN(coverage_ratio) FROM {SECTOR_COMPOSITE_TABLE} WHERE DATE(trade_date) = DATE(?)",
            (target_date,),
        ).fetchone()[0]
    if {"eligible_count", "priced_count"}.issubset(columns):
        eligible_total, priced_total = conn.execute(
            f"""
            SELECT COALESCE(SUM(eligible_count), 0), COALESCE(SUM(priced_count), 0)
            FROM {SECTOR_COMPOSITE_TABLE}
            WHERE DATE(trade_date) = DATE(?)
            """,
            (target_date,),
        ).fetchone()
        weighted_coverage = (
            float(priced_total) / float(eligible_total)
            if eligible_total
            else 0.0
        )
    method_versions = [
        str(row[0])
        for row in conn.execute(
            f"""
            SELECT DISTINCT TRIM(method_version)
            FROM {SECTOR_COMPOSITE_TABLE}
            WHERE DATE(trade_date) = DATE(?)
              AND method_version IS NOT NULL AND TRIM(method_version) <> ''
            ORDER BY TRIM(method_version)
            """,
            (target_date,),
        ).fetchall()
    ]
    completeness = actual / expected if expected else 0.0
    ok = bool(
        expected > 0
        and actual > 0
        and completeness >= min_universe_coverage
        and duplicate == 0
        and abnormal == 0
        # A single small industry can temporarily miss one constituent (for
        # example a newly listed or suspended name) without invalidating the
        # entire cross-sectional universe.  The row keeps LOW_COVERAGE and is
        # excluded by the RPS loader; the gate uses the weighted universe
        # coverage so the remaining sectors can still publish deterministically.
        and (
            (weighted_coverage is not None and weighted_coverage >= min_component_coverage)
            or (weighted_coverage is None and low_coverage == 0)
        )
        and len(method_versions) == 1
    )
    detail = {
        "table": SECTOR_COMPOSITE_TABLE,
        "completeness": completeness,
        "coverage_ratio": completeness,
        "min_component_coverage": min_coverage,
        "low_component_coverage_count": low_coverage,
        "weighted_component_coverage": weighted_coverage,
        "method_versions": method_versions,
        "row_count": row_count,
        "sector_count": actual,
        "min_required_universe_coverage": min_universe_coverage,
        "source": "local_sector_composite",
    }
    return _sector_report(
        module=MODULE_SECTOR_COMPOSITE,
        table_name=SECTOR_COMPOSITE_TABLE,
        run_id=run_id,
        trade_date=target_date,
        expected_count=expected,
        actual_count=actual,
        duplicate_count=duplicate,
        abnormal_count=abnormal,
        status=STATUS_PASS if ok else STATUS_FAIL,
        detail=detail,
    )


def build_sector_vendor_quality_report(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    trade_date: str,
    min_universe_coverage: float = SECTOR_MIN_UNIVERSE_COVERAGE,
) -> dict[str, object]:
    """Report vendor BK OHLC health as best-effort; it never blocks the core chain."""
    target_date = _date(trade_date)
    expected = _sector_universe_count(conn)
    if not table_exists(conn, SECTOR_VENDOR_TABLE):
        return _sector_report(
            module=MODULE_SECTOR_VENDOR,
            table_name=SECTOR_VENDOR_TABLE,
            run_id=run_id,
            trade_date=target_date,
            expected_count=expected,
            actual_count=0,
            duplicate_count=0,
            abnormal_count=0,
            status=STATUS_WARNING,
            detail={"reason": "table_missing", "table": SECTOR_VENDOR_TABLE, "blocking": False},
        )

    columns = table_columns(conn, SECTOR_VENDOR_TABLE)
    required = {"sector_name", "trade_date", "open", "high", "low", "close"}
    missing_columns = _sector_missing_columns(SECTOR_VENDOR_TABLE, columns, required)
    if missing_columns:
        return _sector_report(
            module=MODULE_SECTOR_VENDOR,
            table_name=SECTOR_VENDOR_TABLE,
            run_id=run_id,
            trade_date=target_date,
            expected_count=expected,
            actual_count=0,
            duplicate_count=0,
            abnormal_count=1,
            status=STATUS_WARNING,
            detail={
                "reason": "required_columns_missing",
                "missing_columns": missing_columns,
                "blocking": False,
            },
        )

    names, row_count = _sector_target_rows(conn, SECTOR_VENDOR_TABLE, target_date)
    actual = len(names)
    duplicate = max(row_count - actual, 0)
    abnormal = _scalar(
        conn,
        f"""
        SELECT COUNT(*) FROM {SECTOR_VENDOR_TABLE}
        WHERE DATE(trade_date) = DATE(?)
          AND (
              open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
              OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
              OR high < low OR open < low OR open > high OR close < low OR close > high
          )
        """,
        (target_date,),
    )
    completeness = actual / expected if expected else 0.0
    ok = bool(
        expected > 0
        and actual > 0
        and completeness >= min_universe_coverage
        and duplicate == 0
        and abnormal == 0
    )
    source_distribution = {}
    if "source" in columns:
        source_distribution = {
            str(row[0] or "-"): int(row[1])
            for row in conn.execute(
                f"""
                SELECT source, COUNT(*) FROM {SECTOR_VENDOR_TABLE}
                WHERE DATE(trade_date) = DATE(?)
                GROUP BY source ORDER BY source
                """,
                (target_date,),
            ).fetchall()
        }
    detail = {
        "table": SECTOR_VENDOR_TABLE,
        "completeness": completeness,
        "coverage_ratio": completeness,
        "row_count": row_count,
        "sector_count": actual,
        "source_distribution": source_distribution,
        "blocking": False,
    }
    return _sector_report(
        module=MODULE_SECTOR_VENDOR,
        table_name=SECTOR_VENDOR_TABLE,
        run_id=run_id,
        trade_date=target_date,
        expected_count=expected,
        actual_count=actual,
        duplicate_count=duplicate,
        abnormal_count=abnormal,
        status=STATUS_PASS if ok else STATUS_WARNING,
        detail=detail,
    )


def build_sector_rps_quality_report(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    trade_date: str,
    min_universe_coverage: float = SECTOR_MIN_UNIVERSE_COVERAGE,
) -> dict[str, object]:
    """Validate the new sector RPS table; prefer v2 while retaining read compatibility."""
    target_date = _date(trade_date)
    table_name = next((name for name in SECTOR_RPS_TABLES if table_exists(conn, name)), SECTOR_RPS_TABLES[0])
    expected = _sector_universe_count(conn)
    if not table_exists(conn, table_name):
        return _sector_report(
            module=MODULE_SECTOR_RPS,
            table_name=table_name,
            run_id=run_id,
            trade_date=target_date,
            expected_count=expected,
            actual_count=0,
            duplicate_count=0,
            abnormal_count=1,
            status=STATUS_FAIL,
            detail={"reason": "table_missing", "table": table_name},
        )
    columns = table_columns(conn, table_name)
    required = {"sector_name", "trade_date"}
    missing_columns = _sector_missing_columns(table_name, columns, required)
    if missing_columns:
        return _sector_report(
            module=MODULE_SECTOR_RPS,
            table_name=table_name,
            run_id=run_id,
            trade_date=target_date,
            expected_count=expected,
            actual_count=0,
            duplicate_count=0,
            abnormal_count=1,
            status=STATUS_FAIL,
            detail={"reason": "required_columns_missing", "missing_columns": missing_columns},
        )
    names, row_count = _sector_target_rows(conn, table_name, target_date)
    actual = len(names)
    duplicate = max(row_count - actual, 0)
    completeness = actual / expected if expected else 0.0
    ok = bool(expected > 0 and actual > 0 and completeness >= min_universe_coverage and duplicate == 0)
    return _sector_report(
        module=MODULE_SECTOR_RPS,
        table_name=table_name,
        run_id=run_id,
        trade_date=target_date,
        expected_count=expected,
        actual_count=actual,
        duplicate_count=duplicate,
        abnormal_count=0,
        status=STATUS_PASS if ok else STATUS_FAIL,
        detail={
            "table": table_name,
            "completeness": completeness,
            "coverage_ratio": completeness,
            "row_count": row_count,
            "sector_count": actual,
            "v2_preferred": table_name == SECTOR_RPS_TABLES[0],
        },
    )


def generate_sector_quality_reports(
    db_path: str | Path,
    *,
    trade_date: str,
    run_id: str | None = None,
    modules: Iterable[str] = (MODULE_SECTOR_COMPOSITE, MODULE_SECTOR_VENDOR, MODULE_SECTOR_RPS),
    persist: bool = True,
) -> list[dict[str, object]]:
    """Generate sector composite/vendor/RPS quality reports for one completed date."""
    target_date = _date(trade_date)
    report_run_id = run_id or datetime.now().strftime("sector_quality_%Y%m%d_%H%M%S")
    ensure_data_core_schema(db_path)
    reports: list[dict[str, object]] = []
    with sqlite3.connect(db_path) as conn:
        for module in modules:
            if module == MODULE_SECTOR_COMPOSITE:
                report = build_sector_composite_quality_report(
                    conn, run_id=report_run_id, trade_date=target_date
                )
            elif module == MODULE_SECTOR_VENDOR:
                report = build_sector_vendor_quality_report(
                    conn, run_id=report_run_id, trade_date=target_date
                )
            elif module == MODULE_SECTOR_RPS:
                report = build_sector_rps_quality_report(
                    conn, run_id=report_run_id, trade_date=target_date
                )
            else:
                raise ValueError(f"unknown sector quality module: {module}")
            reports.append(report)
            if persist:
                persist_report(conn, report)
        conn.commit()
    return reports


def sector_reports_allow_rps(reports: Iterable[dict[str, object]]) -> bool:
    """Return whether the composite quality gate permits industry RPS."""
    return any(
        str(report.get("module")) == MODULE_SECTOR_COMPOSITE
        and str(report.get("status")) in {STATUS_PASS, STATUS_WARNING}
        for report in reports
    )


def latest_quality_reports(
    conn: sqlite3.Connection,
    *,
    trade_date: str | None = None,
    module: str = MODULE_STOCK_DAILY,
) -> list[dict[str, object]]:
    if not table_exists(conn, "data_quality_report"):
        return []

    params: list[object] = [module]
    date_filter = ""
    if trade_date:
        date_filter = "AND DATE(trade_date) = DATE(?)"
        params.append(trade_date)

    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY trade_date, module, market
                       ORDER BY created_at DESC
                   ) AS rn
            FROM data_quality_report
            WHERE module = ? {date_filter}
        )
        SELECT run_id, trade_date, module, market, expected_count, actual_count,
               missing_count, abnormal_count, duplicate_count, quality_score,
               status, detail_json, created_at
        FROM ranked
        WHERE rn = 1
        ORDER BY trade_date DESC, market
        """,
        tuple(params),
    ).fetchall()

    return [
        {
            "run_id": row[0],
            "trade_date": row[1],
            "module": row[2],
            "market": row[3],
            "expected_count": row[4],
            "actual_count": row[5],
            "missing_count": row[6],
            "abnormal_count": row[7],
            "duplicate_count": row[8],
            "quality_score": row[9],
            "status": row[10],
            "detail": json.loads(row[11]) if row[11] else {},
            "created_at": row[12],
        }
        for row in rows
    ]


def format_report_line(report: dict[str, object]) -> str:
    score = report.get("quality_score")
    score_label = "-" if score is None else f"{float(score):.2f}"
    detail = report.get("detail") or {}
    if not detail and report.get("detail_json"):
        try:
            detail = json.loads(str(report.get("detail_json")))
        except Exception:
            detail = {}
    raw_missing = report.get("missing_count")
    effective_missing = detail.get("effective_missing_count") if isinstance(detail, dict) else None
    exception_count = detail.get("exception_count") if isinstance(detail, dict) else None
    if effective_missing is not None and effective_missing != raw_missing:
        missing_label = f"{raw_missing} raw/{effective_missing} effective, exceptions={exception_count}"
    else:
        missing_label = str(raw_missing)
    return (
        f"{report['status']} {report['module']}[{report['market']}]: "
        f"date={report['trade_date']}, expected={report['expected_count']}, "
        f"actual={report['actual_count']}, missing={missing_label}, "
        f"abnormal={report['abnormal_count']}, duplicate={report['duplicate_count']}, "
        f"score={score_label}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Data Core quality report")
    parser.add_argument("--db", default=settings.LOCAL_DB_PATH, help="SQLite database path")
    parser.add_argument("--date", required=True, help="target trade date, e.g. 2026-05-15")
    parser.add_argument("--run-id", help="optional run id")
    parser.add_argument(
        "--module",
        choices=(
            MODULE_STOCK_DAILY,
            MODULE_SECTOR_COMPOSITE,
            MODULE_SECTOR_VENDOR,
            MODULE_SECTOR_RPS,
            "sector",
        ),
        default=MODULE_STOCK_DAILY,
        help="quality module; sector runs composite/vendor/RPS reports together",
    )
    parser.add_argument("--fail-on-fail", action="store_true", help="exit non-zero on FAIL")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.module == MODULE_STOCK_DAILY:
        reports = generate_stock_daily_quality_report(
            args.db,
            trade_date=args.date,
            run_id=args.run_id,
            persist=True,
        )
    else:
        modules = (
            (MODULE_SECTOR_COMPOSITE, MODULE_SECTOR_VENDOR, MODULE_SECTOR_RPS)
            if args.module == "sector"
            else (args.module,)
        )
        reports = generate_sector_quality_reports(
            args.db,
            trade_date=args.date,
            run_id=args.run_id,
            modules=modules,
            persist=True,
        )
    for report in reports:
        print(format_report_line(report))

    allowed = (
        report_allows_rps(reports)
        if args.module == MODULE_STOCK_DAILY
        else (
            sector_reports_allow_rps(reports)
            if args.module in {MODULE_SECTOR_COMPOSITE, "sector"}
            else all(str(report.get("status")) != STATUS_FAIL for report in reports)
        )
    )
    if args.fail_on_fail and not allowed:
        print("Data Core quality gate failed: RPS is blocked.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
