"""
个股相关API路由
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from datetime import date, datetime, timedelta
import logging
logger = logging.getLogger(__name__)

from app.api.v1.database import execute_query, fetch_one, fetch_all, paginate
from app.api.v1.schemas import StockInfo, StockKline, StockValuation, ApiResponse

router = APIRouter(prefix="/stock", tags=["个股"])


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
        query = """
            SELECT
                symbol, name, industry, market_cap, pe, pb
            FROM stock_info
            WHERE symbol LIKE :keyword
               OR name LIKE :keyword
            ORDER BY market_cap DESC
            LIMIT 20
        """

        keyword_pattern = f"%{keyword}%"
        results = fetch_all(query, {"keyword": keyword_pattern})

        return ApiResponse(
            code=200,
            message="success",
            data={"stocks": results, "count": len(results)}
        )

    except Exception as e:
        logger.error(f"搜索股票失败: {e}")
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
    limit: int = Query(8, ge=1, le=32, description="返回季度数（默认8个季度）")
):
    """
    获取股票财务摘要数据

    Args:
        symbol: 股票代码
        limit: 返回季度数
    """
    try:
        query = """
            SELECT
                symbol, end_date, revenue, net_profit, eps,
                roe, roa, gross_margin, net_margin
            FROM stock_finance_summary
            WHERE symbol = :symbol
            ORDER BY end_date DESC
            LIMIT :limit
        """

        params = {"symbol": symbol, "limit": limit}
        results = fetch_all(query, params)

        # 按日期升序排列
        results = list(reversed(results))

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": symbol,
                "finance": results,
                "count": len(results)
            }
        )

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
