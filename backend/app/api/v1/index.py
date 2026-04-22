"""
指数相关API路由
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from datetime import datetime
import logging
logger = logging.getLogger(__name__)

from app.api.v1.database import fetch_one, fetch_all, paginate
from app.api.v1.schemas import ApiResponse

router = APIRouter(prefix="/index", tags=["指数"])


# ==================== 指数列表 ====================

@router.get("/list", response_model=ApiResponse)
async def get_index_list():
    """
    获取所有可用的指数列表
    """
    try:
        query = """
            SELECT DISTINCT
                index_code,
                index_name
            FROM index_daily
            ORDER BY index_code
        """

        results = fetch_all(query)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "indices": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取指数列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info/{index_code}", response_model=ApiResponse)
async def get_index_info(index_code: str):
    """
    获取单个指数的基本信息

    Args:
        index_code: 指数代码（如：000001）
    """
    try:
        # 获取最新数据以获取指数名称
        query = """
            SELECT
                index_code,
                index_name,
                trade_date,
                close,
                pct_chg,
                volume
            FROM index_daily
            WHERE index_code = :index_code
            ORDER BY trade_date DESC
            LIMIT 1
        """

        result = fetch_one(query, {"index_code": index_code})

        if not result:
            raise HTTPException(status_code=404, detail=f"指数 {index_code} 不存在")

        # 获取数据范围
        range_query = """
            SELECT
                MIN(trade_date) as start_date,
                MAX(trade_date) as end_date,
                COUNT(*) as data_count
            FROM index_daily
            WHERE index_code = :index_code
        """

        range_result = fetch_one(range_query, {"index_code": index_code})

        result["data_start_date"] = range_result["start_date"]
        result["data_end_date"] = range_result["end_date"]
        result["data_count"] = range_result["data_count"]

        return ApiResponse(
            code=200,
            message="success",
            data=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取指数信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 指数K线 ====================

@router.get("/kline/{index_code}", response_model=ApiResponse)
async def get_index_kline(
    index_code: str,
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数")
):
    """
    获取指数K线数据

    Args:
        index_code: 指数代码
        start_date: 开始日期
        end_date: 结束日期
        limit: 返回条数（默认最近100条）
    """
    try:
        # 默认返回最近100天
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        conditions = ["index_code = :index_code"]
        params = {"index_code": index_code}

        if start_date:
            conditions.append("trade_date >= :start_date")
            params["start_date"] = start_date

        if end_date:
            conditions.append("trade_date <= :end_date")
            params["end_date"] = end_date

        where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                index_code,
                index_name,
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                amount,
                pct_chg
            FROM index_daily
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
                "index_code": index_code,
                "kline": results,
                "count": len(results)
            }
        )

    except Exception as e:
        logger.error(f"获取指数K线失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 指数对比 ====================

@router.get("/compare", response_model=ApiResponse)
async def compare_indices(
    indices: str = Query(..., description="指数代码列表，逗号分隔，如：000001,399001,399006"),
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(60, ge=1, le=365, description="返回条数")
):
    """
    对比多个指数的表现

    Args:
        indices: 指数代码列表，逗号分隔
        start_date: 开始日期
        end_date: 结束日期
        limit: 返回条数
    """
    try:
        # 解析指数列表
        index_list = [idx.strip() for idx in indices.split(',')]

        if len(index_list) > 10:
            raise HTTPException(
                status_code=400,
                detail="最多支持对比10个指数"
            )

        # 默认返回最近60天
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        conditions = []
        params = {}

        # 为每个指数创建条件
        index_conditions = " OR ".join([f"index_code = :idx_{i}" for i in range(len(index_list))])
        conditions.append(f"({index_conditions})")
        for i, idx in enumerate(index_list):
            params[f"idx_{i}"] = idx

        if start_date:
            conditions.append("trade_date >= :start_date")
            params["start_date"] = start_date

        if end_date:
            conditions.append("trade_date <= :end_date")
            params["end_date"] = end_date

        where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                index_code,
                index_name,
                trade_date,
                close,
                pct_chg,
                volume
            FROM index_daily
            {where_clause}
            ORDER BY trade_date DESC
            LIMIT {limit}
        """

        results = fetch_all(query, params)

        # 按日期分组
        from collections import defaultdict
        grouped = defaultdict(list)
        for item in results:
            date = item["trade_date"]
            grouped[date].append(item)

        # 转换为对比格式
        comparison = []
        for date in sorted(grouped.keys(), reverse=True):
            date_data = {"trade_date": date}
            for item in grouped[date]:
                date_data[item["index_code"]] = {
                    "close": item["close"],
                    "pct_chg": item["pct_chg"]
                }
            comparison.append(date_data)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "indices": index_list,
                "comparison": comparison,
                "count": len(comparison)
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"指数对比失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 指数最新行情 ====================

