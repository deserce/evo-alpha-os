"""
个股相关API路由
"""
from fastapi import APIRouter, BackgroundTasks, Query, HTTPException
from typing import Annotated, Optional, List
from datetime import date, datetime, timedelta
import asyncio
import json
import logging
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
logger = logging.getLogger(__name__)

from app.api.v1.database import execute_query, fetch_one, fetch_all, paginate, engine
from app.api.v1.schemas import StockInfo, StockKline, StockValuation, ApiResponse
from app.core.minute_chart import get_minute_chart

try:
    from pypinyin import lazy_pinyin, Style
    _HAS_PINYIN = True
except ImportError:
    _HAS_PINYIN = False

_pinyin_cache: dict[str, tuple[str, str]] = {}


def _pinyin_keys(name: str) -> tuple[str, str]:
    if not _HAS_PINYIN or not name:
        return ("", "")
    cached = _pinyin_cache.get(name)
    if cached is not None:
        return cached
    try:
        full = "".join(lazy_pinyin(name, style=Style.NORMAL)).lower()
        initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower()
    except Exception:
        full, initials = "", ""
    if len(_pinyin_cache) > 8000:
        _pinyin_cache.clear()
    _pinyin_cache[name] = (full, initials)
    return (full, initials)

router = APIRouter(prefix="/stock", tags=["个股"])


VALID_CHART_PERIODS = {"daily", "weekly", "monthly", "30min", "120min"}
DEFAULT_STOCK_POOL_ID = "watchlist"
_stock_pool_schema_ready = False
_stock_pool_schema_engine_id: Optional[int] = None
_enrichment_schema_ready = False
_enrichment_schema_engine_id: Optional[int] = None


class StockPoolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    description: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = None
    monitor_enabled: bool = False


class StockPoolUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40)
    description: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = None
    monitor_enabled: Optional[bool] = None


class StockPoolMemberCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    note: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = None


class StockPoolMemberUpdate(BaseModel):
    note: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = None


class StockEnrichmentRefreshRequest(BaseModel):
    modules: Optional[List[str]] = None
    trade_date: Optional[str] = None


