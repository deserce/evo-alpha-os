"""
日报生成相关API路由
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import datetime, timedelta
import logging
logger = logging.getLogger(__name__)

from app.api.v1.database import fetch_one, fetch_all
from app.api.v1.schemas import ApiResponse

router = APIRouter(prefix="/report", tags=["日报"])


# ==================== 生成日报 ====================

@router.get("/daily/{trade_date}", response_model=ApiResponse)
async def generate_daily_report(trade_date: str):
    """
    生成指定日期的市场日报

    Args:
        trade_date: 交易日期 (YYYY-MM-DD)
    """
    try:
        # 1. 获取市场统计数据
        market_query = """
            SELECT
                up_count,
                down_count,
                flat_count,
                limit_up_count,
                limit_down_count,
                avg_chg,
                total_amount,
                north_money
            FROM market_daily_stats
            WHERE trade_date = :trade_date
        """

        market_data = fetch_one(market_query, {"trade_date": trade_date})

        if not market_data:
            raise HTTPException(
                status_code=404,
                detail=f"日期 {trade_date} 的市场数据不存在"
            )

        # 2. 获取指数表现
        index_query = """
            SELECT
                index_code,
                index_name,
                close,
                pct_chg,
                volume
            FROM index_daily
            WHERE trade_date = :trade_date
            ORDER BY index_code
        """

        index_data = fetch_all(index_query, {"trade_date": trade_date})

        # 3. 获取涨幅榜前10
        top_gainers_query = """
            SELECT
                symbol,
                name,
                close,
                pct_chg,
                volume
            FROM stock_daily_prices s
            LEFT JOIN stock_info i ON s.symbol = i.symbol
            WHERE s.trade_date = :trade_date
            ORDER BY s.pct_chg DESC
            LIMIT 10
        """

        top_gainers = fetch_all(top_gainers_query, {"trade_date": trade_date})

        # 4. 获取跌幅榜前10
        top_losers_query = """
            SELECT
                symbol,
                name,
                close,
                pct_chg,
                volume
            FROM stock_daily_prices s
            LEFT JOIN stock_info i ON s.symbol = i.symbol
            WHERE s.trade_date = :trade_date
            ORDER BY s.pct_chg ASC
            LIMIT 10
        """

        top_losers = fetch_all(top_losers_query, {"trade_date": trade_date})

        # 5. 获取成交额前10
        top_volume_query = """
            SELECT
                symbol,
                name,
                close,
                pct_chg,
                amount
            FROM stock_daily_prices s
            LEFT JOIN stock_info i ON s.symbol = i.symbol
            WHERE s.trade_date = :trade_date
            ORDER BY s.amount DESC
            LIMIT 10
        """

        top_volume = fetch_all(top_volume_query, {"trade_date": trade_date})

        # 6. 获取板块表现
        sector_query = """
            SELECT
                sector_code,
                close,
                pct_chg
            FROM sector_daily_prices
            WHERE trade_date = :trade_date
            ORDER BY pct_chg DESC
            LIMIT 10
        """

        top_sectors = fetch_all(sector_query, {"trade_date": trade_date})

        # 7. 获取当日Alpha机会
        alpha_query = """
            SELECT
                opportunity_type,
                COUNT(*) as count,
                AVG(confidence_score) as avg_confidence
            FROM alpha_opportunities
            WHERE trade_date = :trade_date
              AND status = 'active'
            GROUP BY opportunity_type
        """

        alpha_summary = fetch_all(alpha_query, {"trade_date": trade_date})

        # 组装日报
        report = {
            "trade_date": trade_date,
            "market_overview": {
                "up_count": market_data["up_count"],
                "down_count": market_data["down_count"],
                "flat_count": market_data["flat_count"],
                "limit_up_count": market_data["limit_up_count"],
                "limit_down_count": market_data["limit_down_count"],
                "avg_chg": market_data["avg_chg"],
                "total_amount": market_data["total_amount"],
                "north_money": market_data["north_money"]
            },
            "major_indices": index_data,
            "top_gainers": top_gainers,
            "top_losers": top_losers,
            "top_volume": top_volume,
            "top_sectors": top_sectors,
            "alpha_opportunities": alpha_summary
        }

        return ApiResponse(
            code=200,
            message="success",
            data=report
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成日报失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 最新日报 ====================

@router.get("/daily/latest", response_model=ApiResponse)
async def get_latest_daily_report():
    """
    获取最新日期的日报
    """
    try:
        # 获取最新交易日
        date_query = """
            SELECT MAX(trade_date) as latest_date
            FROM market_daily_stats
        """

        date_result = fetch_one(date_query)

        if not date_result or not date_result["latest_date"]:
            raise HTTPException(status_code=404, detail="暂无可用数据")

        latest_date = date_result["latest_date"]

        # 调用日报生成
        return await generate_daily_report(latest_date)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取最新日报失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 简化版日报（适合移动端） ====================

@router.get("/daily/simple/{trade_date}", response_model=ApiResponse)
async def get_simple_daily_report(trade_date: str):
    """
    获取简化版日报（只包含核心信息）

    Args:
        trade_date: 交易日期
    """
    try:
        # 市场概览
        market_query = """
            SELECT
                up_count,
                down_count,
                avg_chg,
                north_money
            FROM market_daily_stats
            WHERE trade_date = :trade_date
        """

        market_data = fetch_one(market_query, {"trade_date": trade_date})

        if not market_data:
            raise HTTPException(
                status_code=404,
                detail=f"日期 {trade_date} 的数据不存在"
            )

        # 指数表现
        index_query = """
            SELECT
                index_name,
                pct_chg
            FROM index_daily
            WHERE trade_date = :trade_date
            ORDER BY index_code
        """

        index_data = fetch_all(index_query, {"trade_date": trade_date})

        # Top5 涨幅榜
        gainers_query = """
            SELECT
                s.symbol,
                i.name,
                s.pct_chg
            FROM stock_daily_prices s
            LEFT JOIN stock_info i ON s.symbol = i.symbol
            WHERE s.trade_date = :trade_date
            ORDER BY s.pct_chg DESC
            LIMIT 5
        """

        gainers = fetch_all(gainers_query, {"trade_date": trade_date})

        # Top5 跌幅榜
        losers_query = """
            SELECT
                s.symbol,
                i.name,
                s.pct_chg
            FROM stock_daily_prices s
            LEFT JOIN stock_info i ON s.symbol = i.symbol
            WHERE s.trade_date = :trade_date
            ORDER BY s.pct_chg ASC
            LIMIT 5
        """

        losers = fetch_all(losers_query, {"trade_date": trade_date})

        report = {
            "trade_date": trade_date,
            "market": market_data,
            "indices": index_data,
            "top_gainers": gainers,
            "top_losers": losers
        }

        return ApiResponse(
            code=200,
            message="success",
            data=report
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取简化日报失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 周报摘要 ====================

@router.get("/weekly", response_model=ApiResponse)
async def generate_weekly_report(
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD，默认最新)")
):
    """
    生成周报摘要

    Args:
        end_date: 结束日期（默认最新交易日）
    """
    try:
        # 如果没有指定日期，获取最新交易日
        if not end_date:
            date_query = """
                SELECT MAX(trade_date) as latest_date
                FROM market_daily_stats
            """
            date_result = fetch_one(date_query)
            if date_result and date_result["latest_date"]:
                end_date = date_result["latest_date"]
            else:
                raise HTTPException(status_code=404, detail="暂无可用数据")

        # 获取最近5个交易日
        week_query = """
            SELECT
                trade_date,
                up_count,
                down_count,
                avg_chg,
                total_amount,
                north_money
            FROM market_daily_stats
            WHERE trade_date <= :end_date
            ORDER BY trade_date DESC
            LIMIT 5
        """

        week_data = fetch_all(week_query, {"end_date": end_date})

        if not week_data:
            raise HTTPException(status_code=404, detail="暂无足够数据")

        # 按日期升序排列
        week_data = list(reversed(week_data))

        # 计算周统计
        total_up = sum(d["up_count"] or 0 for d in week_data)
        total_down = sum(d["down_count"] or 0 for d in week_data)
        avg_chg = sum(d["avg_chg"] or 0 for d in week_data) / len(week_data)
        total_amount = sum(d["total_amount"] or 0 for d in week_data)
        total_north = sum(d["north_money"] or 0 for d in week_data)

        # 获取本周涨幅榜
        gainers_query = """
            SELECT
                s.symbol,
                i.name,
                AVG(s.pct_chg) as avg_pct_chg,
                SUM(s.pct_chg) as total_pct_chg
            FROM stock_daily_prices s
            LEFT JOIN stock_info i ON s.symbol = i.symbol
            WHERE s.trade_date IN (
                SELECT trade_date
                FROM market_daily_stats
                WHERE trade_date <= :end_date
                ORDER BY trade_date DESC
                LIMIT 5
            )
            GROUP BY s.symbol, i.name
            ORDER BY total_pct_chg DESC
            LIMIT 10
        """

        top_gainers = fetch_all(gainers_query, {"end_date": end_date})

        report = {
            "period": f"最近{len(week_data)}个交易日",
            "start_date": week_data[0]["trade_date"],
            "end_date": week_data[-1]["trade_date"],
            "summary": {
                "total_up": total_up,
                "total_down": total_down,
                "avg_chg": round(avg_chg, 2),
                "total_amount": total_amount,
                "total_north_money": total_north
            },
            "daily_data": week_data,
            "top_gainers": top_gainers
        }

        return ApiResponse(
            code=200,
            message="success",
            data=report
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成周报失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 月度统计 ====================

@router.get("/monthly", response_model=ApiResponse)
async def get_monthly_stats(
    year: int = Query(..., ge=2020, le=2030, description="年份"),
    month: int = Query(..., ge=1, le=12, description="月份")
):
    """
    获取月度统计数据

    Args:
        year: 年份
        month: 月份
    """
    try:
        # 构建日期范围
        start_date = f"{year}-{month:02d}-01"
        if month == 12:
            end_date = f"{year + 1}-01-01"
        else:
            end_date = f"{year}-{month + 1:02d}-01"

        query = """
            SELECT
                trade_date,
                up_count,
                down_count,
                avg_chg,
                total_amount,
                north_money
            FROM market_daily_stats
            WHERE trade_date >= :start_date
              AND trade_date < :end_date
            ORDER BY trade_date
        """

        monthly_data = fetch_all(query, {
            "start_date": start_date,
            "end_date": end_date
        })

        if not monthly_data:
            raise HTTPException(
                status_code=404,
                detail=f"{year}年{month}月暂无数据"
            )

        # 计算月度统计
        total_up = sum(d["up_count"] or 0 for d in monthly_data)
        total_down = sum(d["down_count"] or 0 for d in monthly_data)
        avg_chg = sum(d["avg_chg"] or 0 for d in monthly_data) / len(monthly_data)
        total_amount = sum(d["total_amount"] or 0 for d in monthly_data)
        total_north = sum(d["north_money"] or 0 for d in monthly_data)

        up_days = sum(1 for d in monthly_data if (d["avg_chg"] or 0) > 0)
        down_days = sum(1 for d in monthly_data if (d["avg_chg"] or 0) < 0)

        report = {
            "year": year,
            "month": month,
            "trading_days": len(monthly_data),
            "summary": {
                "total_up": total_up,
                "total_down": total_down,
                "avg_chg": round(avg_chg, 2),
                "total_amount": total_amount,
                "total_north_money": total_north,
                "up_days": up_days,
                "down_days": down_days
            },
            "daily_data": monthly_data
        }

        return ApiResponse(
            code=200,
            message="success",
            data=report
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取月度统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
