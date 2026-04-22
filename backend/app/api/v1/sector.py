"""
板块相关API路由
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from datetime import datetime
import logging
logger = logging.getLogger(__name__)

from app.api.v1.database import fetch_one, fetch_all, paginate
from app.api.v1.schemas import ApiResponse

router = APIRouter(prefix="/sector", tags=["板块"])


# ==================== 板块列表 ====================

@router.get("/list", response_model=ApiResponse)
async def get_sector_list(
    sector_type: Optional[str] = Query(None, description="板块类型: Industry/Concept/Region"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页大小")
):
    """
    获取板块列表

    板块类型：
    - Industry: 行业板块
    - Concept: 概念板块
    - Region: 地域板块
    """
    try:
        conditions = []
        params = {}

        if sector_type:
            conditions.append("sector_type = :sector_type")
            params["sector_type"] = sector_type

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # 从板块映射表获取所有不重复的板块
        query = f"""
            SELECT DISTINCT
                sector_name,
                sector_type
            FROM stock_sector_map
            {where_clause}
            ORDER BY sector_type, sector_name
        """

        result = paginate(query, page, page_size, params)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "sectors": result["data"],
                "total": result["total"],
                "page": result["page"],
                "page_size": result["page_size"]
            }
        )

    except Exception as e:
        logger.error(f"获取板块列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info/{sector_name}", response_model=ApiResponse)
async def get_sector_info(
    sector_name: str,
    sector_type: Optional[str] = Query(None, description="板块类型: Industry/Concept/Region")
):
    """
    获取单个板块的详细信息

    Args:
        sector_name: 板块名称
        sector_type: 板块类型
    """
    try:
        # 获取成分股数量
        conditions = ["sector_name = :sector_name"]
        params = {"sector_name": sector_name}

        if sector_type:
            conditions.append("sector_type = :sector_type")
            params["sector_type"] = sector_type

        where_clause = " AND ".join(conditions)

        count_query = f"""
            SELECT COUNT(DISTINCT symbol) as stock_count
            FROM stock_sector_map
            WHERE {where_clause}
        """

        count_result = fetch_one(count_query, params)

        result = {
            "sector_name": sector_name,
            "sector_type": sector_type,
            "stock_count": count_result["stock_count"] if count_result else 0
        }

        return ApiResponse(
            code=200,
            message="success",
            data=result
        )

    except Exception as e:
        logger.error(f"获取板块信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 板块成分股 ====================

@router.get("/constituents/{sector_name}", response_model=ApiResponse)
async def get_sector_constituents(
    sector_name: str,
    sector_type: Optional[str] = Query(None, description="板块类型: Industry/Concept/Region"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页大小")
):
    """
    获取板块成分股列表

    Args:
        sector_name: 板块名称
        sector_type: 板块类型
    """
    try:
        conditions = ["m.sector_name = :sector_name"]
        params = {"sector_name": sector_name}

        if sector_type:
            conditions.append("m.sector_type = :sector_type")
            params["sector_type"] = sector_type

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT
                s.symbol,
                s.name,
                s.industry,
                s.market_cap,
                s.pe,
                s.pb
            FROM stock_sector_map m
            JOIN stock_info s ON m.symbol = s.symbol
            WHERE {where_clause}
            ORDER BY s.market_cap DESC
        """

        result = paginate(query, page, page_size, params)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "sector_name": sector_name,
                "constituents": result["data"],
                "total": result["total"],
                "page": result["page"],
                "page_size": result["page_size"]
            }
        )

    except Exception as e:
        logger.error(f"获取成分股失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 板块K线 ====================

@router.get("/kline/{sector_name}", response_model=ApiResponse)
async def get_sector_kline(
    sector_name: str,
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数")
):
    """
    获取板块K线数据

    Args:
        sector_name: 板块名称
        start_date: 开始日期
        end_date: 结束日期
        limit: 返回条数（默认最近100条）
    """
    try:
        # 默认返回最近100天
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        conditions = ["sector_name = :sector_name"]
        params = {"sector_name": sector_name}

        if start_date:
            conditions.append("trade_date >= :start_date")
            params["start_date"] = start_date

        if end_date:
            conditions.append("trade_date <= :end_date")
            params["end_date"] = end_date

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT
                sector_name,
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                amount,
                pct_chg
            FROM sector_daily_prices
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
                "sector_name": sector_name,
                "kline": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取板块K线失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 板块涨跌排行 ====================

@router.get("/ranking", response_model=ApiResponse)
async def get_sector_ranking(
    trade_date: Optional[str] = Query(None, description="交易日期 (YYYY-MM-DD，默认最新)"),
    top_n: int = Query(10, ge=1, le=50, description="返回Top N")
):
    """
    获取板块涨跌排行榜

    Args:
        trade_date: 交易日期（默认最新交易日）
        top_n: 返回前N名
    """
    try:
        # 如果没有指定日期，获取最新交易日
        if not trade_date:
            date_query = """
                SELECT MAX(trade_date) as latest_date
                FROM sector_daily_prices
            """
            date_result = fetch_one(date_query)
            if date_result and date_result["latest_date"]:
                trade_date = date_result["latest_date"]
            else:
                raise HTTPException(status_code=404, detail="没有可用数据")

        # 获取涨幅榜
        gainers_query = """
            SELECT
                sector_name,
                trade_date,
                close,
                pct_chg,
                amount
            FROM sector_daily_prices
            WHERE trade_date = :trade_date
            ORDER BY pct_chg DESC
            LIMIT :top_n
        """

        gainers = fetch_all(gainers_query, {
            "trade_date": trade_date,
            "top_n": top_n
        })

        # 获取跌幅榜
        losers_query = """
            SELECT
                sector_name,
                trade_date,
                close,
                pct_chg,
                amount
            FROM sector_daily_prices
            WHERE trade_date = :trade_date
            ORDER BY pct_chg ASC
            LIMIT :top_n
        """

        losers = fetch_all(losers_query, {
            "trade_date": trade_date,
            "top_n": top_n
        })

        return ApiResponse(
            code=200,
            message="success",
            data={
                "trade_date": trade_date,
                "top_gainers": gainers,
                "top_losers": losers
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取板块排行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 板块综合概览 ====================

@router.get("/overview/{sector_name}", response_model=ApiResponse)
async def get_sector_overview(sector_name: str):
    """
    获取板块综合概览

    Args:
        sector_name: 板块名称
    """
    try:
        # 获取成分股数量
        count_query = """
            SELECT COUNT(DISTINCT symbol) as stock_count
            FROM stock_sector_map
            WHERE sector_name = :sector_name
        """
        count_result = fetch_one(count_query, {"sector_name": sector_name})

        sector_info = {
            "sector_name": sector_name,
            "stock_count": count_result["stock_count"] if count_result else 0
        }

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
            FROM sector_daily_prices
            WHERE sector_name = :sector_name
            ORDER BY trade_date DESC
            LIMIT 5
        """
        kline_data = fetch_all(kline_query, {"sector_name": sector_name})
        kline_data = list(reversed(kline_data))

        # 获取Top 10成分股（按市值）
        top_stocks_query = """
            SELECT
                s.symbol,
                s.name,
                s.market_cap,
                s.pe,
                s.pb
            FROM stock_sector_map m
            JOIN stock_info s ON m.symbol = s.symbol
            WHERE m.sector_name = :sector_name
            ORDER BY s.market_cap DESC
            LIMIT 10
        """
        top_stocks = fetch_all(top_stocks_query, {"sector_name": sector_name})

        return ApiResponse(
            code=200,
            message="success",
            data={
                "info": sector_info,
                "latest_kline": kline_data,
                "top_stocks": top_stocks
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取板块概览失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