def _date_text(value) -> Optional[str]:
    if value is None:
        return None
    return str(value)[:10]


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _table_exists(table_name: str) -> bool:
    result = fetch_one(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name=:table_name
        LIMIT 1
        """,
        {"table_name": table_name},
    )
    return bool(result)


def _db_all(query: str, params: Optional[dict] = None) -> List[dict]:
    with engine.begin() as conn:
        result = conn.execute(text(query), params or {})
        return [dict(row) for row in result.mappings().all()]


def _db_one(query: str, params: Optional[dict] = None) -> Optional[dict]:
    rows = _db_all(query, params)
    return rows[0] if rows else None


def _db_execute(query: str, params: Optional[dict] = None) -> int:
    with engine.begin() as conn:
        result = conn.execute(text(query), params or {})
        return result.rowcount or 0


def _db_table_exists(table_name: str) -> bool:
    return bool(_db_one(
        """
        SELECT 1 AS exists_flag
        FROM sqlite_master
        WHERE type='table' AND name=:table_name
        LIMIT 1
        """,
        {"table_name": table_name},
    ))


def _db_columns(table_name: str) -> set[str]:
    if not _db_table_exists(table_name):
        return set()
    rows = _db_all(f"PRAGMA table_info({table_name})")
    return {str(row.get("name")) for row in rows}


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_member_refresh_background(symbols: List[str]) -> None:
    """API 响应后补算，不让历史遍历阻塞股票池操作。"""
    try:
        from quant_engine.watch.member_refresh import WatchMemberRefreshService

        result = WatchMemberRefreshService().refresh(symbols)
        if not result.get("success"):
            logger.warning("Watch 成员后台补算未完成: %s", result)
    except Exception:
        logger.exception("Watch 成员后台补算失败: symbols=%s", symbols)


def _queue_member_refresh(
    background_tasks: BackgroundTasks | None,
    symbols: List[str],
) -> bool:
    normalized = list(
        dict.fromkeys(
            _normalize_symbol(symbol)
            for symbol in symbols
            if _normalize_symbol(symbol)
        )
    )
    if background_tasks is None or not normalized:
        return False
    background_tasks.add_task(_run_member_refresh_background, normalized)
    return True


def _load_json(value):
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _ensure_enrichment_tables() -> None:
    global _enrichment_schema_engine_id, _enrichment_schema_ready

    current_engine_id = id(engine)
    if _enrichment_schema_engine_id != current_engine_id:
        _enrichment_schema_engine_id = current_engine_id
        _enrichment_schema_ready = False
    if _enrichment_schema_ready:
        return

    from data_job.collectors.stock_enrichment_collector import StockEnrichmentCollector

    collector = StockEnrichmentCollector()
    collector.engine = engine
    collector.ensure_tables()
    _enrichment_schema_ready = True


def _latest_row(table_name: str, where_sql: str, params: dict, order_column: str) -> Optional[dict]:
    rows = _db_all(
        f"""
        SELECT *
        FROM {table_name}
        WHERE {where_sql}
        ORDER BY {order_column} DESC
        LIMIT 1
        """,
        params,
    )
    return rows[0] if rows else None


def _limited_rows(
    table_name: str,
    where_sql: str,
    params: dict,
    order_column: str,
    limit: int = 20,
) -> list[dict]:
    return _db_all(
        f"""
        SELECT *
        FROM {table_name}
        WHERE {where_sql}
        ORDER BY {order_column} DESC
        LIMIT :limit
        """,
        {**params, "limit": limit},
    )


def _quarter_from_report_date(report_date: Optional[str]) -> Optional[int]:
    date_text = _date_text(report_date)
    if not date_text:
        return None
    month = date_text[5:7]
    return {"03": 1, "06": 2, "09": 3, "12": 4}.get(month)


FINANCE_METRICS = (
    "total_revenue",
    "net_profit",
    "deduct_net_profit",
    "eps",
    "roe",
    "operating_cashflow_per_share",
    "gross_margin",
    "net_margin",
)
FINANCE_DERIVABLE_METRICS = (
    "total_revenue",
    "net_profit",
    "deduct_net_profit",
)
FINANCE_PERIOD_YOY_COLUMNS = {
    "total_revenue": ("total_revenue_yoy", "revenue_up"),
    "net_profit": ("net_profit_yoy", "net_profit_up"),
    "deduct_net_profit": ("deduct_net_profit_yoy", "deduct_net_profit_up"),
}
FINANCE_PERIOD_QOQ_COLUMNS = {
    "total_revenue": ("total_revenue_qoq", "revenue_qoq"),
    "net_profit": ("net_profit_qoq",),
    "deduct_net_profit": ("deduct_net_profit_qoq",),
}
FINANCE_SINGLE_YOY_COLUMNS = {
    "total_revenue": ("total_revenue_single_yoy", "revenue_single_yoy"),
    "net_profit": ("net_profit_single_yoy",),
    "deduct_net_profit": ("deduct_net_profit_single_yoy",),
}
FINANCE_SINGLE_QOQ_COLUMNS = {
    "total_revenue": ("total_revenue_single_qoq", "revenue_single_qoq"),
    "net_profit": ("net_profit_single_qoq",),
    "deduct_net_profit": ("deduct_net_profit_single_qoq",),
}


def _first_float(row: dict, columns: tuple[str, ...]) -> Optional[float]:
    for column in columns:
        value = _to_float(row.get(column))
        if value is not None:
            return value
    return None


def _single_value(row: dict, metric: str) -> Optional[float]:
    """Read the canonical single-quarter value, with a bounded legacy alias."""
    return _first_float(row, (f"{metric}_single", f"quarter_{metric}"))


def _attach_single_quarter_values(rows: list[dict], key: str, target_key: str) -> None:
    """Fill an absolute single-quarter metric without crossing a missing quarter.

    Explicit source values, including zero and negative values, always win. Only
    absolute amount metrics call this helper; EPS, ROE and ratios must never be
    obtained by subtracting cumulative values.
    """
    rows_by_period: dict[tuple[str, int], dict] = {}
    for row in sorted(rows, key=lambda item: str(item.get("report_date") or "")):
        report_date = _date_text(row.get("report_date"))
        year = report_date[:4] if report_date else ""
        quarter = _quarter_from_report_date(report_date)
        explicit = _single_value(row, key)
        current = _to_float(row.get(key))

        if explicit is not None:
            row[target_key] = explicit
        elif current is None or quarter is None:
            row[target_key] = None
        elif quarter == 1:
            row[target_key] = current
        else:
            previous_row = rows_by_period.get((year, quarter - 1))
            previous = _to_float(previous_row.get(key)) if previous_row else None
            row[target_key] = current - previous if previous is not None else None

        if row[target_key] is not None and explicit is None:
            if row.get("single_value_source") == "missing":
                row["single_value_source"] = "derived"

        if year and quarter is not None:
            rows_by_period[(year, quarter)] = row


def _finance_summary_rows(
    symbol: str,
    limit: int = 12,
    *,
    start_date: Optional[str] = None,
    as_of: Optional[str] = None,
) -> list[dict]:
    if not _db_table_exists("stock_finance_summary"):
        return []

    columns = _db_columns("stock_finance_summary")
    conditions = ["code=:symbol"]
    params = {"symbol": symbol, "limit": limit}
    if start_date:
        conditions.append("DATE(report_date) >= DATE(:start_date)")
        params["start_date"] = start_date
    if as_of:
        disclosure_date = (
            "COALESCE(NULLIF(notice_date, ''), report_date)"
            if "notice_date" in columns
            else "report_date"
        )
        conditions.append(f"DATE({disclosure_date}) <= DATE(:as_of)")
        params["as_of"] = as_of
    rows = _db_all(
        f"""
        SELECT *
        FROM stock_finance_summary
        WHERE {' AND '.join(conditions)}
        ORDER BY report_date DESC
        LIMIT :limit
        """,
        params,
    )
    for row in rows:
        row["report_date"] = _date_text(row.get("report_date"))
        row["notice_date"] = _date_text(row.get("notice_date"))
        row["update_date"] = _date_text(row.get("update_date"))
        row["quarter"] = _quarter_from_report_date(row.get("report_date"))
        row["single_value_source"] = (
            "explicit"
            if any(_single_value(row, metric) is not None for metric in FINANCE_METRICS)
            else "missing"
        )

    for metric in FINANCE_DERIVABLE_METRICS:
        _attach_single_quarter_values(rows, metric, f"{metric}_single")
    for metric in FINANCE_METRICS:
        if metric in FINANCE_DERIVABLE_METRICS:
            continue
        row_key = f"{metric}_single"
        for row in rows:
            row[row_key] = _single_value(row, metric)

    return sorted(rows, key=lambda item: str(item.get("report_date") or ""), reverse=True)


def _finance_report_type(row: dict, quarter: Optional[int]) -> Optional[str]:
    value = row.get("report_type")
    if value:
        return str(value)
    return {1: "Q1", 2: "H1", 3: "Q3", 4: "ANNUAL"}.get(quarter)


def _finance_report_name(row: dict, year: Optional[int], quarter: Optional[int]) -> Optional[str]:
    value = row.get("report_date_name")
    if value:
        return str(value)
    if year is None:
        return None
    suffix = {1: "一季报", 2: "中报", 3: "三季报", 4: "年报"}.get(quarter)
    return f"{year}{suffix}" if suffix else str(year)


def _finance_common_row(row: dict) -> dict:
    report_date = _date_text(row.get("report_date"))
    quarter = _quarter_from_report_date(report_date)
    year = int(report_date[:4]) if report_date and report_date[:4].isdigit() else None
    return {
        "report_date": report_date,
        "report_type": _finance_report_type(row, quarter),
        "report_date_name": _finance_report_name(row, year, quarter),
        "year": year,
        "quarter": quarter,
        "notice_date": _date_text(row.get("notice_date")),
        "update_date": _date_text(row.get("update_date")),
        "source": row.get("source"),
        "fetched_at": str(row.get("fetched_at")) if row.get("fetched_at") else None,
    }


def _finance_period_row(row: dict) -> dict:
    result = _finance_common_row(row)
    for metric in FINANCE_METRICS:
        metric_columns = (metric, "roe_diluted") if metric == "roe" else (metric,)
        result[metric] = _first_float(row, metric_columns)
        result[f"{metric}_yoy"] = _first_float(
            row,
            FINANCE_PERIOD_YOY_COLUMNS.get(metric, (f"{metric}_yoy",)),
        )
        result[f"{metric}_qoq"] = _first_float(
            row,
            FINANCE_PERIOD_QOQ_COLUMNS.get(metric, (f"{metric}_qoq",)),
        )
    return result


def _finance_quarter_rows(rows: list[dict]) -> list[dict]:
    result = []
    for row in sorted(rows, key=lambda item: str(item.get("report_date") or "")):
        quarter_row = _finance_common_row(row)
        for metric in FINANCE_METRICS:
            value = _single_value(row, metric)
            quarter_row[metric] = value
            quarter_row[f"{metric}_yoy"] = _first_float(
                row,
                FINANCE_SINGLE_YOY_COLUMNS.get(metric, (f"{metric}_single_yoy",)),
            )
            quarter_row[f"{metric}_qoq"] = _first_float(
                row,
                FINANCE_SINGLE_QOQ_COLUMNS.get(metric, (f"{metric}_single_qoq",)),
            )
        quarter_row["value_source"] = row.get("single_value_source") or "missing"
        result.append(quarter_row)
    return result


def _finance_performance(rows: list[dict], as_of: str) -> dict:
    chronological = sorted(rows, key=lambda item: str(item.get("report_date") or ""))
    periods = [_finance_period_row(row) for row in chronological]
    annual = [row for row in periods if row.get("quarter") == 4]
    quarters = _finance_quarter_rows(chronological)
    available_metrics = [
        metric
        for metric in FINANCE_METRICS
        if any(row.get(metric) is not None for row in (*periods, *quarters))
    ]

    missing_quarter_amount = any(
        period.get(metric) is not None and quarter.get(metric) is None
        for period, quarter in zip(periods, quarters)
        for metric in FINANCE_DERIVABLE_METRICS
    )
    missing_core_amount = any(
        row.get(metric) is None
        for row in (*periods, *quarters)
        for metric in ('total_revenue', 'net_profit')
    )
    if not periods:
        data_status = "EMPTY"
    elif not available_metrics or missing_quarter_amount or missing_core_amount:
        data_status = "PARTIAL"
    else:
        data_status = "OK"

    latest_report_date = periods[-1]["report_date"] if periods else None
    latest_source = next(
        (row.get("source") for row in reversed(periods) if row.get("source")),
        None,
    )
    fetched_at = max(
        (str(row.get("fetched_at")) for row in periods if row.get("fetched_at")),
        default=None,
    )
    return {
        "periods": periods,
        "annual": annual,
        "quarters": quarters,
        "available_metrics": available_metrics,
        "latest_report_date": latest_report_date,
        "source": latest_source,
        "fetched_at": fetched_at,
        "data_status": data_status,
        "as_of": as_of,
    }


def _table_freshness(
    table_name: str,
    *,
    symbol: Optional[str] = None,
    symbol_column: str = "symbol",
    date_column: Optional[str] = None,
) -> dict:
    if not _db_table_exists(table_name):
        return {"table": table_name, "status": "missing", "row_count": 0}
    columns = _db_columns(table_name)
    fetched_expr = "MAX(fetched_at)" if "fetched_at" in columns else "NULL"
    date_expr = f"MAX({date_column})" if date_column and date_column in columns else "NULL"
    params = {}
    where = ""
    if symbol:
        where = f"WHERE {symbol_column} = :symbol"
        params["symbol"] = symbol
    row = _db_one(
        f"""
        SELECT
            COUNT(*) AS row_count,
            {date_expr} AS max_date,
            {fetched_expr} AS fetched_at
        FROM {table_name}
        {where}
        """,
        params,
    )
    count = int(row.get("row_count") or 0) if row else 0
    return {
        "table": table_name,
        "status": "cached" if count > 0 else "empty",
        "row_count": count,
        "max_date": row.get("max_date") if row else None,
        "fetched_at": row.get("fetched_at") if row else None,
    }


def _ensure_stock_pool_tables() -> None:
    global _stock_pool_schema_engine_id, _stock_pool_schema_ready

    current_engine_id = id(engine)
    if _stock_pool_schema_engine_id != current_engine_id:
        _stock_pool_schema_engine_id = current_engine_id
        _stock_pool_schema_ready = False
    if _stock_pool_schema_ready:
        return

    if _db_table_exists("stock_pools") and _db_table_exists("stock_pool_members"):
        if "monitor_enabled" not in _db_columns("stock_pools"):
            _db_execute(
                "ALTER TABLE stock_pools "
                "ADD COLUMN monitor_enabled INTEGER NOT NULL DEFAULT 0"
            )
            _db_execute(
                """
                UPDATE stock_pools
                SET monitor_enabled=1
                WHERE pool_id=:pool_id
                """,
                {"pool_id": DEFAULT_STOCK_POOL_ID},
            )
        default_pool = _db_one(
            "SELECT legacy_migrated FROM stock_pools WHERE pool_id=:pool_id",
            {"pool_id": DEFAULT_STOCK_POOL_ID},
        )
        if default_pool:
            _migrate_legacy_watchlist()
            _stock_pool_schema_ready = True
            return

    now = _now_text()
    _db_execute(
        """
        CREATE TABLE IF NOT EXISTS stock_pools (
            pool_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            sort_order INTEGER DEFAULT 0,
            is_default INTEGER DEFAULT 0,
            monitor_enabled INTEGER NOT NULL DEFAULT 0,
            legacy_migrated INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    _db_execute(
        """
        CREATE TABLE IF NOT EXISTS stock_pool_members (
            pool_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            note TEXT,
            sort_order INTEGER DEFAULT 0,
            added_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (pool_id, symbol)
        )
        """
    )
    _db_execute("CREATE INDEX IF NOT EXISTS idx_stock_pool_members_symbol ON stock_pool_members(symbol)")
    _db_execute("CREATE INDEX IF NOT EXISTS idx_stock_pool_members_pool ON stock_pool_members(pool_id)")

    default_pool = _db_one(
        "SELECT pool_id FROM stock_pools WHERE pool_id=:pool_id",
        {"pool_id": DEFAULT_STOCK_POOL_ID},
    )
    if not default_pool:
        _db_execute(
            """
            INSERT INTO stock_pools (
                pool_id, name, description, sort_order, is_default, monitor_enabled,
                legacy_migrated, created_at, updated_at
            )
            VALUES (
                :pool_id, '自选股', '默认自选股池', 0, 1, 1,
                0, :now, :now
            )
            """,
            {"pool_id": DEFAULT_STOCK_POOL_ID, "now": now},
        )

    _migrate_legacy_watchlist()
    _stock_pool_schema_ready = True


def _migrate_legacy_watchlist() -> None:
    default_pool = _db_one(
        "SELECT legacy_migrated FROM stock_pools WHERE pool_id=:pool_id",
        {"pool_id": DEFAULT_STOCK_POOL_ID},
    )
    if not default_pool or int(default_pool.get("legacy_migrated") or 0) == 1:
        return

    if _db_table_exists("watchlist"):
        now = _now_text()
        rows = _db_all(
            """
            SELECT symbol, name, added_at
            FROM watchlist
            WHERE symbol IS NOT NULL AND TRIM(symbol) <> ''
            """
        )
        for index, row in enumerate(rows):
            _db_execute(
                """
                INSERT INTO stock_pool_members (
                    pool_id, symbol, note, sort_order, added_at, updated_at
                )
                VALUES (
                    :pool_id, :symbol, NULL, :sort_order,
                    COALESCE(:added_at, :now), :now
                )
                ON CONFLICT(pool_id, symbol) DO NOTHING
                """,
                {
                    "pool_id": DEFAULT_STOCK_POOL_ID,
                    "symbol": _normalize_symbol(str(row.get("symbol") or "")),
                    "sort_order": index,
                    "added_at": row.get("added_at"),
                    "now": now,
                },
            )

    _db_execute(
        """
        UPDATE stock_pools
        SET legacy_migrated=1, updated_at=:now
        WHERE pool_id=:pool_id
        """,
        {"pool_id": DEFAULT_STOCK_POOL_ID, "now": _now_text()},
    )


def _pool_row(pool_id: str) -> Optional[dict]:
    return _db_one(
        """
        SELECT
            p.pool_id, p.name, p.description, p.sort_order, p.is_default,
            p.monitor_enabled,
            p.created_at, p.updated_at, COUNT(m.symbol) AS stock_count
        FROM stock_pools p
        LEFT JOIN stock_pool_members m ON m.pool_id = p.pool_id
        WHERE p.pool_id=:pool_id
        GROUP BY p.pool_id
        """,
        {"pool_id": pool_id},
    )


def _require_pool(pool_id: str) -> dict:
    _ensure_stock_pool_tables()
    pool = _pool_row(pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail=f"分类池 {pool_id} 不存在")
    return pool


def _require_manual_pool(pool_id: str) -> dict:
    pool = _require_pool(pool_id)
    if pool_id == "smart_mrgc_20d":
        raise HTTPException(status_code=409, detail="智能分组由盘后自动维护，不能手工修改")
    return pool


def _require_stock(symbol: str) -> dict:
    normalized = _normalize_symbol(symbol)
    stock = _db_one(
        """
        SELECT symbol, name, industry, market_cap, pe, pb
        FROM stock_info
        WHERE symbol=:symbol
        """,
        {"symbol": normalized},
    )
    if not stock:
        raise HTTPException(status_code=404, detail=f"股票 {normalized} 不存在")
    return stock


def _latest_stock_trade_date() -> Optional[str]:
    row = _db_one("SELECT DATE(MAX(trade_date)) AS trade_date FROM stock_daily_prices")
    return row.get("trade_date") if row else None


def _member_row(pool_id: str, symbol: str, trade_date: Optional[str] = None) -> Optional[dict]:
    rows = _stock_pool_members(pool_id, trade_date, symbol=symbol)
    return rows[0] if rows else None


def _resolve_smart_pool(trade_date: Optional[str]) -> dict:
    """Resolve dated smart members using the current engine's read-only connection."""
    from quant_engine.watch.smart_mrgc_pool import resolve_members

    target = trade_date or _latest_stock_trade_date()
    if not target:
        raise HTTPException(status_code=409, detail={"reason": "source_window_incomplete"})
    try:
        if date.fromisoformat(target).isoformat() != target:
            raise ValueError("noncanonical date")
    except ValueError:
        raise HTTPException(status_code=422, detail="交易日必须为 YYYY-MM-DD")
    with engine.connect() as connection:
        return resolve_members(connection.connection.driver_connection, target)


def _dated_smart_metadata(pool: dict, resolved: dict) -> dict:
    state = dict(pool.get("smart_state") or {})
    state.update({key: value for key, value in resolved.items() if key != "members"})
    return {**pool, "is_smart": True, "stock_count": resolved["member_count"],
            "smart_state": state}


def _stock_pool_members(pool_id: str, trade_date: Optional[str] = None, symbol: Optional[str] = None,
                        smart_result: Optional[dict] = None) -> List[dict]:
    target_date = trade_date or _latest_stock_trade_date()
    symbol_clause = "AND m.symbol=:symbol" if symbol else ""
    rps_join = ""
    rps_select = """
            NULL AS rps_5,
            NULL AS rps_10,
            NULL AS rps_20,
            NULL AS rps_50,
            NULL AS rps_120,
            NULL AS rps_250
    """
    if _db_table_exists("quant_feature_rps"):
        rps_join = """
        LEFT JOIN quant_feature_rps r
          ON r.symbol = m.symbol
         AND DATE(r.trade_date) = DATE(p.trade_date)
        """
        rps_select = """
            r.rps_5,
            r.rps_10,
            r.rps_20,
            r.rps_50,
            r.rps_120,
            r.rps_250
        """
    params = {
        "pool_id": pool_id,
        "trade_date": target_date,
        "symbol": _normalize_symbol(symbol or ""),
        "date_upper": target_date + "T" if target_date else None,
    }
    member_table = "stock_pool_members"
    member_cte = ""
    if pool_id == "smart_mrgc_20d":
        resolved = smart_result or _resolve_smart_pool(target_date)
        if resolved["status"] != "READY":
            raise HTTPException(status_code=409, detail={
                key: value for key, value in resolved.items() if key != "members"})
        if not resolved["members"]:
            return []
        values = []
        for index, member in enumerate(resolved["members"]):
            params[f"member_{index}"] = member["symbol"]
            params[f"note_{index}"] = (
                f"最近命中 {member['last_hit_date']} · 20日命中 {member['hit_count']} 次")
            values.append(f"(:pool_id,:member_{index},:note_{index},0,NULL,NULL)")
        member_table = "members_scope"
        member_cte = ("members_scope(pool_id,symbol,note,sort_order,added_at,updated_at) AS ("
                      + "VALUES " + ",".join(values) + "),")
        params["date_upper"] = target_date + "T"
    return _db_all(
        f"""
        WITH {member_cte} latest_price AS (
            SELECT p.*
            FROM {member_table} m
            JOIN stock_daily_prices p ON p.symbol = m.symbol
             AND p.trade_date = (
                 SELECT MAX(pr.trade_date) FROM stock_daily_prices pr
                 WHERE pr.symbol = m.symbol
                   AND (:trade_date IS NULL OR pr.trade_date < :date_upper)
             )
            WHERE m.pool_id=:pool_id {symbol_clause}
        )
        SELECT
            m.pool_id,
            m.symbol,
            COALESCE(s.name, m.symbol) AS name,
            s.industry,
            s.market_cap,
            s.pe,
            s.pb,
            m.note,
            m.sort_order,
            m.added_at,
            m.updated_at,
            DATE(p.trade_date) AS trade_date,
            p.close,
            p.pct_chg,
            p.amount,
            p.volume,
            p.turnover_rate,
            {rps_select}
        FROM {member_table} m
        LEFT JOIN stock_info s ON s.symbol = m.symbol
        LEFT JOIN latest_price p ON p.symbol = m.symbol
        {rps_join}
        WHERE m.pool_id=:pool_id
          {symbol_clause}
        ORDER BY m.sort_order ASC, m.added_at DESC, m.symbol ASC
        """,
        params,
    )


def _ema(values: List[Optional[float]], span: int) -> List[Optional[float]]:
    alpha = 2 / (span + 1)
    result: List[Optional[float]] = []
    previous: Optional[float] = None
    for value in values:
        if value is None:
            result.append(previous)
            continue
        previous = value if previous is None else previous * (1 - alpha) + value * alpha
        result.append(round(previous, 4))
    return result


def _calculate_macd(rows: List[dict]) -> dict:
    closes = [_to_float(row.get("close")) for row in rows]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    diff: List[Optional[float]] = []
    dea_values: List[Optional[float]] = []
    hist: List[Optional[float]] = []
    dea: Optional[float] = None
    alpha = 2 / (9 + 1)

    for short, long in zip(ema12, ema26):
        if short is None or long is None:
            diff.append(None)
            dea_values.append(dea)
            hist.append(None)
            continue
        dif = short - long
        dea = dif if dea is None else dea * (1 - alpha) + dif * alpha
        diff.append(round(dif, 4))
        dea_values.append(round(dea, 4))
        hist.append(round((dif - dea) * 2, 4))

    return {"diff": diff, "dea": dea_values, "hist": hist}


def _calculate_kdj(rows: List[dict], period: int = 9) -> dict:
    k_values: List[Optional[float]] = []
    d_values: List[Optional[float]] = []
    j_values: List[Optional[float]] = []
    k = 50.0
    d = 50.0

    for index, row in enumerate(rows):
        window = rows[max(0, index - period + 1): index + 1]
        highs = [_to_float(item.get("high")) for item in window]
        lows = [_to_float(item.get("low")) for item in window]
        highs = [value for value in highs if value is not None]
        lows = [value for value in lows if value is not None]
        close = _to_float(row.get("close"))

        if not highs or not lows or close is None:
            k_values.append(None)
            d_values.append(None)
            j_values.append(None)
            continue

        highest = max(highs)
        lowest = min(lows)
        rsv = 50.0 if highest == lowest else (close - lowest) / (highest - lowest) * 100
        k = (2 / 3) * k + (1 / 3) * rsv
        d = (2 / 3) * d + (1 / 3) * k
        j = 3 * k - 2 * d
        k_values.append(round(k, 4))
        d_values.append(round(d, 4))
        j_values.append(round(j, 4))

    return {"k": k_values, "d": d_values, "j": j_values}


def _period_key(trade_date: str, period: str) -> str:
    parsed = datetime.strptime(trade_date, "%Y-%m-%d").date()
    if period == "weekly":
        year, week, _ = parsed.isocalendar()
        return f"{year}-W{week:02d}"
    if period == "monthly":
        return parsed.strftime("%Y-%m")
    return trade_date


def _last_present(rows: List[dict], key: str):
    for row in reversed(rows):
        value = row.get(key)
        if value is not None:
            return value
    return None


def _aggregate_chart_rows(rows: List[dict], period: str) -> List[dict]:
    if period == "daily":
        return rows

    grouped: List[tuple[str, List[dict]]] = []
    for row in rows:
        trade_date = row.get("trade_date")
        if not trade_date:
            continue
        key = _period_key(str(trade_date), period)
        if not grouped or grouped[-1][0] != key:
            grouped.append((key, []))
        grouped[-1][1].append(row)

    result: List[dict] = []
    previous_close: Optional[float] = None
    for _, items in grouped:
        first = items[0]
        last = items[-1]
        close = _to_float(last.get("close"))
        pct_chg = None
        if previous_close and close is not None:
            pct_chg = (close / previous_close - 1) * 100
        previous_close = close if close is not None else previous_close

        aggregated = {
            "symbol": last.get("symbol"),
            "trade_date": last.get("trade_date"),
            "open": _to_float(first.get("open")),
            "high": max((_to_float(item.get("high")) or 0) for item in items),
            "low": min(
                (_to_float(item.get("low")) for item in items if _to_float(item.get("low")) is not None),
                default=None,
            ),
            "close": close,
            "volume": sum(_to_float(item.get("volume")) or 0 for item in items),
            "amount": sum(_to_float(item.get("amount")) or 0 for item in items),
            "pct_chg": pct_chg,
            "turnover_rate": sum(_to_float(item.get("turnover_rate")) or 0 for item in items),
        }

        for key in (
            "rps_5",
            "rps_10",
            "rps_20",
            "rps_50",
            "rps_120",
            "rps_250",
            "northbound_hold_ratio",
            "northbound_hold_value",
            "fund_report_date",
            "fund_count",
            "fund_hold_count",
            "fund_hold_value",
            "fund_hold_ratio",
        ):
            aggregated[key] = _last_present(items, key)
        result.append(aggregated)

    return result


def _fund_holding_reports(symbol: str, end_date: str) -> List[dict]:
    if not _table_exists("finance_fund_holdings"):
        return []

    valuation_expr = "NULL as matched_total_mv"
    valuation_date_expr = "NULL as matched_total_mv_date"
    if _table_exists("stock_valuation_daily"):
        valuation_expr = """
            COALESCE(
                (
                    SELECT v.total_mv
                    FROM stock_valuation_daily v
                    WHERE v.code = f.symbol
                      AND DATE(v.trade_date) <= DATE(f.report_date)
                    ORDER BY DATE(v.trade_date) DESC
                    LIMIT 1
                ),
                (
                    SELECT v.total_mv
                    FROM stock_valuation_daily v
                    WHERE v.code = f.symbol
                      AND DATE(v.trade_date) >= DATE(f.report_date)
                    ORDER BY DATE(v.trade_date) ASC
                    LIMIT 1
                )
            ) as matched_total_mv
        """
        valuation_date_expr = """
            COALESCE(
                (
                    SELECT DATE(v.trade_date)
                    FROM stock_valuation_daily v
                    WHERE v.code = f.symbol
                      AND DATE(v.trade_date) <= DATE(f.report_date)
                    ORDER BY DATE(v.trade_date) DESC
                    LIMIT 1
                ),
                (
                    SELECT DATE(v.trade_date)
                    FROM stock_valuation_daily v
                    WHERE v.code = f.symbol
                      AND DATE(v.trade_date) >= DATE(f.report_date)
                    ORDER BY DATE(v.trade_date) ASC
                    LIMIT 1
                )
            ) as matched_total_mv_date
        """

    query = f"""
        SELECT
            f.symbol,
            DATE(f.report_date) as report_date,
            f.fund_count,
            f.hold_count as fund_hold_count,
            f.hold_value as fund_hold_value,
            {valuation_expr},
            {valuation_date_expr}
        FROM finance_fund_holdings f
        WHERE f.symbol = :symbol
          AND DATE(f.report_date) <= DATE(:end_date)
        ORDER BY DATE(f.report_date) ASC
    """
    reports = fetch_all(query, {"symbol": symbol, "end_date": end_date})
    for report in reports:
        total_mv = _to_float(report.get("matched_total_mv"))
        hold_value = _to_float(report.get("fund_hold_value"))
        report_date = _date_text(report.get("report_date"))
        matched_date = _date_text(report.get("matched_total_mv_date"))
        distance = None
        if report_date and matched_date:
            distance = abs(
                (
                    datetime.strptime(matched_date, "%Y-%m-%d").date()
                    - datetime.strptime(report_date, "%Y-%m-%d").date()
                ).days
            )
        report["fund_hold_ratio"] = (
            hold_value / total_mv * 100
            if total_mv and hold_value is not None and (distance is None or distance <= 45)
            else None
        )
    return reports


def _attach_fund_reports(rows: List[dict], reports: List[dict]) -> None:
    report_index = 0
    current = None
    for row in rows:
        trade_date = row.get("trade_date")
        while report_index < len(reports) and reports[report_index].get("report_date") <= trade_date:
            current = reports[report_index]
            report_index += 1

        row["fund_report_date"] = current.get("report_date") if current else None
        row["fund_count"] = current.get("fund_count") if current else None
        row["fund_hold_count"] = current.get("fund_hold_count") if current else None
        row["fund_hold_value"] = current.get("fund_hold_value") if current else None
        row["fund_hold_ratio"] = current.get("fund_hold_ratio") if current else None


def _chart_series(rows: List[dict]) -> dict:
    macd = _calculate_macd(rows)
    kdj = _calculate_kdj(rows)

    for index, row in enumerate(rows):
        row["macd_diff"] = macd["diff"][index]
        row["macd_dea"] = macd["dea"][index]
        row["macd_hist"] = macd["hist"][index]
        row["kdj_k"] = kdj["k"][index]
        row["kdj_d"] = kdj["d"][index]
        row["kdj_j"] = kdj["j"][index]

    return {
        "dates": [row.get("trade_date") for row in rows],
        "kline": [
            [row.get("open"), row.get("close"), row.get("low"), row.get("high")]
            for row in rows
        ],
        "volume": [row.get("volume") for row in rows],
        "amount": [row.get("amount") for row in rows],
        "turnover_rate": [row.get("turnover_rate") for row in rows],
        "pct_chg": [row.get("pct_chg") for row in rows],
        "macd": macd,
        "kdj": kdj,
        "rps_short": {
            "r5": [row.get("rps_5") for row in rows],
            "r10": [row.get("rps_10") for row in rows],
            "r20": [row.get("rps_20") for row in rows],
        },
        "rps_long": {
            "r50": [row.get("rps_50") for row in rows],
            "r120": [row.get("rps_120") for row in rows],
            "r250": [row.get("rps_250") for row in rows],
        },
        "funds": {
            "north": [row.get("northbound_hold_ratio") for row in rows],
            "north_value": [row.get("northbound_hold_value") for row in rows],
            "fund": [row.get("fund_hold_ratio") for row in rows],
            "fund_value": [row.get("fund_hold_value") for row in rows],
            "fund_count": [row.get("fund_count") for row in rows],
        },
    }


# ==================== 股票信息 ====================

@router.get("/list", response_model=ApiResponse)
async def get_stock_list(
    industry: Optional[str] = Query(None, description="所属行业"),
    min_market_cap: Optional[float] = Query(None, description="最小市值（亿元）"),
    max_market_cap: Optional[float] = Query(None, description="最大市值（亿元）"),
    min_pe: Optional[float] = Query(None, description="最小市盈率"),
    max_pe: Optional[float] = Query(None, description="最大市盈率"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页大小")
):
    """
    获取股票列表（支持筛选和分页）

    筛选条件：
    - industry: 所属行业
    - min_market_cap/max_market_cap: 市值范围（亿元）
    - min_pe/max_pe: 市盈率范围
    """
    try:
        # 构建查询条件
        conditions = []
        params = {}

        if industry:
            conditions.append("industry = :industry")
            params["industry"] = industry

        if min_market_cap is not None:
            conditions.append("market_cap >= :min_market_cap")
            params["min_market_cap"] = min_market_cap

        if max_market_cap is not None:
            conditions.append("market_cap <= :max_market_cap")
            params["max_market_cap"] = max_market_cap

        if min_pe is not None:
            conditions.append("pe >= :min_pe")
            params["min_pe"] = min_pe

        if max_pe is not None:
            conditions.append("pe <= :max_pe")
            params["max_pe"] = max_pe

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                symbol, name, industry, market_cap, pe, pb,
                high_52w, low_52w
            FROM stock_info
            {where_clause}
            ORDER BY market_cap DESC
        """

        result = paginate(query, page, page_size, params)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "stocks": result["data"],
                "total": result["total"],
                "page": result["page"],
                "page_size": result["page_size"]
            }
        )

    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info/{symbol}", response_model=ApiResponse)
async def get_stock_info(symbol: str):
    """
    获取单个股票的详细信息

    Args:
        symbol: 股票代码（如：000001）
    """
    try:
        query = """
            SELECT
                symbol, name, industry, market_cap, pe, pb,
                high_52w, low_52w
            FROM stock_info
            WHERE symbol = :symbol
        """

        result = fetch_one(query, {"symbol": symbol})

        if not result:
            raise HTTPException(status_code=404, detail=f"股票 {symbol} 不存在")

        return ApiResponse(
            code=200,
            message="success",
            data=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取股票信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search", response_model=ApiResponse)
async def search_stocks(
    keyword: str = Query(..., min_length=1, description="搜索关键词（代码或名称）")
):
    """
    搜索股票（支持代码和名称模糊搜索）

    Args:
        keyword: 搜索关键词，如"平安"或"000001"
    """
    try:
        # 支持代码、名称、拼音全拼、拼音首字母搜索
        query = """
            SELECT
                symbol, name, industry, market_cap, pe, pb
            FROM stock_info
            WHERE symbol LIKE :keyword
               OR name LIKE :keyword
               OR pinyin LIKE :pinyin_kw
               OR pinyin_short LIKE :pinyin_kw
            ORDER BY market_cap DESC
            LIMIT 20
        """

        keyword_pattern = f"%{keyword}%"
        # 拼音搜索：转为小写
        pinyin_kw = f"%{keyword.lower()}%"
        results = fetch_all(query, {"keyword": keyword_pattern, "pinyin_kw": pinyin_kw})

        return ApiResponse(
            code=200,
            message="success",
            data={"stocks": results, "count": len(results)}
        )

    except Exception as e:
        logger.error(f"搜索股票失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 自选股与自定义分类池 ====================

@router.get("/pools", response_model=ApiResponse)
async def get_stock_pools(
    trade_date: Optional[str] = Query(None, description="历史交易日，默认最新交易日"),
):
    """获取自选股和自定义分类池。"""
    try:
        _ensure_stock_pool_tables()
        pools = _db_all(
            """
            SELECT
                p.pool_id,
                p.name,
                p.description,
                p.sort_order,
                p.is_default,
                p.monitor_enabled,
                p.created_at,
                p.updated_at,
                COUNT(m.symbol) AS stock_count
            FROM stock_pools p
            LEFT JOIN stock_pool_members m ON m.pool_id = p.pool_id
            GROUP BY p.pool_id
            ORDER BY p.sort_order ASC, p.is_default DESC, p.created_at ASC
            """
        )
        target_date = trade_date if isinstance(trade_date, str) else None
        for index, pool in enumerate(pools):
            pool["is_smart"] = pool["pool_id"] == "smart_mrgc_20d"
            if pool["is_smart"] and _db_table_exists("smart_watchlist_state"):
                pool["smart_state"] = _db_one(
                    "SELECT * FROM smart_watchlist_state WHERE pool_id=:pool_id",
                    {"pool_id": pool["pool_id"]},
                )
            if pool["is_smart"]:
                pools[index] = _dated_smart_metadata(pool, _resolve_smart_pool(target_date))
        return ApiResponse(
            code=200,
            message="success",
            data={
                "pools": pools,
                "default_pool_id": DEFAULT_STOCK_POOL_ID,
                "count": len(pools),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取股票池失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pools", response_model=ApiResponse)
async def create_stock_pool(payload: StockPoolCreate):
    """创建自定义分类池。"""
    try:
        _ensure_stock_pool_tables()
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="分类池名称不能为空")

        max_order = _db_one("SELECT MAX(sort_order) AS max_order FROM stock_pools") or {}
        sort_order = payload.sort_order
        if sort_order is None:
            sort_order = int(max_order.get("max_order") or 0) + 10

        pool_id = f"pool_{uuid4().hex[:12]}"
        now = _now_text()
        _db_execute(
            """
            INSERT INTO stock_pools (
                pool_id, name, description, sort_order, is_default, monitor_enabled,
                legacy_migrated, created_at, updated_at
            )
            VALUES (
                :pool_id, :name, :description, :sort_order, 0, :monitor_enabled,
                1, :now, :now
            )
            """,
            {
                "pool_id": pool_id,
                "name": name,
                "description": payload.description,
                "sort_order": sort_order,
                "monitor_enabled": 1 if payload.monitor_enabled else 0,
                "now": now,
            },
        )
        return ApiResponse(code=200, message="success", data={"pool": _pool_row(pool_id)})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建股票池失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/pools/{pool_id}", response_model=ApiResponse)
async def update_stock_pool(
    pool_id: str,
    payload: StockPoolUpdate,
    background_tasks: BackgroundTasks = None,
):
    """重命名或调整自定义分类池。"""
    try:
        pool = _require_manual_pool(pool_id)
        now = _now_text()
        _db_execute(
            """
            UPDATE stock_pools
            SET
                name=COALESCE(:name, name),
                description=COALESCE(:description, description),
                sort_order=COALESCE(:sort_order, sort_order),
                monitor_enabled=COALESCE(:monitor_enabled, monitor_enabled),
                updated_at=:now
            WHERE pool_id=:pool_id
            """,
            {
                "pool_id": pool["pool_id"],
                "name": payload.name.strip() if payload.name else None,
                "description": payload.description,
                "sort_order": payload.sort_order,
                "monitor_enabled": (
                    1 if payload.monitor_enabled else 0
                ) if payload.monitor_enabled is not None else None,
                "now": now,
            },
        )
        refresh_queued = False
        if (
            payload.monitor_enabled is True
            and int(pool.get("monitor_enabled") or 0) == 0
        ):
            member_rows = _db_all(
                "SELECT symbol FROM stock_pool_members "
                "WHERE pool_id=:pool_id ORDER BY sort_order, symbol",
                {"pool_id": pool_id},
            )
            refresh_queued = _queue_member_refresh(
                background_tasks,
                [str(row.get("symbol") or "") for row in member_rows],
            )
        return ApiResponse(
            code=200,
            message="success",
            data={
                "pool": _pool_row(pool_id),
                "refresh_queued": refresh_queued,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新股票池失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pools/{pool_id}", response_model=ApiResponse)
async def delete_stock_pool(pool_id: str):
    """删除自定义分类池，默认自选股不可删除。"""
    try:
        pool = _require_manual_pool(pool_id)
        if int(pool.get("is_default") or 0) == 1:
            raise HTTPException(status_code=400, detail="默认自选股池不可删除")

        _db_execute("DELETE FROM stock_pool_members WHERE pool_id=:pool_id", {"pool_id": pool_id})
        _db_execute("DELETE FROM stock_pools WHERE pool_id=:pool_id", {"pool_id": pool_id})
        return ApiResponse(code=200, message="success", data={"deleted": True, "pool_id": pool_id})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除股票池失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pools/{pool_id}/stocks", response_model=ApiResponse)
async def get_stock_pool_stocks(
    pool_id: str,
    trade_date: Optional[str] = Query(None, description="交易日，默认数据库最新交易日"),
):
    """获取分类池内股票及真实行情/RPS字段。"""
    try:
        pool = _require_pool(pool_id)
        if not isinstance(trade_date, str):
            trade_date = None
        target_date = trade_date or _latest_stock_trade_date()
        resolved = _resolve_smart_pool(target_date) if pool_id == "smart_mrgc_20d" else None
        stocks = _stock_pool_members(pool_id, target_date, smart_result=resolved)
        if resolved is not None:
            if _db_table_exists("smart_watchlist_state"):
                pool["smart_state"] = _db_one(
                    "SELECT * FROM smart_watchlist_state WHERE pool_id=:pool_id",
                    {"pool_id": pool_id})
            pool = _dated_smart_metadata(pool, resolved)
        return ApiResponse(
            code=200,
            message="success",
            data={
                "pool": pool,
                "stocks": stocks,
                "trade_date": target_date,
                "count": len(stocks),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取股票池成员失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pools/{pool_id}/stocks", response_model=ApiResponse)
async def add_stock_pool_member(
    pool_id: str,
    payload: StockPoolMemberCreate,
    background_tasks: BackgroundTasks = None,
):
    """向分类池添加股票。重复添加为幂等更新。"""
    try:
        pool = _require_manual_pool(pool_id)
        stock = _require_stock(payload.symbol)
        symbol = stock["symbol"]
        existing_member = _member_row(pool_id, symbol)
        now = _now_text()
        max_order = _db_one(
            "SELECT MAX(sort_order) AS max_order FROM stock_pool_members WHERE pool_id=:pool_id",
            {"pool_id": pool_id},
        ) or {}
        insert_sort_order = payload.sort_order
        if insert_sort_order is None:
            insert_sort_order = int(max_order.get("max_order") or 0) + 10

        _db_execute(
            """
            INSERT INTO stock_pool_members (
                pool_id, symbol, note, sort_order, added_at, updated_at
            )
            VALUES (
                :pool_id, :symbol, :note, :sort_order, :now, :now
            )
            ON CONFLICT(pool_id, symbol) DO UPDATE SET
                note=CASE
                    WHEN :note_was_set = 1 THEN excluded.note
                    ELSE stock_pool_members.note
                END,
                sort_order=CASE
                    WHEN :sort_order_was_set = 1 THEN excluded.sort_order
                    ELSE stock_pool_members.sort_order
                END,
                updated_at=:now
            """,
            {
                "pool_id": pool_id,
                "symbol": symbol,
                "note": payload.note,
                "sort_order": insert_sort_order,
                "note_was_set": 1 if payload.note is not None else 0,
                "sort_order_was_set": 1 if payload.sort_order is not None else 0,
                "now": now,
            },
        )
        refresh_queued = False
        if existing_member is None and background_tasks is not None:
            from data_job.auto_f10 import enqueue_symbols

            background_tasks.add_task(enqueue_symbols, [symbol], engine=engine)
        if existing_member is None and int(pool.get("monitor_enabled") or 0) == 1:
            refresh_queued = _queue_member_refresh(background_tasks, [symbol])
        return ApiResponse(
            code=200,
            message="success",
            data={
                "stock": _member_row(pool_id, symbol),
                "refresh_queued": refresh_queued,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"添加股票池成员失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/pools/{pool_id}/stocks/{symbol}", response_model=ApiResponse)
async def update_stock_pool_member(pool_id: str, symbol: str, payload: StockPoolMemberUpdate):
    """更新分类池内股票备注或排序。"""
    try:
        _require_manual_pool(pool_id)
        normalized = _normalize_symbol(symbol)
        if not _member_row(pool_id, normalized):
            raise HTTPException(status_code=404, detail=f"股票 {normalized} 不在分类池中")

        _db_execute(
            """
            UPDATE stock_pool_members
            SET
                note=COALESCE(:note, note),
                sort_order=COALESCE(:sort_order, sort_order),
                updated_at=:now
            WHERE pool_id=:pool_id AND symbol=:symbol
            """,
            {
                "pool_id": pool_id,
                "symbol": normalized,
                "note": payload.note,
                "sort_order": payload.sort_order,
                "now": _now_text(),
            },
        )
        return ApiResponse(
            code=200,
            message="success",
            data={"stock": _member_row(pool_id, normalized)},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新股票池成员失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pools/{pool_id}/stocks/{symbol}", response_model=ApiResponse)
async def delete_stock_pool_member(pool_id: str, symbol: str):
    """从分类池移除股票。"""
    try:
        _require_manual_pool(pool_id)
        normalized = _normalize_symbol(symbol)
        affected = _db_execute(
            """
            DELETE FROM stock_pool_members
            WHERE pool_id=:pool_id AND symbol=:symbol
            """,
            {"pool_id": pool_id, "symbol": normalized},
        )
        if affected == 0:
            raise HTTPException(status_code=404, detail=f"股票 {normalized} 不在分类池中")
        return ApiResponse(
            code=200,
            message="success",
            data={"deleted": True, "pool_id": pool_id, "symbol": normalized},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"移除股票池成员失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== K线数据 ====================

@router.get("/kline/{symbol}", response_model=ApiResponse)
async def get_stock_kline(
    symbol: str,
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数")
):
    """
    获取股票K线数据

    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        limit: 返回条数（默认最近100条）
    """
    try:
        # 默认返回最近100天
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        conditions = ["symbol = :symbol"]
        params = {"symbol": symbol}

        if start_date:
            conditions.append("trade_date >= :start_date")
            params["start_date"] = start_date

        if end_date:
            conditions.append("trade_date <= :end_date")
            params["end_date"] = end_date

        where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                symbol, trade_date, open, high, low, close,
                volume, amount, pct_chg, turnover_rate
            FROM stock_daily_prices
            {where_clause}
            ORDER BY trade_date DESC
            LIMIT {limit}
        """

        results = fetch_all(query, params)

        # 按日期升序排列
        results = list(reversed(results))

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": symbol,
                "kline": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取K线数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chart/{symbol}", response_model=ApiResponse)
async def get_stock_chart(
    symbol: str,
    period: str = Query("daily", description="周期: daily, weekly, monthly, 30min, 120min"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)，默认数据库最新交易日"),
    limit: int = Query(260, ge=30, le=520, description="返回K线数量"),
):
    """
    获取个股作战室图表数据。

    数据只来自本地数据库：K线、成交量、成交额、RPS、北向持股和基金持股。
    MACD/KDJ 基于真实K线序列计算，不使用前端模拟数据。
    """
    period = str(getattr(period, "default", period)).lower()
    if period not in VALID_CHART_PERIODS:
        raise HTTPException(status_code=400, detail="period 必须是 daily、weekly、monthly、30min 或 120min")

    if period in {"30min", "120min"}:
        try:
            result = await asyncio.to_thread(
                get_minute_chart, "stock", symbol, period,
                getattr(end_date, "default", end_date), int(getattr(limit, "default", limit)),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rows = result.pop("bars", [])
        series = _chart_series(rows)
        # Daily factors and holdings are not minute-frequency observations.
        series.pop("rps_short", None)
        series.pop("rps_long", None)
        series.pop("funds", None)
        return ApiResponse(data={
            **result,
            "symbol": symbol,
            "latest_date": rows[-1]["trade_date"] if rows else None,
            "end_date": end_date,
            "rows": rows,
            "count": len(rows),
            **series,
        })

    try:
        latest_row = fetch_one(
            """
            SELECT DATE(MAX(trade_date)) as latest_date
            FROM stock_daily_prices
            WHERE symbol = :symbol
            """,
            {"symbol": symbol},
        )
        latest_date = latest_row.get("latest_date") if latest_row else None
        chart_end_date = end_date or latest_date or datetime.now().strftime("%Y-%m-%d")

        raw_limit = limit
        if period == "weekly":
            raw_limit = min(1600, limit * 6)
        elif period == "monthly":
            raw_limit = min(2600, limit * 24)

        rps_join = ""
        rps_select = """
            NULL as rps_5,
            NULL as rps_10,
            NULL as rps_20,
            NULL as rps_50,
            NULL as rps_120,
            NULL as rps_250
        """
        if _table_exists("quant_feature_rps"):
            rps_join = """
                LEFT JOIN quant_feature_rps r
                  ON r.symbol = p.symbol
                 AND DATE(r.trade_date) = DATE(p.trade_date)
            """
            rps_select = """
                r.rps_5,
                r.rps_10,
                r.rps_20,
                r.rps_50,
                r.rps_120,
                r.rps_250
            """

        north_join = ""
        north_select = """
            NULL as northbound_hold_ratio,
            NULL as northbound_hold_value
        """
        if _table_exists("stock_northbound_holdings"):
            north_join = """
                LEFT JOIN stock_northbound_holdings n
                  ON n.symbol = p.symbol
                 AND DATE(n.hold_date) = DATE(p.trade_date)
            """
            north_select = """
                n.hold_ratio as northbound_hold_ratio,
                n.hold_value as northbound_hold_value
            """

        query = f"""
            SELECT
                p.symbol,
                DATE(p.trade_date) as trade_date,
                p.open,
                p.high,
                p.low,
                p.close,
                p.volume,
                p.amount,
                p.pct_chg,
                p.turnover_rate,
                {rps_select},
                {north_select}
            FROM stock_daily_prices p
            {rps_join}
            {north_join}
            WHERE p.symbol = :symbol
              AND DATE(p.trade_date) <= DATE(:end_date)
            ORDER BY DATE(p.trade_date) DESC
            LIMIT {raw_limit}
        """
        raw_rows = fetch_all(query, {"symbol": symbol, "end_date": chart_end_date})
        raw_rows = list(reversed(raw_rows))

        rows = []
        for item in raw_rows:
            rows.append({
                "symbol": item.get("symbol"),
                "trade_date": _date_text(item.get("trade_date")),
                "open": _to_float(item.get("open")),
                "high": _to_float(item.get("high")),
                "low": _to_float(item.get("low")),
                "close": _to_float(item.get("close")),
                "volume": _to_float(item.get("volume")),
                "amount": _to_float(item.get("amount")),
                "pct_chg": _to_float(item.get("pct_chg")),
                "turnover_rate": _to_float(item.get("turnover_rate")),
                "rps_5": _to_float(item.get("rps_5")),
                "rps_10": _to_float(item.get("rps_10")),
                "rps_20": _to_float(item.get("rps_20")),
                "rps_50": _to_float(item.get("rps_50")),
                "rps_120": _to_float(item.get("rps_120")),
                "rps_250": _to_float(item.get("rps_250")),
                "northbound_hold_ratio": _to_float(item.get("northbound_hold_ratio")),
                "northbound_hold_value": _to_float(item.get("northbound_hold_value")),
            })

        if rows:
            fund_reports = _fund_holding_reports(symbol, chart_end_date)
            _attach_fund_reports(rows, fund_reports)
            rows = _aggregate_chart_rows(rows, period)[-limit:]

        series = _chart_series(rows)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": symbol,
                "period": period,
                "latest_date": latest_date,
                "end_date": chart_end_date,
                "rows": rows,
                "count": len(rows),
                **series,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取个股图表数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 个股重资料缓存 ====================

@router.get("/enrichment/{symbol}", response_model=ApiResponse)
async def get_stock_enrichment(symbol: str):
    """读取个股资料面板缓存。

    该接口只读 SQLite，不触发外部数据源请求。缓存为空时返回空数组和 freshness 状态。
    """
    code = _normalize_symbol(symbol)
    try:
        _ensure_enrichment_tables()
        info = _db_one("SELECT * FROM stock_info WHERE symbol=:symbol", {"symbol": code})
        if not info:
            raise HTTPException(status_code=404, detail=f"股票 {code} 不存在")

        valuation = _db_one(
            """
            SELECT *
            FROM stock_valuation_daily
            WHERE code=:symbol
            ORDER BY DATE(trade_date) DESC
            LIMIT 1
            """,
            {"symbol": code},
        ) if _db_table_exists("stock_valuation_daily") else None
        if valuation:
            if _to_float(valuation.get("pe_ttm")) is None and _to_float(info.get("pe")) is not None:
                valuation["pe_ttm"] = _to_float(info.get("pe"))
            if _to_float(valuation.get("pb")) is None and _to_float(info.get("pb")) is not None:
                valuation["pb"] = _to_float(info.get("pb"))
        elif info:
            valuation = {
                "code": code,
                "name": info.get("name"),
                "trade_date": None,
                "pe_ttm": _to_float(info.get("pe")),
                "pb": _to_float(info.get("pb")),
                "total_mv": None,
                "circ_mv": None,
                "source": "stock_info",
            }

        finance_snapshot = _latest_row(
            "stock_finance_snapshot_tdx",
            "symbol=:symbol",
            {"symbol": code},
            "report_date",
        )
        f10_sections = _limited_rows(
            "stock_f10_sections",
            "symbol=:symbol",
            {"symbol": code},
            "category",
            20,
        )
        financial_reports = _limited_rows(
            "stock_financial_reports",
            "symbol=:symbol",
            {"symbol": code},
            "report_date",
            40,
        )
        finance_summary = _finance_summary_rows(code, 12)
        announcements = _limited_rows(
            "stock_announcements_cninfo",
            "symbol=:symbol",
            {"symbol": code},
            "publish_date",
            30,
        )
        fund_flow = _limited_rows(
            "stock_fund_flow_daily",
            "symbol=:symbol",
            {"symbol": code},
            "trade_date",
            120,
        )
        concepts = _limited_rows(
            "stock_concept_blocks",
            "symbol=:symbol",
            {"symbol": code},
            "block_name",
            30,
        )
        stock_research = _limited_rows(
            "stock_research_reports",
            "symbol=:symbol",
            {"symbol": code},
            "publish_date",
            20,
        )
        industry_name = (info or {}).get("industry") or ""
        industry_research = _limited_rows(
            "industry_research_reports",
            "industry_name=:industry_name",
            {"industry_name": industry_name},
            "publish_date",
            20,
        ) if industry_name else []
        dragon_tiger = _limited_rows(
            "stock_dragon_tiger_records",
            "symbol=:symbol",
            {"symbol": code},
            "trade_date",
            20,
        )
        margin = _limited_rows(
            "stock_margin_trading",
            "symbol=:symbol",
            {"symbol": code},
            "trade_date",
            60,
        )
        block_trades = _limited_rows(
            "stock_block_trades",
            "symbol=:symbol",
            {"symbol": code},
            "trade_date",
            30,
        )
        holder_num = _limited_rows(
            "stock_holder_num",
            "symbol=:symbol",
            {"symbol": code},
            "report_date",
            20,
        )
        dividends = _limited_rows(
            "stock_dividend_history",
            "symbol=:symbol",
            {"symbol": code},
            "ex_dividend_date",
            20,
        )
        lockups = _limited_rows(
            "stock_lockup_expiry",
            "symbol=:symbol",
            {"symbol": code},
            "free_date",
            30,
        )

        for row in financial_reports:
            row["payload"] = _load_json(row.pop("payload_json", None))
        for collection in (
            [finance_snapshot] if finance_snapshot else [],
            f10_sections,
            stock_research,
            industry_research,
            announcements,
            fund_flow,
            dragon_tiger,
            margin,
            block_trades,
            holder_num,
            dividends,
            lockups,
        ):
            for row in collection:
                if "raw_json" in row:
                    row["raw"] = _load_json(row.pop("raw_json", None))

        freshness = [
            _table_freshness("stock_valuation_daily", symbol=code, symbol_column="code", date_column="trade_date"),
            _table_freshness("stock_finance_snapshot_tdx", symbol=code, date_column="report_date"),
            _table_freshness("stock_f10_sections", symbol=code),
            _table_freshness("stock_financial_reports", symbol=code, date_column="report_date"),
            _table_freshness("stock_finance_summary", symbol=code, symbol_column="code", date_column="report_date"),
            _table_freshness("stock_concept_blocks", symbol=code),
            _table_freshness("stock_research_reports", symbol=code, date_column="publish_date"),
            _table_freshness("industry_research_reports", date_column="publish_date"),
            _table_freshness("stock_announcements_cninfo", symbol=code, date_column="publish_date"),
            _table_freshness("stock_fund_flow_daily", symbol=code, date_column="trade_date"),
            _table_freshness("stock_dragon_tiger_records", symbol=code, date_column="trade_date"),
            _table_freshness("stock_margin_trading", symbol=code, date_column="trade_date"),
            _table_freshness("stock_block_trades", symbol=code, date_column="trade_date"),
            _table_freshness("northbound_flow_daily", date_column="trade_date"),
            _table_freshness("stock_holder_num", symbol=code, date_column="report_date"),
            _table_freshness("stock_dividend_history", symbol=code, date_column="ex_dividend_date"),
            _table_freshness("stock_lockup_expiry", symbol=code, date_column="free_date"),
        ]

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": code,
                "info": info,
                "profile": {
                    "info": info,
                    "valuation": valuation,
                    "concepts": concepts,
                },
                "finance": {
                    "snapshot": finance_snapshot,
                    "reports": financial_reports,
                    "summary": finance_summary,
                },
                "research": {
                    "stock_reports": stock_research,
                    "industry_reports": industry_research,
                },
                "f10_sections": f10_sections,
                "announcements": announcements,
                "funds": {
                    "fund_flow": fund_flow,
                    "dragon_tiger": dragon_tiger,
                    "margin": margin,
                    "block_trades": block_trades,
                    "northbound_flow": _limited_rows(
                        "northbound_flow_daily",
                        "1=1",
                        {},
                        "trade_date",
                        20,
                    ),
                },
                "risk": {
                    "lockups": lockups,
                    "holder_num": holder_num,
                    "dividends": dividends,
                },
                "freshness": freshness,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取个股资料缓存失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enrichment/{symbol}/refresh", response_model=ApiResponse)
async def refresh_stock_enrichment(symbol: str, payload: StockEnrichmentRefreshRequest):
    """按需刷新单只股票资料缓存。"""
    code = _normalize_symbol(symbol)
    try:
        info = _db_one("SELECT symbol FROM stock_info WHERE symbol=:symbol", {"symbol": code})
        if not info:
            raise HTTPException(status_code=404, detail=f"股票 {code} 不存在")

        from data_job.collectors.stock_enrichment_collector import StockEnrichmentCollector

        collector = StockEnrichmentCollector()
        collector.engine = engine
        statuses = await run_in_threadpool(
            collector.refresh_symbol,
            code,
            modules=payload.modules,
            trade_date=payload.trade_date,
        )
        cached = await get_stock_enrichment(code)
        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": code,
                "refresh": statuses,
                "cache": cached.data,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"刷新个股资料缓存失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 估值数据 ====================

@router.get("/valuation/{symbol}", response_model=ApiResponse)
async def get_stock_valuation(
    symbol: str,
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(30, ge=1, le=365, description="返回条数")
):
    """
    获取股票估值数据

    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        limit: 返回条数（默认最近30天）
    """
    try:
        # 默认返回最近30天
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        conditions = ["code = :code"]
        params = {"code": symbol}

        if start_date:
            conditions.append("trade_date >= :start_date")
            params["start_date"] = start_date

        if end_date:
            conditions.append("trade_date <= :end_date")
            params["end_date"] = end_date

        where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                code, trade_date, pe_ttm, pb, total_mv, circ_mv
            FROM stock_valuation_daily
            {where_clause}
            ORDER BY trade_date DESC
            LIMIT {limit}
        """

        results = fetch_all(query, params)

        # 按日期升序排列
        results = list(reversed(results))

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": symbol,
                "valuation": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取估值数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 财务数据 ====================

@router.get("/finance/{symbol}", response_model=ApiResponse)
async def get_stock_finance(
    symbol: str,
    limit: Annotated[
        int,
        Query(ge=1, le=32, description="兼容财务摘要返回条数"),
    ] = 8,
    years: Annotated[
        int,
        Query(ge=1, le=10, description="业绩序列包含的自然年数"),
    ] = 6,
    as_of: Annotated[
        Optional[date],
        Query(description="数据截止日 (YYYY-MM-DD)"),
    ] = None,
):
    """读取本地财务缓存，同时返回兼容摘要与年度/单季业绩序列。"""
    try:
        code = _normalize_symbol(symbol)
        if isinstance(as_of, datetime):
            as_of_date = as_of.date()
        elif isinstance(as_of, date):
            as_of_date = as_of
        elif as_of:
            try:
                as_of_date = date.fromisoformat(str(as_of)[:10])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="as_of 必须是 YYYY-MM-DD") from exc
        else:
            as_of_date = date.today()

        start_date = date(as_of_date.year - years + 1, 1, 1)
        performance_rows = _finance_summary_rows(
            code,
            max(limit, years * 4),
            start_date=start_date.isoformat(),
            as_of=as_of_date.isoformat(),
        )
        results = performance_rows[:limit]

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": code,
                "finance": results,
                "count": len(results),
                "performance": _finance_performance(
                    performance_rows,
                    as_of_date.isoformat(),
                ),
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取财务数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 资金流向 ====================

@router.get("/flow/{symbol}", response_model=ApiResponse)
async def get_stock_flow(
    symbol: str,
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(30, ge=1, le=365, description="返回条数")
):
    """
    获取股票资金流向数据

    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        limit: 返回条数
    """
    try:
        # 检查是否存在资金流向表
        check_query = """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='stock_capital_flow'
        """
        table_exists = fetch_one(check_query)

        if not table_exists:
            return ApiResponse(
                code=200,
                message="资金流向数据表不存在",
                data={"symbol": symbol, "flow": [], "count": 0}
            )

        # 默认返回最近30天
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        conditions = ["symbol = :symbol"]
        params = {"symbol": symbol}

        if start_date:
            conditions.append("trade_date >= :start_date")
            params["start_date"] = start_date

        if end_date:
            conditions.append("trade_date <= :end_date")
            params["end_date"] = end_date

        where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                symbol, trade_date,
                main_inflow, main_outflow, main_net,
                retail_inflow, retail_outflow, retail_net
            FROM stock_capital_flow
            {where_clause}
            ORDER BY trade_date DESC
            LIMIT {limit}
        """

        results = fetch_all(query, params)
        results = list(reversed(results))

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": symbol,
                "flow": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取资金流向失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 综合查询 ====================

@router.get("/overview/{symbol}", response_model=ApiResponse)
async def get_stock_overview(symbol: str):
    """
    获取股票综合概览（信息 + 最新K线 + 最新估值）

    Args:
        symbol: 股票代码
    """
    try:
        # 获取基本信息
        info_query = """
            SELECT * FROM stock_info WHERE symbol = :symbol
        """
        stock_info = fetch_one(info_query, {"symbol": symbol})

        if not stock_info:
            raise HTTPException(status_code=404, detail=f"股票 {symbol} 不存在")

        # 获取最新K线（最近5天）
        kline_query = """
            SELECT
                trade_date, open, high, low, close, volume, pct_chg
            FROM stock_daily_prices
            WHERE symbol = :symbol
            ORDER BY trade_date DESC
            LIMIT 5
        """
        kline_data = fetch_all(kline_query, {"symbol": symbol})
        kline_data = list(reversed(kline_data))

        # 获取最新估值
        valuation_query = """
            SELECT
                trade_date, pe_ttm, pb, total_mv, circ_mv
            FROM stock_valuation_daily
            WHERE code = :symbol
            ORDER BY trade_date DESC
            LIMIT 1
        """
        valuation_data = fetch_one(valuation_query, {"symbol": symbol})

        return ApiResponse(
            code=200,
            message="success",
            data={
                "info": stock_info,
                "latest_kline": kline_data,
                "latest_valuation": valuation_data
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取股票概览失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
