"""
批量计算历史市场统计数据
从 stock_daily_prices 表计算所有历史交易日的市场统计
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


def get_all_trade_dates():
    """获取数据库中所有交易日期"""
    engine = get_local_engine()

    with engine.begin() as conn:
        query = """
            SELECT DISTINCT trade_date
            FROM stock_daily_prices
            ORDER BY trade_date
        """
        df = pd.read_sql(query, engine)

    return df['trade_date'].tolist()


def calculate_stats_for_date(trade_date):
    """计算指定日期的市场统计"""
    from data_job.utils.market_stats_calculator import calculate_market_stats

    try:
        result = calculate_market_stats(trade_date=trade_date)
        return result
    except Exception as e:
        logger.error(f"❌ {trade_date} 统计失败: {e}")
        return None


def calculate_historical_stats(start_date=None, end_date=None):
    """
    批量计算历史市场统计

    Args:
        start_date: 开始日期 YYYY-MM-DD（默认：最早日期）
        end_date: 结束日期 YYYY-MM-DD（默认：今天）
    """
    engine = get_local_engine()

    # 获取所有交易日期
    all_dates = get_all_trade_dates()

    if not all_dates:
        logger.error("❌ 没有找到交易日期")
        return

    # 过滤日期范围
    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
    if end_date:
        all_dates = [d for d in all_dates if d <= end_date]

    logger.info("=" * 60)
    logger.info("📊 批量计算历史市场统计")
    logger.info("=" * 60)
    logger.info(f"日期范围: {all_dates[0]} 至 {all_dates[-1]}")
    logger.info(f"总交易日数: {len(all_dates)}")

    # 检查哪些日期已经计算过
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT DISTINCT trade_date FROM market_daily_stats ORDER BY trade_date
        """)).fetchall()
        calculated_dates = set([row[0] for row in result])

    # 找出需要计算的日期
    pending_dates = [d for d in all_dates if d not in calculated_dates]

    if not pending_dates:
        logger.info("✅ 所有日期的市场统计已计算")
        return

    logger.info(f"需要计算的日期: {len(pending_dates)} 个")

    # 批量计算
    success_count = 0
    failed_count = 0

    for trade_date in tqdm(pending_dates, desc="  计算历史统计", unit="天"):
        try:
            result = calculate_stats_for_date(trade_date)

            if result:
                success_count += 1
            else:
                failed_count += 1

        except Exception as e:
            logger.debug(f"  ❌ {trade_date}: {e}")
            failed_count += 1

    # 总结
    logger.info(f"\n✅ 成功: {success_count}, ❌ 失败: {failed_count}")

    # 验证最终结果
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT COUNT(*) FROM market_daily_stats
        """)).fetchone()
        logger.info(f"📊 market_daily_stats 表总记录数: {result[0]}")


def verify_historical_data():
    """验证历史数据完整性"""
    engine = get_local_engine()

    logger.info("\n" + "=" * 60)
    logger.info("🔍 验证历史数据完整性")
    logger.info("=" * 60)

    # 1. 检查 stock_daily_prices 表
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT
                MIN(trade_date) as min_date,
                MAX(trade_date) as max_date,
                COUNT(DISTINCT trade_date) as total_days
            FROM stock_daily_prices
        """)).fetchone()

        logger.info(f"\nstock_daily_prices 表:")
        logger.info(f"  日期范围: {result[0]} 至 {result[1]}")
        logger.info(f"  交易日数量: {result[2]}")

    # 2. 检查 stock_valuation_daily 表
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT
                MIN(trade_date) as min_date,
                MAX(trade_date) as max_date,
                COUNT(DISTINCT trade_date) as total_days
            FROM stock_valuation_daily
        """)).fetchone()

        logger.info(f"\nstock_valuation_daily 表:")
        logger.info(f"  日期范围: {result[0]} 至 {result[1] if result[0] else '无数据'}")
        logger.info(f"  交易日数量: {result[2] if result[0] else 0}")

    # 3. 检查 market_daily_stats 表
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT
                MIN(trade_date) as min_date,
                MAX(trade_date) as max_date,
                COUNT(*) as total_days
            FROM market_daily_stats
        """)).fetchone()

        logger.info(f"\nmarket_daily_stats 表:")
        logger.info(f"  日期范围: {result[0]} 至 {result[1] if result[0] else '无数据'}")
        logger.info(f"  记录数量: {result[2] if result[0] else 0}")

    # 4. 检查数据覆盖率
    logger.info(f"\n数据覆盖率:")
    with engine.begin() as conn:
        # stock_daily_prices 的交易日期
        prices_days = conn.execute(text("""
            SELECT COUNT(DISTINCT trade_date) FROM stock_daily_prices
        """)).fetchone()[0]

        # market_daily_stats 的记录数
        stats_days = conn.execute(text("""
            SELECT COUNT(*) FROM market_daily_stats
        """)).fetchone()[0]

        coverage = (stats_days / prices_days * 100) if prices_days > 0 else 0
        logger.info(f"  市场统计覆盖: {stats_days}/{prices_days} ({coverage:.1f}%)")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="批量计算历史市场统计")
    parser.add_argument('--start', type=str, help='开始日期 YYYY-MM-DD')
    parser.add_argument('--end', type=str, help='结束日期 YYYY-MM-DD')
    parser.add_argument('--verify', action='store_true', help='仅验证数据完整性')

    args = parser.parse_args()

    if args.verify:
        verify_historical_data()
    else:
        calculate_historical_stats(
            start_date=args.start,
            end_date=args.end
        )


if __name__ == "__main__":
    main()
