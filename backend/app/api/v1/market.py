"""
市场统计相关API路由
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, Any
from datetime import datetime, timedelta
import asyncio
import logging
import math
logger = logging.getLogger(__name__)

from app.api.v1.database import fetch_one, fetch_all, paginate
from app.api.v1.market_resolver import market_priority, pick_market_row
from app.api.v1.schemas import ApiResponse
from app.core.minute_chart import get_minute_chart

router = APIRouter(prefix="/market", tags=["市场统计"])


CORE_MARKET_INDEX_CODES = ["000001", "000300", "000852", "399006", "000688", "399317"]
INDEX_DISPLAY_NAMES = {
    "000001": "上证指数",
    "000300": "沪深300",
    "000852": "中证1000",
    "399006": "创业板指",
    "000688": "科创50",
    "399317": "国证A指",
}
def _safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    """将数据库数值安全转为 Python float，避免 NaN 泄漏到 JSON。"""
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _safe_int(value: Any, default: int = 0) -> int:
    return int(round(_safe_float(value, float(default))))


def _format_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _table_exists(table_name: str) -> bool:
    row = fetch_one(
        "SELECT 1 AS exists_flag FROM sqlite_master WHERE type='table' AND name=:table_name LIMIT 1",
        {"table_name": table_name},
    )
    return bool(row)


def _resolve_market_date(trade_date: Optional[str]) -> str:
    """
    解析市场页交易日期。

    date=latest 或空值时取 market_strength_daily / index_daily / stock_daily_prices 中最近日期；
    指定日期时回退到不晚于该日的最近可用交易日。
    """
    requested = None
    if trade_date and trade_date != "latest":
        try:
            requested = datetime.strptime(trade_date[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"无效交易日期: {trade_date}") from exc

    candidates = [
        ("market_strength_daily", "trade_date"),
        ("index_daily", "trade_date"),
        ("stock_daily_prices", "trade_date"),
        ("market_daily_stats", "trade_date"),
    ]

    for table_name, date_column in candidates:
        if not _table_exists(table_name):
            continue
        if requested:
            row = fetch_one(
                f"""
                SELECT MAX(DATE({date_column})) AS trade_date
                FROM {table_name}
                WHERE DATE({date_column}) <= DATE(:trade_date)
                """,
                {"trade_date": requested},
            )
        else:
            row = fetch_one(
                f"SELECT MAX(DATE({date_column})) AS trade_date FROM {table_name}"
            )
        if row and row.get("trade_date"):
            return str(row["trade_date"])

    if requested:
        return requested
    raise HTTPException(status_code=404, detail="暂无可用市场数据")


def _resolve_index_date(trade_date: Optional[str]) -> str:
    """解析指数交易日期，只以 index_daily 的可用日期为准。"""
    requested = None
    if trade_date and trade_date != "latest":
        try:
            requested = datetime.strptime(trade_date[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"无效交易日期: {trade_date}") from exc

    if not _table_exists("index_daily"):
        if requested:
            return requested
        raise HTTPException(status_code=404, detail="暂无可用指数数据")

    if requested:
        row = fetch_one(
            """
            SELECT MAX(DATE(trade_date)) AS trade_date
            FROM index_daily
            WHERE DATE(trade_date) <= DATE(:trade_date)
            """,
            {"trade_date": requested},
        )
    else:
        row = fetch_one("SELECT MAX(DATE(trade_date)) AS trade_date FROM index_daily")

    if row and row.get("trade_date"):
        return str(row["trade_date"])
    if requested:
        return requested
    raise HTTPException(status_code=404, detail="暂无可用指数数据")


def _priority(row: dict) -> int:
    return market_priority(row.get("index_code"))


def _dedupe_market_rows(rows: list[dict]) -> list[dict]:
    """market_strength_daily 每个指数一行；市场级历史只保留优先级最高的一行。"""
    selected: dict[str, dict] = {}
    for row in rows:
        date = _format_date(row.get("trade_date"))
        if not date:
            continue
        current = selected.get(date)
        if current is None or _priority(row) < _priority(current):
            selected[date] = row
    return [selected[date] for date in sorted(selected.keys())]


def _latest_market_row(trade_date: str) -> Optional[dict]:
    if not _table_exists("market_strength_daily"):
        return None
    rows = fetch_all(
        """
        SELECT trade_date, index_code, index_name, close, ma_20, ma_50, ma_120,
               above_ma20, above_ma50, up_count, down_count,
               new_high_20_count, new_high_60_count, new_high_120_count,
               limit_up_count, limit_down_count, total_amount, amount_ratio_20,
               rps90_count, market_strength_score, market_state, created_at
        FROM market_strength_daily
        WHERE DATE(trade_date) <= DATE(:trade_date)
        ORDER BY DATE(trade_date) DESC
        LIMIT 12
        """,
        {"trade_date": trade_date},
    )
    if not rows:
        return None
    latest_date = _format_date(rows[0].get("trade_date"))
    same_day_rows = [row for row in rows if _format_date(row.get("trade_date")) == latest_date]
    return pick_market_row(same_day_rows) if same_day_rows else rows[0]


def _market_history(trade_date: str, days: int) -> list[dict]:
    if not _table_exists("market_strength_daily"):
        return []
    rows = fetch_all(
        """
        SELECT trade_date, index_code, index_name, up_count, down_count,
               new_high_20_count, new_high_60_count, new_high_120_count,
               limit_up_count, limit_down_count, total_amount, amount_ratio_20,
               rps90_count, market_strength_score, market_state, created_at
        FROM market_strength_daily
        WHERE DATE(trade_date) <= DATE(:trade_date)
        ORDER BY DATE(trade_date) DESC
        LIMIT :limit
        """,
        {"trade_date": trade_date, "limit": max(days * 6, days)},
    )
    return _dedupe_market_rows(rows)[-days:]


def _rolling_mean(values: list[Optional[float]], end_index: int, window: int) -> Optional[float]:
    if end_index + 1 < window:
        return None
    window_values = [value for value in values[end_index + 1 - window:end_index + 1] if value is not None]
    if len(window_values) < window:
        return None
    return round(sum(window_values) / window, 4)


def _load_index_items(index_code: str, trade_date: str, days: int) -> list[dict]:
    if not _table_exists("index_daily"):
        return []
    load_limit = max(days + 260, days)
    rows = fetch_all(
        """
        SELECT index_code, index_name, trade_date, open, high, low, close,
               volume, amount, pct_chg
        FROM index_daily
        WHERE index_code = :index_code
          AND DATE(trade_date) <= DATE(:trade_date)
        ORDER BY DATE(trade_date) DESC
        LIMIT :limit
        """,
        {"index_code": index_code, "trade_date": trade_date, "limit": load_limit},
    )
    rows = list(reversed(rows))
    closes = [_safe_float(row.get("close"), None) for row in rows]

    items = []
    for index, row in enumerate(rows):
        close = _safe_float(row.get("close"))
        item = {
            "tradeDate": _format_date(row.get("trade_date")),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": close,
            "volume": _safe_float(row.get("volume")),
            "amount": _safe_float(row.get("amount")),
            "pctChange": _safe_float(row.get("pct_chg")),
            "ma20": _rolling_mean(closes, index, 20),
            "ma50": _rolling_mean(closes, index, 50),
            "ma120": _rolling_mean(closes, index, 120),
            "ma250": _rolling_mean(closes, index, 250),
        }
        items.append(item)
    return items[-days:]


def _return_pct(items: list[dict], period: int) -> Optional[float]:
    if len(items) <= period:
        return None
    latest = _safe_float(items[-1].get("close"))
    previous = _safe_float(items[-1 - period].get("close"))
    if previous <= 0:
        return None
    return round((latest - previous) / previous * 100, 2)


def _market_indices(trade_date: str) -> list[dict]:
    indices = []
    for index_code in CORE_MARKET_INDEX_CODES:
        items = _load_index_items(index_code, trade_date, 260)
        if not items:
            continue
        latest = items[-1]
        close = _safe_float(latest.get("close"))
        ma20 = latest.get("ma20")
        ma50 = latest.get("ma50")
        ma120 = latest.get("ma120")
        ma250 = latest.get("ma250")
        above_ma20 = bool(ma20 is not None and close >= _safe_float(ma20))
        above_ma50 = bool(ma50 is not None and close >= _safe_float(ma50))
        above_ma120 = bool(ma120 is not None and close >= _safe_float(ma120))
        above_ma250 = bool(ma250 is not None and close >= _safe_float(ma250))
        above_count = sum([above_ma20, above_ma50, above_ma120])
        trend_state = "strong" if above_count >= 2 else "neutral" if above_count == 1 else "weak"
        index_name = latest.get("index_name") or INDEX_DISPLAY_NAMES.get(index_code, index_code)
        indices.append({
            "indexCode": index_code,
            "indexName": index_name,
            "tradeDate": latest.get("tradeDate"),
            "close": close,
            "pctChange": _safe_float(latest.get("pctChange")),
            "amount": _safe_float(latest.get("amount")),
            "ma20": ma20,
            "ma50": ma50,
            "ma120": ma120,
            "ma250": ma250,
            "aboveMa20": above_ma20,
            "aboveMa50": above_ma50,
            "aboveMa120": above_ma120,
            "aboveMa250": above_ma250,
            "trendState": trend_state,
            "return20d": _return_pct(items, 20),
            "return50d": _return_pct(items, 50),
            "return120d": _return_pct(items, 120),
        })
    return indices


def _trading_permission(score: float) -> str:
    if score >= 80:
        return "active"
    if score >= 65:
        return "normal"
    if score >= 50:
        return "limited"
    if score >= 35:
        return "observe"
    return "forbidden"


def _sector_distribution(rows: list[dict], sector_key: str = "industry") -> list[dict]:
    counts: dict[str, int] = {}
    for row in rows:
        sector = str(row.get(sector_key) or "未分类")
        counts[sector] = counts.get(sector, 0) + 1
    total = sum(counts.values())
    return [
        {
            "sector": sector,
            "count": count,
            "percentage": round(count / total * 100, 2) if total else 0,
        }
        for sector, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


def _limit_stock_rows(rows: list[dict], limit: int = 10) -> list[dict]:
    result = []
    for row in rows[:limit]:
        industry = str(row.get("industry") or "未分类")
        result.append(
            {
                "code": row.get("symbol"),
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "reason": industry,
                "sector": industry,
                "latest_price": _safe_float(row.get("latest_price")),
                "pct_chg": _safe_float(row.get("pct_chg")),
                "boards": _safe_int(row.get("boards"), 0),
                "amount": _safe_float(row.get("amount")),
                "seal_amount": _safe_float(row.get("seal_amount")),
                "first_limit_time": row.get("first_limit_time"),
                "last_limit_time": row.get("last_limit_time"),
            }
        )
    return result


def _resolve_limit_board_date(trade_date: Optional[str]) -> str:
    requested = None
    if trade_date and trade_date != "latest":
        try:
            requested = datetime.strptime(trade_date[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"无效交易日期: {trade_date}") from exc

    for table_name in ("limit_board_trading", "limit_down_trading", "consecutive_boards_stats"):
        if not _table_exists(table_name):
            continue
        if requested:
            row = fetch_one(
                f"""
                SELECT MAX(DATE(trade_date)) AS trade_date
                FROM {table_name}
                WHERE DATE(trade_date) <= DATE(:trade_date)
                """,
                {"trade_date": requested},
            )
        else:
            row = fetch_one(f"SELECT MAX(DATE(trade_date)) AS trade_date FROM {table_name}")
        if row and row.get("trade_date"):
            return str(row["trade_date"])

    if requested:
        return requested
    raise HTTPException(status_code=404, detail="暂无涨跌停数据")


def _relative_news_time(value: Any) -> str:
    raw = str(value or "")
    if len(raw) >= 16:
        return raw[11:16]
    if len(raw) >= 10:
        return raw[:10]
    return raw or "-"


def _risk_notes(market: dict) -> list[dict]:
    up_count = _safe_int(market.get("up_count"))
    down_count = _safe_int(market.get("down_count"))
    total = up_count + down_count
    up_ratio = up_count / total if total else 0
    limit_down_count = _safe_int(market.get("limit_down_count"))
    amount_ratio = _safe_float(market.get("amount_ratio_20"), 1.0)
    rps90_count = _safe_int(market.get("rps90_count"))
    new_high_60_count = _safe_int(market.get("new_high_60_count"))

    notes = []
    if up_ratio >= 0.58:
        notes.append({
            "level": "low",
            "title": "市场宽度良好",
            "description": f"上涨占比 {up_ratio * 100:.1f}%，扩散状态支持交易。",
        })
    elif up_ratio >= 0.45:
        notes.append({
            "level": "medium",
            "title": "市场宽度中性",
            "description": f"上涨占比 {up_ratio * 100:.1f}%，需要优先选择强行业。",
        })
    else:
        notes.append({
            "level": "high",
            "title": "市场宽度偏弱",
            "description": f"上涨占比 {up_ratio * 100:.1f}%，新开仓应明显降速。",
        })

    notes.append({
        "level": "high" if limit_down_count >= 30 else "medium" if limit_down_count >= 10 else "low",
        "title": "跌停风险",
        "description": f"跌停数量 {limit_down_count} 只，亏钱效应{'偏高' if limit_down_count >= 30 else '可控' if limit_down_count < 10 else '需要观察'}。",
    })

    notes.append({
        "level": "low" if amount_ratio >= 1.0 else "medium" if amount_ratio >= 0.75 else "high",
        "title": "成交额状态",
        "description": f"成交额为 20 日均量的 {amount_ratio:.2f} 倍。",
    })

    notes.append({
        "level": "low" if rps90_count >= 500 and new_high_60_count >= 20 else "medium",
        "title": "强势扩散",
        "description": f"RPS90 数量 {rps90_count}，60日新高 {new_high_60_count}。",
    })
    return notes


def _hero(market: dict, previous_market: Optional[dict], trade_date: str) -> dict:
    score = round(_safe_float(market.get("market_strength_score"), 50), 2)
    state = str(market.get("market_state") or "M2")
    previous_state = str(previous_market.get("market_state")) if previous_market else state
    state_order = {"M0": 0, "M1": 1, "M2": 2, "M3": 3, "M4": 4}
    if state_order.get(state, 2) > state_order.get(previous_state, 2):
        state_change = "upgrade"
    elif state_order.get(state, 2) < state_order.get(previous_state, 2):
        state_change = "downgrade"
    else:
        state_change = "stable"

    permission_map = {
        "forbidden": "暂停新开仓",
        "observe": "观察为主",
        "limited": "小仓参与",
        "normal": "允许正常参与",
        "active": "允许积极参与",
    }
    permission = _trading_permission(score)
    notes = _risk_notes(market)
    return {
        "tradeDate": trade_date,
        "marketState": state,
        "marketStrengthScore": score,
        "previousMarketState": previous_state,
        "stateChange": state_change,
        "suggestedPositionMin": max(0, math.floor(score * 0.6)),
        "suggestedPositionMax": min(100, math.floor(score * 0.9)),
        "tradingPermission": permission,
        "summary": f"市场处于 {state}，强度分 {score:.2f}，{permission_map[permission]}。优先从强势行业与 S4/S5 标的中筛选机会。",
        "riskNotes": notes,
        "updatedAt": str(market.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    }


def _previous_market_row(trade_date: str) -> Optional[dict]:
    if not _table_exists("market_strength_daily"):
        return None
    rows = fetch_all(
        """
        SELECT trade_date, index_code, market_state, market_strength_score
        FROM market_strength_daily
        WHERE DATE(trade_date) < DATE(:trade_date)
        ORDER BY DATE(trade_date) DESC
        LIMIT 12
        """,
        {"trade_date": trade_date},
    )
    if not rows:
        return None
    latest_date = _format_date(rows[0].get("trade_date"))
    same_day_rows = [row for row in rows if _format_date(row.get("trade_date")) == latest_date]
    return pick_market_row(same_day_rows) if same_day_rows else rows[0]


def _build_histories(history_rows: list[dict]) -> dict:
    amounts = [_safe_float(row.get("total_amount")) for row in history_rows]
    amount_ma20 = []
    for index, _ in enumerate(amounts):
        start = max(0, index - 19)
        window = amounts[start:index + 1]
        amount_ma20.append(sum(window) / len(window) if window else 0)

    breadth_history = []
    limit_history = []
    strength_history = []
    amount_history = []

    for index, row in enumerate(history_rows):
        trade_date = _format_date(row.get("trade_date"))
        up_count = _safe_int(row.get("up_count"))
        down_count = _safe_int(row.get("down_count"))
        total_count = up_count + down_count
        up_ratio = round(up_count / total_count * 100, 2) if total_count else 0
        total_amount = _safe_float(row.get("total_amount"))
        ma20 = amount_ma20[index]
        ratio = _safe_float(row.get("amount_ratio_20"), total_amount / ma20 if ma20 else 1.0)

        breadth_history.append({
            "tradeDate": trade_date,
            "upCount": up_count,
            "downCount": down_count,
            "upRatio": up_ratio,
        })
        limit_history.append({
            "tradeDate": trade_date,
            "limitUpCount": _safe_int(row.get("limit_up_count")),
            "limitDownCount": _safe_int(row.get("limit_down_count")),
        })
        strength_history.append({
            "tradeDate": trade_date,
            "newHigh20Count": _safe_int(row.get("new_high_20_count")),
            "newHigh60Count": _safe_int(row.get("new_high_60_count")),
            "newHigh120Count": _safe_int(row.get("new_high_120_count")),
            "rps90Count": _safe_int(row.get("rps90_count")),
        })
        amount_history.append({
            "tradeDate": trade_date,
            "totalAmount": total_amount,
            "amountMa20": ma20,
            "amountRatio20": ratio,
        })

    return {
        "breadthHistory": breadth_history[-60:],
        "limitStatsHistory": limit_history[-60:],
        "strengthDiffusionHistory": strength_history[-120:],
        "amountHistory": amount_history[-60:],
    }


# ==================== Market Dashboard 聚合数据 ====================

@router.get("/dates", response_model=ApiResponse)
async def get_market_dates(limit: int = Query(120, ge=1, le=300)):
    """获取 Market 页可切换交易日期。"""
    date_sources = [
        ("market_strength_daily", "trade_date"),
        ("stock_daily_prices", "trade_date"),
        ("index_daily", "trade_date"),
    ]
    for table_name, date_column in date_sources:
        if not _table_exists(table_name):
            continue
        rows = fetch_all(
            f"""
            SELECT trade_date
            FROM (
                SELECT DISTINCT DATE({date_column}) AS trade_date
                FROM {table_name}
                ORDER BY DATE({date_column}) DESC
                LIMIT :limit
            )
            ORDER BY DATE(trade_date) DESC
            """,
            {"limit": limit},
        )
        dates = [str(row["trade_date"]) for row in rows if row.get("trade_date")]
        if dates:
            return ApiResponse(data={"dates": dates, "latest": dates[0]})
    return ApiResponse(data={"dates": [], "latest": None})


@router.get("/dashboard", response_model=ApiResponse)
async def get_market_dashboard(date: Optional[str] = Query("latest", description="交易日期 YYYY-MM-DD 或 latest")):
    """
    Market 市场温度页聚合接口。

    数据来自本地真实表：
    - market_strength_daily: 市场状态、广度、涨跌停、新高、RPS90、成交额
    - index_daily: 指数 K 线、均线、趋势摘要
    """
    try:
        trade_date = _resolve_market_date(date)
        market = _latest_market_row(trade_date)
        if not market:
            raise HTTPException(status_code=404, detail=f"日期 {trade_date} 暂无市场强度数据")

        actual_trade_date = _format_date(market.get("trade_date")) or trade_date
        previous_market = _previous_market_row(actual_trade_date)
        history_rows = _market_history(actual_trade_date, 140)
        histories = _build_histories(history_rows)
        hero = _hero(market, previous_market, actual_trade_date)
        risk_notes = hero["riskNotes"]
        indices = _market_indices(actual_trade_date)

        up_count = _safe_int(market.get("up_count"))
        down_count = _safe_int(market.get("down_count"))
        total = up_count + down_count
        breadth = {
            "upCount": up_count,
            "downCount": down_count,
            "upRatio": round(up_count / total * 100, 2) if total else 0,
            "limitUpCount": _safe_int(market.get("limit_up_count")),
            "limitDownCount": _safe_int(market.get("limit_down_count")),
            "newHigh20Count": _safe_int(market.get("new_high_20_count")),
            "newHigh60Count": _safe_int(market.get("new_high_60_count")),
            "newHigh120Count": _safe_int(market.get("new_high_120_count")),
            "rps90Count": _safe_int(market.get("rps90_count")),
            "totalAmount": _safe_float(market.get("total_amount")),
            "amountRatio20": _safe_float(market.get("amount_ratio_20"), 1.0),
        }

        return ApiResponse(
            data={
                "tradeDate": actual_trade_date,
                "trade_date": actual_trade_date,
                "market_state": hero["marketState"],
                "market_strength_score": hero["marketStrengthScore"],
                "market_index_code": market.get("index_code"),
                "updatedAt": hero["updatedAt"],
                "hero": hero,
                "indices": indices,
                "defaultIndexCode": market.get("index_code") or (indices[0]["indexCode"] if indices else "000001"),
                "breadth": breadth,
                "riskNotes": risk_notes,
                **histories,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取Market Dashboard失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/indices", response_model=ApiResponse)
async def get_market_indices(date: Optional[str] = Query("latest", description="交易日期 YYYY-MM-DD 或 latest")):
    """获取 Market 页指数摘要。"""
    try:
        trade_date = _resolve_index_date(date)
        indices = _market_indices(trade_date)
        return ApiResponse(
            data={
                "tradeDate": trade_date,
                "indices": indices,
                "count": len(indices),
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取Market指数摘要失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/indices/{index_code}/kline", response_model=ApiResponse)
async def get_market_index_kline(
    index_code: str,
    days: int = Query(250, ge=20, le=1000, description="返回最近N个交易日"),
    date: Optional[str] = Query("latest", description="截止交易日期 YYYY-MM-DD 或 latest"),
    period: str = Query("daily", description="周期: daily, 30min, 120min"),
):
    """获取 Market 指数 K 线，包含 MA20/50/120/250。"""
    period = str(getattr(period, "default", period)).strip().lower()
    if period not in {"daily", "30min", "120min"}:
        raise HTTPException(status_code=400, detail="period 必须是 daily、30min 或 120min")
    if period != "daily":
        if index_code not in CORE_MARKET_INDEX_CODES:
            raise HTTPException(status_code=400, detail="不支持的分钟指数代码")
        try:
            result = await asyncio.to_thread(
                get_minute_chart, "index", index_code, period, getattr(date, "default", date),
                max(30, min(int(getattr(days, "default", days)), 520)), 0 if index_code.startswith("399") else 1,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rows = result.pop("bars", [])
        closes = [_safe_float(row.get("close"), None) for row in rows]
        items = [{
            "tradeDate": row["trade_date"],
            **{key: row.get(key) for key in ("open", "high", "low", "close", "volume", "amount")},
            "pctChange": row.get("pct_chg"),
            **{f"ma{window}": _rolling_mean(closes, index, window)
               for window in (20, 50, 120, 250)},
        } for index, row in enumerate(rows)]
        return ApiResponse(data={
            **result,
            "indexCode": index_code,
            "indexName": INDEX_DISPLAY_NAMES.get(index_code, index_code),
            "tradeDate": items[-1]["tradeDate"] if items else None,
            "items": items,
            "count": len(items),
        })
    try:
        trade_date = _resolve_index_date(date)
        items = _load_index_items(index_code, trade_date, days)
        if not items:
            raise HTTPException(status_code=404, detail=f"指数 {index_code} 暂无K线数据")
        index_name = items[-1].get("index_name") or INDEX_DISPLAY_NAMES.get(index_code, index_code)
        return ApiResponse(
            data={
                "indexCode": index_code,
                "indexName": index_name,
                "tradeDate": items[-1].get("tradeDate"),
                "items": items,
                "count": len(items),
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取Market指数K线失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/limit-boards", response_model=ApiResponse)
async def get_limit_boards(
    date: Optional[str] = Query("latest", description="交易日期 YYYY-MM-DD 或 latest"),
    limit: int = Query(20, ge=1, le=100, description="代表个股数量"),
):
    """获取真实涨停、跌停和连板数据。"""
    try:
        trade_date = _resolve_limit_board_date(date)
        limit_up_rows = fetch_all(
            """
            SELECT trade_date, symbol, name, pct_chg, latest_price, amount,
                   seal_amount, first_limit_time, last_limit_time, break_count,
                   boards, industry
            FROM limit_board_trading
            WHERE DATE(trade_date) = DATE(:trade_date)
            ORDER BY COALESCE(boards, 1) DESC,
                     COALESCE(seal_amount, 0) DESC,
                     COALESCE(amount, 0) DESC
            """,
            {"trade_date": trade_date},
        ) if _table_exists("limit_board_trading") else []
        limit_down_rows = fetch_all(
            """
            SELECT trade_date, symbol, name, pct_chg, latest_price, amount,
                   seal_amount, last_limit_time, consecutive_down AS boards,
                   break_count, industry
            FROM limit_down_trading
            WHERE DATE(trade_date) = DATE(:trade_date)
            ORDER BY COALESCE(consecutive_down, 1) DESC,
                     COALESCE(seal_amount, 0) DESC,
                     COALESCE(amount, 0) DESC
            """,
            {"trade_date": trade_date},
        ) if _table_exists("limit_down_trading") else []
        ladder_rows = fetch_all(
            """
            SELECT boards, stock_count
            FROM consecutive_boards_stats
            WHERE DATE(trade_date) = DATE(:trade_date)
            ORDER BY boards DESC
            """,
            {"trade_date": trade_date},
        ) if _table_exists("consecutive_boards_stats") else []
        ladder = {
            str(_safe_int(row.get("boards"), 0)): _safe_int(row.get("stock_count"), 0)
            for row in ladder_rows
            if _safe_int(row.get("boards"), 0) > 0
        }
        if not ladder:
            for row in limit_up_rows:
                boards = max(1, _safe_int(row.get("boards"), 1))
                ladder[str(boards)] = ladder.get(str(boards), 0) + 1

        return ApiResponse(
            data={
                "tradeDate": trade_date,
                "limit_up": {
                    "total": len(limit_up_rows),
                    "by_sector": _sector_distribution(limit_up_rows),
                    "top_stocks": _limit_stock_rows(limit_up_rows, limit),
                    "rows": _limit_stock_rows(limit_up_rows, 200),
                },
                "limit_down": {
                    "total": len(limit_down_rows),
                    "by_sector": _sector_distribution(limit_down_rows),
                    "top_stocks": _limit_stock_rows(limit_down_rows, limit),
                    "rows": _limit_stock_rows(limit_down_rows, 200),
                },
                "ladder": ladder,
                "consecutive": [
                    {
                        "boards": _safe_int(row.get("boards"), 0),
                        "stock_count": _safe_int(row.get("stock_count"), 0),
                    }
                    for row in ladder_rows
                ],
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取涨跌停数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/news", response_model=ApiResponse)
async def get_market_news(
    limit: int = Query(20, ge=1, le=100),
    sector: Optional[str] = Query(None, description="板块过滤；没有关联数据时仅支持 all"),
):
    """获取真实新闻数据；没有股票/板块关联时不伪造板块归属。"""
    try:
        if not _table_exists("news_articles"):
            return ApiResponse(data={"items": [], "sectors": [], "count": 0, "hasSectorRelations": False})

        has_relations = _table_exists("news_stock_relation") and bool(
            fetch_one("SELECT 1 AS exists_flag FROM news_stock_relation LIMIT 1")
        )
        rows = fetch_all(
            """
            SELECT article_id, title, content, source, publish_time, url, sentiment_type
            FROM news_articles
            ORDER BY DATETIME(publish_time) DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        items = []
        for row in rows:
            content = str(row.get("content") or "")
            summary = content[:120] + ("..." if len(content) > 120 else "")
            items.append(
                {
                    "id": row.get("article_id"),
                    "sector": "全市场",
                    "title": row.get("title"),
                    "summary": summary,
                    "source": row.get("source"),
                    "time": _relative_news_time(row.get("publish_time")),
                    "publishTime": row.get("publish_time"),
                    "sentiment": row.get("sentiment_type") or "neutral",
                    "impact": "medium",
                    "url": row.get("url"),
                }
            )

        if sector and sector not in {"all", "全市场"} and has_relations:
            items = [item for item in items if item["sector"] == sector]

        return ApiResponse(
            data={
                "items": items,
                "sectors": ["全市场"],
                "count": len(items),
                "hasSectorRelations": has_relations,
            }
        )
    except Exception as e:
        logger.error(f"获取新闻数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 市场统计数据 ====================

@router.get("/stats/latest", response_model=ApiResponse)
async def get_latest_market_stats():
    """
    获取最新的市场统计数据（使用股票成交额汇总）
    """
    try:
        # 获取市场统计数据
        query = """
            SELECT
                trade_date,
                up_count,
                down_count,
                flat_count,
                limit_up_count,
                limit_down_count,
                avg_chg,
                total_volume,
                total_amount,
                north_money
            FROM market_daily_stats
            ORDER BY trade_date DESC
            LIMIT 1
        """

        result = fetch_one(query)

        if not result:
            raise HTTPException(status_code=404, detail="暂无市场统计数据")

        # 获取股票成交额汇总（更准确的全市场成交额）
        stock_amount_query = """
            SELECT
                COUNT(*) as stock_count,
                SUM(volume) as total_stock_volume,
                SUM(amount) as total_stock_amount
            FROM stock_daily_prices
            WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily_prices)
        """
        stock_data = fetch_one(stock_amount_query)

        # 将股票成交额添加到结果中
        if stock_data:
            result['stock_count'] = stock_data.get('stock_count', 0)
            result['stock_volume'] = stock_data.get('total_stock_volume', 0)
            result['stock_amount'] = stock_data.get('total_stock_amount', 0)

        return ApiResponse(
            code=200,
            message="success",
            data=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取最新市场统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/{trade_date}", response_model=ApiResponse)
async def get_market_stats(trade_date: str):
    """
    获取指定日期的市场统计数据（使用股票成交额汇总）

    Args:
        trade_date: 交易日期 (YYYY-MM-DD)
    """
    try:
        query = """
            SELECT
                trade_date,
                up_count,
                down_count,
                flat_count,
                limit_up_count,
                limit_down_count,
                avg_chg,
                total_volume,
                total_amount,
                north_money
            FROM market_daily_stats
            WHERE trade_date = :trade_date
        """

        result = fetch_one(query, {"trade_date": trade_date})

        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"日期 {trade_date} 的市场统计数据不存在"
            )

        # 获取股票成交额汇总（更准确的全市场成交额）
        stock_amount_query = """
            SELECT
                COUNT(*) as stock_count,
                SUM(volume) as total_stock_volume,
                SUM(amount) as total_stock_amount
            FROM stock_daily_prices
            WHERE trade_date = :trade_date
        """
        stock_data = fetch_one(stock_amount_query, {"trade_date": trade_date})

        # 将股票成交额添加到结果中
        if stock_data:
            result['stock_count'] = stock_data.get('stock_count', 0)
            result['stock_volume'] = stock_data.get('total_stock_volume', 0)
            result['stock_amount'] = stock_data.get('total_stock_amount', 0)

        return ApiResponse(
            code=200,
            message="success",
            data=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取市场统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 板块RPS矩阵 ====================

@router.get("/rps_matrix", response_model=ApiResponse)
async def get_rps_matrix(
    period: str = Query('rps_20', description="RPS周期: rps_5, rps_10, rps_20, rps_50, rps_120, rps_250"),
    limit_days: int = Query(20, ge=1, le=60, description="最近N天的数据"),
    top_n: int = Query(50, ge=10, le=100, description="Top N板块")
):
    """
    获取板块RPS矩阵数据（用于热力图展示）

    Args:
        period: RPS周期（默认rps_20）
        limit_days: 最近几天数据（默认20天）
        top_n: 返回Top N板块（默认50）
    """
    try:
        # 验证period参数
        valid_periods = ['rps_5', 'rps_10', 'rps_20', 'rps_50', 'rps_120', 'rps_250']
        if period not in valid_periods:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid period. Must be one of: {', '.join(valid_periods)}"
            )

        # v2 is the canonical net-value RPS track.  Once its table exists we
        # intentionally do not fall back to the legacy mixed-source table.
        rps_table = (
            "quant_feature_sector_rps_v2"
            if _table_exists("quant_feature_sector_rps_v2")
            else "quant_feature_sector_rps"
        )

        # 获取最近N天的交易日期
        dates_query = f"""
            SELECT DISTINCT trade_date
            FROM {rps_table}
            ORDER BY trade_date DESC
            LIMIT :limit_days
        """
        dates_result = fetch_all(dates_query, {"limit_days": limit_days})

        if not dates_result:
            raise HTTPException(status_code=404, detail="暂无RPS数据")

        # 日期列表（升序）
        dates = [row['trade_date'] for row in reversed(dates_result)]

        # 获取Top N板块（按最新日期的RPS排序）
        latest_date = dates[-1]
        top_sectors_query = f"""
            SELECT
                sector_name,
                {period}
            FROM {rps_table}
            WHERE trade_date = :latest_date
                AND {period} IS NOT NULL
            ORDER BY {period} DESC
            LIMIT :top_n
        """
        top_sectors_result = fetch_all(top_sectors_query, {
            "latest_date": latest_date,
            "top_n": top_n
        })

        if not top_sectors_result:
            raise HTTPException(status_code=404, detail="暂无板块RPS数据")

        sectors = [row['sector_name'] for row in top_sectors_result]

        # 构建热力图数据
        # 格式: [[date_index, sector_index, rps_value], ...]
        heatmap_data = []

        for date_idx, date in enumerate(dates):
            for sector_idx, sector in enumerate(sectors):
                # 查询该日期该板块的RPS值
                rps_query = """
                    SELECT {period}
                    FROM {rps_table}
                    WHERE trade_date = :trade_date
                        AND sector_name = :sector_name
                """.format(period=period, rps_table=rps_table)

                result = fetch_one(rps_query, {
                    "trade_date": date,
                    "sector_name": sector
                })

                rps_value = result[period] if result and result[period] is not None else 50

                # RPS值保留2位小数
                heatmap_data.append([date_idx, sector_idx, round(rps_value, 2)])

        return ApiResponse(
            code=200,
            message="success",
            data={
                "dates": dates,
                "sectors": sectors,
                "data": heatmap_data
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取RPS矩阵失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
