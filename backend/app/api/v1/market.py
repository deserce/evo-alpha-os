"""
市场统计相关API路由
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import datetime, timedelta
import logging
logger = logging.getLogger(__name__)

from app.api.v1.database import fetch_one, fetch_all, paginate
from app.api.v1.schemas import ApiResponse

router = APIRouter(prefix="/market", tags=["市场统计"])


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

        # 获取最近N天的交易日期
        dates_query = """
            SELECT DISTINCT trade_date
            FROM quant_feature_sector_rps
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
            FROM quant_feature_sector_rps
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
                    FROM quant_feature_sector_rps
                    WHERE trade_date = :trade_date
                        AND sector_name = :sector_name
                """.format(period=period)

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
