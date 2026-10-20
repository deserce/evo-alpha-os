"""按需、只读的结构转折候选；与正式 Watch 规则和通知隔离。"""

from contextlib import closing
from datetime import date, timedelta
import logging
import re
import sqlite3

from fastapi import APIRouter, HTTPException

from app.api.v1.schemas import ApiResponse
from quant_engine.watch.engine import DB_PATH
from quant_engine.research.structure_turning import evaluate_structure_turning
from quant_engine.watch.structure_monitor import HISTORY_ANCHOR, MAX_BARS, StructureMonitor

router = APIRouter(prefix="/watch", tags=["结构候选"])
logger = logging.getLogger(__name__)
MAX_RANGE_DAYS = 1095


@router.get("/structure-candidate", response_model=ApiResponse)
def get_structure_candidate(symbol: str, start_date: str, end_date: str):
    """只回放当前请求的一只股票，禁止创建扫描、交易或通知记录。"""
    if not re.fullmatch(r"[0-9]{6}", symbol):
        raise HTTPException(status_code=422, detail="symbol 必须是六位股票代码")
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="日期必须是 YYYY-MM-DD") from exc
    if (start.isoformat() != start_date or end.isoformat() != end_date
            or start_date < HISTORY_ANCHOR or end == date.max
            or start > end or (end - start).days > MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422,
            detail="日期需为YYYY-MM-DD，起点不早于2023-01-01，升序且跨度不超过三年",
        )
    try:
        with closing(sqlite3.connect(
            DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=2
        )) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT substr(trade_date,1,10) AS trade_date, open, high, low, close, volume "
                "FROM stock_daily_prices WHERE symbol=? AND trade_date>=? AND trade_date<? "
                "ORDER BY trade_date LIMIT ?",
                (symbol, HISTORY_ANCHOR, (end + timedelta(days=1)).isoformat(), MAX_BARS + 1),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("结构候选行情读取失败: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="候选行情暂不可用，请稍后重试") from exc
    if len(rows) > MAX_BARS:
        raise HTTPException(status_code=422, detail="固定历史起点至结束日超过1500根上限，请选择更早结束日")
    try:
        result = evaluate_structure_turning([dict(row) for row in rows])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"行情质量未通过：{exc}") from exc
    result["markers"] = [
        marker for marker in result["markers"]
        if start_date <= marker["trade_date"] <= end_date
    ]
    result["trades"] = [
        trade for trade in result["trades"]
        if trade["entry_date"] <= end_date
        and (trade.get("exit_date") is None or trade["exit_date"] >= start_date)
    ]
    return ApiResponse(data={
        **result,
        "symbol": symbol,
        "experimental": True,
        "requested_start": start_date,
        "requested_end": end_date,
        "history_start": rows[0]["trade_date"] if rows else None,
        "history_end": rows[-1]["trade_date"] if rows else None,
        "history_anchor": HISTORY_ANCHOR,
        "summary_scope": "含热身区间；图表标记仅显示请求区间",
    })


@router.get("/structure-monitor", response_model=ApiResponse)
def get_structure_monitor(
    trade_date: str | None = None, symbol: str | None = None, pool_id: str | None = None,
):
    """Read exact-date C monitoring snapshots; never trigger calculation or notifications."""
    if symbol is not None and not re.fullmatch(r"[0-9]{6}", symbol):
        raise HTTPException(status_code=422, detail="symbol 必须是六位股票代码")
    if pool_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", pool_id):
        raise HTTPException(status_code=422, detail="pool_id 格式无效")
    if trade_date is not None:
        try:
            parsed = date.fromisoformat(trade_date)
            if parsed.isoformat() != trade_date or trade_date < HISTORY_ANCHOR or parsed == date.max:
                raise ValueError("invalid date")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="日期必须是2023-01-01起的YYYY-MM-DD") from exc
    try:
        result = StructureMonitor(DB_PATH).read_status(
            trade_date, symbol=symbol, pool_id=pool_id,
        )
    except (sqlite3.Error, ValueError) as exc:
        logger.warning("C盘后监控读取失败: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="C盘后监控暂不可用，请稍后重试") from exc
    return ApiResponse(data={**result, "simulated": True, "execution": "none"})
