"""
使用优化版采集器运行数据采集
"""
import sys
from pathlib import Path
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from datetime import datetime, timedelta
import logging
import pandas as pd

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """主函数"""
    from data_job.collectors.optimized_kline_collector import OptimizedKlineCollector
    from app.core.database import get_engine
    from sqlalchemy import text

    logger.info("=" * 80)
    logger.info("🚀 开始使用优化版采集器采集数据")
    logger.info("=" * 80)

    # 获取数据库引擎
    engine = get_engine()

    # 获取需要采集的股票列表
    with engine.connect() as conn:
        # 获取核心股票池的股票
        query = text("""
            SELECT DISTINCT symbol
            FROM quant_stock_pool
            WHERE pool_name = 'core_pool'
            AND is_active = 1
            ORDER BY symbol
            LIMIT 100
        """)
        result = conn.execute(query)
        symbols = [row[0] for row in result]

    logger.info(f"📊 需要采集的股票数量: {len(symbols)}")

    # 计算日期范围（最近5天）
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')

    logger.info(f"📅 采集日期范围: {start_date} ~ {end_date}")

    # 创建优化版采集器（使用最保守的配置）
    logger.info("\n⚙️  采集器配置:")
    logger.info("   模式: 极保守（单线程）")
    logger.info("   延迟: 2-4秒")
    logger.info("   批次: 30只/批")
    logger.info("   批次休息: 60秒")
    logger.info("   重试次数: 5次")

    collector = OptimizedKlineCollector(
        min_delay=2.0,      # 2秒最小延迟
        max_delay=4.0,      # 4秒最大延迟
        batch_size=30,      # 30只一批
        batch_rest=60,      # 批次间休息60秒
        max_retries=5,      # 5次重试
    )

    # 开始采集
    logger.info("\n" + "=" * 80)
    logger.info("🎯 开始采集...")
    logger.info("=" * 80 + "\n")

    results = collector.collect_batch(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        progress_callback=lambda sym, success: logger.info(f"进度: {sym} - {'✅' if success else '❌'}")
    )

    # 统计结果
    success_count = sum(1 for v in results.values() if v is not None)
    fail_count = len(results) - success_count

    logger.info("\n" + "=" * 80)
    logger.info("📊 采集完成统计")
    logger.info("=" * 80)
    logger.info(f"总股票数: {len(symbols)}")
    logger.info(f"成功: {success_count} ({success_count/len(symbols)*100:.1f}%)")
    logger.info(f"失败: {fail_count} ({fail_count/len(symbols)*100:.1f}%)")
    logger.info("=" * 80)

    # 保存成功的数据到数据库
    if success_count > 0:
        logger.info("\n💾 开始保存数据到数据库...")

        from app.core.database import get_engine
        from sqlalchemy import text

        engine = get_engine()

        save_count = 0
        with engine.connect() as conn:
            for symbol, df in results.items():
                if df is not None and not df.empty:
                    try:
                        # 转换日期格式
                        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')

                        # 保存到数据库
                        df.to_sql(
                            'stock_daily_prices',
                            con=conn,
                            if_exists='append',
                            index=False
                        )
                        save_count += 1
                    except Exception as e:
                        logger.error(f"保存 {symbol} 失败: {e}")

            conn.commit()

        logger.info(f"✅ 成功保存 {save_count} 只股票的数据到数据库")

    logger.info("\n✅ 所有任务完成！")

if __name__ == "__main__":
    main()
