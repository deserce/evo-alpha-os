"""On-demand multi-period stock chart signals, isolated from formal Watch writes."""
from contextlib import closing
from datetime import date, datetime, timedelta
import asyncio
import logging
import re
import sqlite3
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from app.api.v1.schemas import ApiResponse
from app.api.v1.stock import get_stock_chart
from quant_engine.watch.engine import DB_PATH
from quant_engine.watch.chart_replay import PERIODS, load_context, read_rules, replay_ab
from quant_engine.research.structure_turning import evaluate_structure_turning
from data_core.market_calendar import is_a_share_session

router = APIRouter(prefix="/watch", tags=["图表策略回放"])
logger = logging.getLogger(__name__)


def complete_rows(rows: list[dict], period: str, cutoff: str) -> list[dict]:
    """Exclude forming calendar buckets; minute service already supplies closed bars."""
    if period not in {"weekly", "monthly", "daily"}:
        return rows
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    boundary = min(date.fromisoformat(cutoff), now.date())
    accepted = []
    for row in rows:
        day = date.fromisoformat(str(row["trade_date"])[:10])
        bucket_end = day
        if period == "weekly":
            bucket_end = day + timedelta(days=4-day.weekday())
        elif period == "monthly":
            next_month = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
            bucket_end = next_month - timedelta(days=1)
        while not is_a_share_session(bucket_end):
            bucket_end -= timedelta(days=1)
        if bucket_end < boundary or (bucket_end == boundary and
                (boundary < now.date() or now.hour >= 15)):
            accepted.append(row)
    return accepted


def calculate(symbol: str, period: str, rows: list[dict]) -> dict:
    with closing(sqlite3.connect(DB_PATH.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        rules, source = read_rules(conn)
        contexts, sessions = load_context(conn, symbol, rows)
        result = replay_ab(conn, symbol, period, rows, contexts, sessions, rules)
    structure = evaluate_structure_turning(rows, period=period)
    latest_blocked = result["blocked"][-1] if result["blocked"] else None
    ready = result["latest"] is not None and (not latest_blocked or latest_blocked["trade_date"] < rows[-1]["trade_date"])
    message = None if ready else {"warmup": "A/B至少需要60根完成K线暖机",
            "daily_context_missing": "A/B所需日线背景缺失，已阻断",
            "no_complete_bars": "暂无完整K线可回放"}.get(
                (latest_blocked or {}).get("reason", "no_complete_bars"), "策略背景暂不可用")
    if ready and result["latest"]["path_status"]["B"] == "BLOCKED":
        message = "B修复所需日线背景不完整，B路径已阻断"
    return {**result, "structure": structure, "rule_version": rules.version,
            "rules_source": source, "status": "OK" if ready else "BLOCKED",
            "message": message,
            "context_date": result["context_date"]}


@router.get("/chart-signals", response_model=ApiResponse)
async def get_chart_signals(
    symbol: str, period: str, end_date: str | None = None,
    limit: int = Query(260, ge=30, le=520),
):
    """Reuse the chart's bounded source and cache; no schedules, schema or writes."""
    limit = int(getattr(limit, "default", limit))
    if not re.fullmatch(r"[0-9]{6}", symbol) or period not in PERIODS or not 30 <= limit <= 520:
        raise HTTPException(status_code=422, detail="股票、周期或数量无效")
    if end_date is not None:
        try:
            if date.fromisoformat(end_date).isoformat() != end_date:
                raise ValueError("date")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="end_date 必须是 YYYY-MM-DD") from exc
    chart = await get_stock_chart(symbol, period, end_date, limit)
    payload = chart.data or {}
    rows = complete_rows(payload.get("rows", []), period, end_date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    try:
        result = await asyncio.to_thread(calculate, symbol, period, rows)
    except (sqlite3.Error, ValueError) as exc:
        logger.warning("图表策略回放失败: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="图表策略数据暂不可用") from exc
    if not rows and payload.get("message"):
        result["message"] = payload["message"]
    return ApiResponse(data={**result, "symbol": symbol, "period": period,
                             "chart_only": True, "context_period": "daily",
                             "window_unit": "bars", "count": len(rows)})
