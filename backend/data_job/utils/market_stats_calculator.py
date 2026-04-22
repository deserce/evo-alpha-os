"""
市场统计计算脚本
计算每日市场统计数据：上涨/下跌家数、涨停/跌停家数、平均涨幅、成交量等
"""
import pandas as pd
import sys
from pathlib import Path
from datetime import datetime, timedelta
import logging
from sqlalchemy import text
from tqdm import tqdm

# 添加项目路径
current_dir = Path(__file__).parent
backend_dir = current_dir.parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.database import get_local_engine

logger = logging.getLogger(__name__)


def _execute_query(engine, query, params):
    """执行查询并返回结果"""
    with engine.begin() as conn:
        return conn.execute(text(query), params).fetchone()


def calculate_market_stats(trade_date=None):
    """
    计算指定日期的市场统计数据

    Args:
        trade_date: 交易日期 YYYY-MM-DD（默认：最新日期）

    Returns:
        dict: 统计结果
    """
    engine = get_local_engine()

    if not trade_date:
        # 获取最新交易日期
        with engine.begin() as conn:
            query = "SELECT MAX(trade_date) as max_date FROM stock_daily_prices"
            result = conn.execute(text(query)).fetchone()
        trade_date = result[0] if result and result[0] else datetime.now().strftime("%Y-%m-%d")
        logger.info(f"使用最新日期: {trade_date}")

    logger.info(f"📊 计算 {trade_date} 的市场统计数据...")

    try:
        # 1. 统计上涨/下跌/平家长数
        up_down_query = """
            SELECT
                SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) as up_count,
                SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) as down_count,
                SUM(CASE WHEN pct_chg = 0 THEN 1 ELSE 0 END) as flat_count
            FROM stock_daily_prices
            WHERE trade_date = :trade_date
        """
        result = _execute_query(engine, up_down_query, {"trade_date": trade_date})
        up_count, down_count, flat_count = result

        # 2. 统计涨停/跌停家数（简化判断：涨幅>9.9%或涨幅<-9.9%）
        limit_query = """
            SELECT
                SUM(CASE WHEN pct_chg >= 9.9 THEN 1 ELSE 0 END) as limit_up_count,
                SUM(CASE WHEN pct_chg <= -9.9 THEN 1 ELSE 0 END) as limit_down_count
            FROM stock_daily_prices
            WHERE trade_date = :trade_date
        """
        result = _execute_query(engine, limit_query, {"trade_date": trade_date})
        limit_up_count, limit_down_count = result

        # 3. 计算平均涨幅
        avg_query = """
            SELECT AVG(pct_chg) as avg_chg
            FROM stock_daily_prices
            WHERE trade_date = :trade_date
        """
        result = _execute_query(engine, avg_query, {"trade_date": trade_date})
        avg_chg = result[0] if result and result[0] else 0.0

        # 4. 计算总成交量和成交额
        volume_query = """
            SELECT
                SUM(volume) as total_volume,
                SUM(amount) as total_amount
            FROM stock_daily_prices
            WHERE trade_date = :trade_date
        """
        result = _execute_query(engine, volume_query, {"trade_date": trade_date})
        total_volume, total_amount = result

        # 5. 获取北向资金（如果有）
        north_money = get_north_money(engine, trade_date)

        # 6. 保存或更新到 market_daily_stats 表
        save_to_database(
            engine=engine,
            trade_date=trade_date,
            up_count=up_count or 0,
            down_count=down_count or 0,
            flat_count=flat_count or 0,
            limit_up_count=limit_up_count or 0,
            limit_down_count=limit_down_count or 0,
            avg_chg=avg_chg or 0,
            total_volume=total_volume or 0,
            total_amount=total_amount or 0,
            north_money=north_money
        )

        logger.info("✅ 市场统计计算完成！")
        logger.info(f"  📈 上涨: {up_count}, 📉 下跌: {down_count}, ➡️ 平盘: {flat_count}")
        logger.info(f"  🔥 涨停: {limit_up_count}, 📉 跌停: {limit_down_count}")
        logger.info(f"  📊 平均涨幅: {avg_chg:.2f}%")
        logger.info(f"  💰 成交额: {total_amount/100000000:.1f}亿元")

        return {
            "trade_date": trade_date,
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "avg_chg": avg_chg,
            "total_volume": total_volume,
            "total_amount": total_amount,
            "north_money": north_money
        }

    except Exception as e:
        logger.error(f"❌ 计算失败: {e}")
        raise


