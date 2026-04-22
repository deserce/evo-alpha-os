"""
Alpha机会相关API路由
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import datetime
import logging
logger = logging.getLogger(__name__)

from app.api.v1.database import fetch_one, fetch_all, paginate
from app.api.v1.schemas import ApiResponse

router = APIRouter(prefix="/alpha", tags=["Alpha机会"])


# ==================== Alpha机会列表 ====================

@router.get("/opportunities", response_model=ApiResponse)
async def get_alpha_opportunities(
    trade_date: Optional[str] = Query(None, description="交易日期 (YYYY-MM-DD)"),
    opportunity_type: Optional[str] = Query(None, description="机会类型"),
    status: str = Query("active", description="状态: active/closed/all"),
    min_confidence: Optional[float] = Query(None, ge=0, le=100, description="最小置信度"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页大小")
):
    """
    获取Alpha机会列表

    机会类型示例：
    - RPS突破
    - 口袋支点
    - 机构持仓
    - 等
    """
    try:
        conditions = []
        params = {}

        if trade_date:
            conditions.append("trade_date = :trade_date")
            params["trade_date"] = trade_date

        if opportunity_type:
            conditions.append("opportunity_type = :opportunity_type")
            params["opportunity_type"] = opportunity_type

        if status != "all":
            conditions.append("status = :status")
            params["status"] = status

        if min_confidence is not None:
            conditions.append("confidence_score >= :min_confidence")
            params["min_confidence"] = min_confidence

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                opportunity_id,
                trade_date,
                symbol,
                opportunity_type,
                confidence_score,
                reason,
                status,
                created_at
            FROM alpha_opportunities
            {where_clause}
            ORDER BY trade_date DESC, confidence_score DESC
        """

        result = paginate(query, page, page_size, params)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "opportunities": result["data"],
                "total": result["total"],
                "page": result["page"],
                "page_size": result["page_size"]
            }
        )

    except Exception as e:
        logger.error(f"获取Alpha机会列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/opportunity/{opportunity_id}", response_model=ApiResponse)
