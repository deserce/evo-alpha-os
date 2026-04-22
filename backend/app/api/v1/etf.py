"""
ETF相关API路由
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import datetime
import logging
logger = logging.getLogger(__name__)

from app.api.v1.database import fetch_one, fetch_all, paginate
from app.api.v1.schemas import ApiResponse

router = APIRouter(prefix="/etf", tags=["ETF"])


# ==================== ETF列表 ====================

@router.get("/list", response_model=ApiResponse)
async def get_etf_list(
    fund_type: Optional[str] = Query(None, description="基金类型"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页大小")
):
    """
    获取ETF列表

    基金类型示例：
    - 股票型ETF
    - 债券型ETF
    - 商品型ETF
    - 跨境ETF
    """
    try:
        conditions = []
        params = {}

        if fund_type:
            conditions.append("fund_type = :fund_type")
            params["fund_type"] = fund_type

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                symbol,
                name,
                fund_type
            FROM etf_info
            {where_clause}
            ORDER BY symbol
        """

        result = paginate(query, page, page_size, params)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "etfs": result["data"],
                "total": result["total"],
                "page": result["page"],
                "page_size": result["page_size"]
            }
        )

    except Exception as e:
        logger.error(f"获取ETF列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info/{symbol}", response_model=ApiResponse)
async def get_etf_info(symbol: str):
    """
    获取单个ETF的详细信息

    Args:
        symbol: ETF代码
    """
    try:
        query = """
            SELECT
                symbol,
                name,
                fund_type
            FROM etf_info
            WHERE symbol = :symbol
        """

        result = fetch_one(query, {"symbol": symbol})

        if not result:
            raise HTTPException(status_code=404, detail=f"ETF {symbol} 不存在")

        return ApiResponse(
            code=200,
            message="success",
            data=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取ETF信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search", response_model=ApiResponse)
async def search_etfs(
    keyword: str = Query(..., min_length=1, description="搜索关键词（代码或名称）")
):
    """
    搜索ETF（支持代码和名称模糊搜索）

    Args:
        keyword: 搜索关键词，如"沪深300"或"510300"
    """
    try:
        query = """
            SELECT
                symbol,
                name,
                fund_type
            FROM etf_info
            WHERE symbol LIKE :keyword
               OR name LIKE :keyword
            ORDER BY symbol
            LIMIT 20
        """

        keyword_pattern = f"%{keyword}%"
        results = fetch_all(query, {"keyword": keyword_pattern})

        return ApiResponse(
            code=200,
            message="success",
            data={"etfs": results, "count": len(results)}
        )

    except Exception as e:
        logger.error(f"搜索ETF失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== ETF K线 ====================

@router.get("/kline/{symbol}", response_model=ApiResponse)
async def get_etf_kline(
    symbol: str,
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数")
):
    """
    获取ETF K线数据

    Args:
        symbol: ETF代码
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

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT
                symbol,
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                amount,
                pct_chg
            FROM etf_daily_prices
            WHERE {where_clause}
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
        logger.error(f"获取ETF K线失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== ETF涨跌排行 ====================

@router.get("/ranking", response_model=ApiResponse)
async def get_etf_ranking(
    trade_date: Optional[str] = Query(None, description="交易日期 (YYYY-MM-DD，默认最新)"),
    top_n: int = Query(10, ge=1, le=50, description="返回Top N"),
    fund_type: Optional[str] = Query(None, description="基金类型筛选")
):
    """
    获取ETF涨跌排行榜

    Args:
        trade_date: 交易日期（默认最新交易日）
        top_n: 返回前N名
        fund_type: 基金类型筛选
    """
    try:
        # 如果没有指定日期，获取最新交易日
        if not trade_date:
            date_query = """
                SELECT MAX(trade_date) as latest_date
                FROM etf_daily_prices
            """
            date_result = fetch_one(date_query)
            if date_result and date_result["latest_date"]:
                trade_date = date_result["latest_date"]
            else:
                raise HTTPException(status_code=404, detail="没有可用数据")

        conditions = ["e.trade_date = :trade_date"]
        params = {"trade_date": trade_date}

        if fund_type:
            conditions.append("i.fund_type = :fund_type")
            params["fund_type"] = fund_type

        where_clause = " AND ".join(conditions)

        # 获取涨幅榜
        gainers_query = f"""
            SELECT
                e.symbol,
                i.name,
                e.trade_date,
                e.close,
                e.pct_chg,
                e.amount
            FROM etf_daily_prices e
            LEFT JOIN etf_info i ON e.symbol = i.symbol
            WHERE {where_clause}
            ORDER BY e.pct_chg DESC
            LIMIT {top_n}
        """

        gainers = fetch_all(gainers_query, params)

        # 获取跌幅榜
        losers_query = f"""
            SELECT
                e.symbol,
                i.name,
                e.trade_date,
                e.close,
                e.pct_chg,
                e.amount
            FROM etf_daily_prices e
            LEFT JOIN etf_info i ON e.symbol = i.symbol
            WHERE {where_clause}
            ORDER BY e.pct_chg ASC
            LIMIT {top_n}
        """

        losers = fetch_all(losers_query, params)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "trade_date": trade_date,
                "fund_type": fund_type,
                "top_gainers": gainers,
                "top_losers": losers
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取ETF排行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== ETF综合概览 ====================

@router.get("/overview/{symbol}", response_model=ApiResponse)
async def get_etf_overview(symbol: str):
    """
    获取ETF综合概览

    Args:
        symbol: ETF代码
    """
    try:
        # 获取基本信息
        info_query = """
            SELECT symbol, name, fund_type FROM etf_info WHERE symbol = :symbol
        """
        etf_info = fetch_one(info_query, {"symbol": symbol})

        if not etf_info:
            raise HTTPException(status_code=404, detail=f"ETF {symbol} 不存在")

        # 获取最新K线（最近5天）
        kline_query = """
            SELECT
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                pct_chg
            FROM etf_daily_prices
            WHERE symbol = :symbol
            ORDER BY trade_date DESC
            LIMIT 5
        """
        kline_data = fetch_all(kline_query, {"symbol": symbol})
        kline_data = list(reversed(kline_data))

        return ApiResponse(
            code=200,
            message="success",
            data={
                "info": etf_info,
                "latest_kline": kline_data
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取ETF概览失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