def get_north_money(engine, trade_date):
    """获取北向资金数据"""
    try:
        # 从 stock_northbound_holdings 表获取
        # 简化：取指定日期或之前最近的数据
        with engine.begin() as conn:
            query = """
                SELECT SUM(hold_amount) as total
                FROM stock_northbound_holdings
                WHERE hold_date <= :trade_date
            """
            result = conn.execute(text(query), {"trade_date": trade_date}).fetchone()

        if result and result[0]:
            return result[0]
        else:
            return 0

    except Exception as e:
        logger.debug(f"  ⚠️  无法获取北向资金: {e}")
        return 0


def save_to_database(engine, trade_date, up_count, down_count, flat_count,
                     limit_up_count, limit_down_count, avg_chg,
                     total_volume, total_amount, north_money):
    """保存统计数据到数据库"""
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT OR REPLACE INTO market_daily_stats
                (trade_date, up_count, down_count, flat_count, limit_up_count,
                 limit_down_count, avg_chg, total_volume, total_amount, north_money, updated_at)
                VALUES (:trade_date, :up_count, :down_count, :flat_count, :limit_up_count,
                 :limit_down_count, :avg_chg, :total_volume, :total_amount, :north_money, CURRENT_TIMESTAMP)
            """), {"trade_date": trade_date, "up_count": up_count, "down_count": down_count,
                   "flat_count": flat_count, "limit_up_count": limit_up_count,
                   "limit_down_count": limit_down_count, "avg_chg": avg_chg,
                   "total_volume": total_volume, "total_amount": total_amount,
                   "north_money": north_money})

        logger.debug("  ✅ 统计数据已保存")

    except Exception as e:
        logger.error(f"❌ 保存失败: {e}")
        raise


def calculate_historical_stats(start_date, end_date=None):
    """
    计算历史日期范围的市场统计数据

    Args:
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD（默认：今天）

    Returns:
        list: 计算结果列表
    """
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    logger.info(f"📊 计算历史统计数据: {start_date} 至 {end_date}")

    engine = get_local_engine()

    # 获取日期范围内的所有交易日
    query = """
        SELECT DISTINCT trade_date
        FROM stock_daily_prices
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
    """
    df_dates = pd.read_sql(query, engine, params=(start_date, end_date))

    results = []

    for _, row in tqdm(df_dates.iterrows(), total=len(df_dates), desc="  批量计算统计", unit="天"):
        trade_date = row['trade_date']
        try:
            stats = calculate_market_stats(trade_date)
            results.append(stats)
        except Exception as e:
            logger.debug(f"⚠️  {trade_date} 统计失败: {e}")
            continue

    logger.info(f"✅ 历史统计计算完成: {len(results)} 个交易日")

    return results


if __name__ == "__main__":
    # 配置日志
    logger.remove()
    logger.add(sys.stdout, level="INFO")

    logger.info("🚀 市场统计计算器启动")

    # 计算最近一天的数据
    result = calculate_market_stats()

    logger.info("\n📊 统计结果:")
    logger.info(f"  上涨家数: {result['up_count']}")
    logger.info(f"  下跌家数: {result['down_count']}")
    logger.info(f"  涨停家数: {result['limit_up_count']}")
    logger.info(f"  平均涨幅: {result['avg_chg']:.2f}%")