async def get_alpha_opportunity(opportunity_id: str):
    """
    获取单个Alpha机会的详细信息

    Args:
        opportunity_id: 机会ID
    """
    try:
        query = """
            SELECT
                opportunity_id,
                trade_date,
                symbol,
                opportunity_type,
                confidence_score,
                reason,
                status,
                created_at
            FROM alpha_opportunities
            WHERE opportunity_id = :opportunity_id
        """

        result = fetch_one(query, {"opportunity_id": opportunity_id})

        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"机会 {opportunity_id} 不存在"
            )

        # 获取股票基本信息
        stock_query = """
            SELECT
                symbol,
                name,
                industry,
                market_cap,
                pe,
                pb
            FROM stock_info
            WHERE symbol = :symbol
        """

        stock_info = fetch_one(stock_query, {"symbol": result["symbol"]})

        result["stock_info"] = stock_info

        return ApiResponse(
            code=200,
            message="success",
            data=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取Alpha机会详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 按股票查询机会 ====================

@router.get("/opportunities/by-stock/{symbol}", response_model=ApiResponse)
async def get_opportunities_by_stock(
    symbol: str,
    status: str = Query("active", description="状态: active/closed/all"),
    limit: int = Query(20, ge=1, le=100, description="返回条数")
):
    """
    获取指定股票的Alpha机会

    Args:
        symbol: 股票代码
        status: 状态
        limit: 返回条数
    """
    try:
        conditions = ["symbol = :symbol"]
        params = {"symbol": symbol}

        if status != "all":
            conditions.append("status = :status")
            params["status"] = status

        where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                opportunity_id,
                trade_date,
                symbol,
                opportunity_type,
                confidence_score,
                reason,
                status,
                created_at
            FROM alpha_opportunities
            {where_clause}
            ORDER BY trade_date DESC
            LIMIT {limit}
        """

        results = fetch_all(query, params)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": symbol,
                "opportunities": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取股票机会失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 机会类型统计 ====================

@router.get("/stats/by-type", response_model=ApiResponse)
async def get_opportunity_stats_by_type(
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)")
):
    """
    按类型统计Alpha机会

    Args:
        start_date: 开始日期
        end_date: 结束日期
    """
    try:
        conditions = []
        params = {}

        if start_date:
            conditions.append("trade_date >= :start_date")
            params["start_date"] = start_date

        if end_date:
            conditions.append("trade_date <= :end_date")
            params["end_date"] = end_date

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                opportunity_type,
                status,
                COUNT(*) as count,
                AVG(confidence_score) as avg_confidence
            FROM alpha_opportunities
            {where_clause}
            GROUP BY opportunity_type, status
            ORDER BY opportunity_type, status
        """

        results = fetch_all(query, params)

        # 按类型分组
        from collections import defaultdict
        grouped = defaultdict(lambda: {"active": 0, "closed": 0, "avg_confidence": 0})

        for item in results:
            opp_type = item["opportunity_type"]
            status = item["status"]
            grouped[opp_type][status] = item["count"]
            grouped[opp_type]["avg_confidence"] = item["avg_confidence"]

        return ApiResponse(
            code=200,
            message="success",
            data={
                "statistics": dict(grouped),
                "total_types": len(grouped)
            }
        )

    except Exception as e:
        logger.error(f"获取机会类型统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 最新机会 ====================

@router.get("/opportunities/latest", response_model=ApiResponse)
async def get_latest_opportunities(
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    min_confidence: Optional[float] = Query(None, ge=0, le=100, description="最小置信度")
):
    """
    获取最新的Alpha机会

    Args:
        limit: 返回条数
        min_confidence: 最小置信度筛选
    """
    try:
        conditions = ["status = 'active'"]
        params = {}

        if min_confidence is not None:
            conditions.append("confidence_score >= :min_confidence")
            params["min_confidence"] = min_confidence

        where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                opportunity_id,
                trade_date,
                symbol,
                opportunity_type,
                confidence_score,
                reason,
                status,
                created_at
            FROM alpha_opportunities
            {where_clause}
            ORDER BY trade_date DESC, confidence_score DESC
            LIMIT {limit}
        """

        results = fetch_all(query, params)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "opportunities": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取最新机会失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 高置信度机会 ====================

@router.get("/opportunities/high-confidence", response_model=ApiResponse)
async def get_high_confidence_opportunities(
    min_confidence: float = Query(80, ge=0, le=100, description="最小置信度"),
    limit: int = Query(20, ge=1, le=100, description="返回条数")
):
    """
    获取高置信度Alpha机会

    Args:
        min_confidence: 最小置信度（默认80）
        limit: 返回条数
    """
    try:
        query = f"""
            SELECT
                opportunity_id,
                trade_date,
                symbol,
                opportunity_type,
                confidence_score,
                reason,
                status,
                created_at
            FROM alpha_opportunities
            WHERE status = 'active'
              AND confidence_score >= :min_confidence
            ORDER BY confidence_score DESC, trade_date DESC
            LIMIT {limit}
        """

        results = fetch_all(query, {"min_confidence": min_confidence})

        return ApiResponse(
            code=200,
            message="success",
            data={
                "min_confidence": min_confidence,
                "opportunities": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取高置信度机会失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 机会跟踪 ====================

@router.get("/tracking", response_model=ApiResponse)
async def get_opportunity_tracking(
    symbol: str = Query(..., description="股票代码")
):
    """
    获取股票的机会跟踪记录

    Args:
        symbol: 股票代码
    """
    try:
        # 从机会跟踪表获取记录
        query = """
            SELECT
                tracking_id,
                symbol,
                trade_date,
                tracking_type,
                current_price,
                target_price,
                stop_loss,
                status,
                notes,
                created_at
            FROM opportunity_tracking
            WHERE symbol = :symbol
            ORDER BY trade_date DESC
            LIMIT 10
        """

        results = fetch_all(query, {"symbol": symbol})

        return ApiResponse(
            code=200,
            message="success",
            data={
                "symbol": symbol,
                "tracking": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取机会跟踪失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 每日Alpha机会摘要 ====================

@router.get("/daily-summary", response_model=ApiResponse)
async def get_daily_alpha_summary(
    trade_date: Optional[str] = Query(None, description="交易日期 (YYYY-MM-DD，默认最新)")
):
    """
    获取指定日期的Alpha机会摘要

    Args:
        trade_date: 交易日期
    """
    try:
        # 如果没有指定日期，获取最新日期
        if not trade_date:
            date_query = """
                SELECT MAX(trade_date) as latest_date
                FROM alpha_opportunities
            """
            date_result = fetch_one(date_query)
            if date_result and date_result["latest_date"]:
                trade_date = date_result["latest_date"]
            else:
                raise HTTPException(status_code=404, detail="暂无Alpha机会数据")

        # 统计各类机会数量
        stats_query = """
            SELECT
                opportunity_type,
                COUNT(*) as count,
                AVG(confidence_score) as avg_confidence,
                COUNT(DISTINCT symbol) as unique_stocks
            FROM alpha_opportunities
            WHERE trade_date = :trade_date
              AND status = 'active'
            GROUP BY opportunity_type
            ORDER BY count DESC
        """

        stats = fetch_all(stats_query, {"trade_date": trade_date})

        # 获取高置信度机会
        top_query = """
            SELECT
                opportunity_id,
                symbol,
                opportunity_type,
                confidence_score,
                reason
            FROM alpha_opportunities
            WHERE trade_date = :trade_date
              AND status = 'active'
            ORDER BY confidence_score DESC
            LIMIT 10
        """

        top_opportunities = fetch_all(top_query, {"trade_date": trade_date})

        # 总计
        total_query = """
            SELECT
                COUNT(*) as total_opportunities,
                COUNT(DISTINCT symbol) as total_stocks,
                AVG(confidence_score) as avg_confidence
            FROM alpha_opportunities
            WHERE trade_date = :trade_date
              AND status = 'active'
        """

        total = fetch_one(total_query, {"trade_date": trade_date})

        return ApiResponse(
            code=200,
            message="success",
            data={
                "trade_date": trade_date,
                "total_stats": total,
                "by_type": stats,
                "top_opportunities": top_opportunities
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取Alpha摘要失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