@router.get("/latest", response_model=ApiResponse)
async def get_latest_indices():
    """
    获取所有指数的最新行情数据
    """
    try:
        query = """
            SELECT
                index_code,
                index_name,
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                pct_chg,
                change
            FROM index_daily
            WHERE trade_date = (
                SELECT MAX(trade_date) FROM index_daily
            )
            ORDER BY index_code
        """

        results = fetch_all(query)

        return ApiResponse(
            code=200,
            message="success",
            data={
                "indices": results,
                "count": len(results),
                "trade_date": results[0]["trade_date"] if results else None
            }
        )

    except Exception as e:
        logger.error(f"获取指数最新行情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 指数综合概览 ====================

@router.get("/overview/{index_code}", response_model=ApiResponse)
async def get_index_overview(index_code: str):
    """
    获取指数综合概览

    Args:
        index_code: 指数代码
    """
    try:
        # 获取基本信息
        info_query = """
            SELECT
                index_code,
                index_name
            FROM index_daily
            WHERE index_code = :index_code
            LIMIT 1
        """
        index_info = fetch_one(info_query, {"index_code": index_code})

        if not index_info:
            raise HTTPException(status_code=404, detail=f"指数 {index_code} 不存在")

        # 获取最新行情
        latest_query = """
            SELECT
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                pct_chg
            FROM index_daily
            WHERE index_code = :index_code
            ORDER BY trade_date DESC
            LIMIT 1
        """
        latest_data = fetch_one(latest_query, {"index_code": index_code})

        # 获取最近5天K线
        kline_query = """
            SELECT
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                pct_chg
            FROM index_daily
            WHERE index_code = :index_code
            ORDER BY trade_date DESC
            LIMIT 5
        """
        kline_data = fetch_all(kline_query, {"index_code": index_code})
        kline_data = list(reversed(kline_data))

        # 计算简单统计
        stats_query = """
            SELECT
                COUNT(*) as trading_days,
                MAX(close) as period_high,
                MIN(close) as period_low,
                AVG(pct_chg) as avg_pct_chg
            FROM index_daily
            WHERE index_code = :index_code
              AND trade_date >= date('now', '-30 days')
        """
        stats = fetch_one(stats_query, {"index_code": index_code})

        return ApiResponse(
            code=200,
            message="success",
            data={
                "info": index_info,
                "latest": latest_data,
                "latest_kline": kline_data,
                "stats_30d": stats
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取指数概览失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 指数历史行情（按日期） ====================

@router.get("/daily/{trade_date}", response_model=ApiResponse)
async def get_indices_by_date(trade_date: str):
    """
    获取指定日期的所有指数行情数据

    Args:
        trade_date: 交易日期 (YYYY-MM-DD)
    """
    try:
        query = """
            SELECT
                index_code,
                index_name,
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                amount,
                pct_chg,
                change
            FROM index_daily
            WHERE trade_date = :trade_date
            ORDER BY index_code
        """

        results = fetch_all(query, {"trade_date": trade_date})

        if not results:
            raise HTTPException(
                status_code=404,
                detail=f"日期 {trade_date} 的指数数据不存在"
            )

        return ApiResponse(
            code=200,
            message="success",
            data={
                "indices": results,
                "count": len(results),
                "trade_date": trade_date
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取指定日期指数行情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
