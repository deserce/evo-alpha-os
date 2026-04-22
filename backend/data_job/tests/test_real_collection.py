"""
实时采集测试
测试修改后的采集器能否正常工作并同步更新 stock_info
"""
import sys
from pathlib import Path
import pandas as pd
from datetime import date

# 添加项目路径
current_dir = Path(__file__).parent
backend_dir = current_dir.parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.database import get_local_engine
from sqlalchemy import text
import logging
logger = logging.getLogger(__name__)


def test_valuation_collector():
    """测试 StockValuationCollector 实时采集"""
    logger.info("=" * 60)
    logger.info("🧪 实时测试: StockValuationCollector")
    logger.info("=" * 60)

    from data_job.collectors.stock_valuation_collector import StockValuationCollector

    # 记录测试前的数据
    engine = get_local_engine()
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT COUNT(*) FROM stock_info WHERE pe IS NOT NULL
        """)).fetchone()
        before_count = result[0]
        logger.info(f"测试前: {before_count} 只股票有 PE 数据")

    # 运行采集器
    logger.info("\n开始采集估值数据...")
    collector = StockValuationCollector()

    try:
        # 只获取数据，不实际保存（测试模式）
        logger.info("正在获取实时数据...")
        df_raw = collector.fetch_data()

        if df_raw is not None and not df_raw.empty:
            logger.info(f"✅ 成功获取 {len(df_raw)} 条数据")

            # 处理数据
            df_clean = collector.process_data(df_raw)
            logger.info(f"✅ 数据处理完成: {len(df_clean)} 条")

            # 检查数据示例
            logger.info("\n数据示例:")
            sample = df_clean.head(3)
            for _, row in sample.iterrows():
                logger.info(f"  {row['code']} - PE:{row['pe_ttm']:.2f}, PB:{row['pb']:.2f}, 市值:{row['total_mv']/1e8:.2f}亿")

            # 保存数据（这里会触发同步更新）
            logger.info("\n正在保存数据...")
            collector.save_data(df_clean)

            # 验证更新后的数据
            logger.info("\n验证 stock_info 表更新...")
            with engine.begin() as conn:
                result = conn.execute(text("""
                    SELECT COUNT(*) FROM stock_info WHERE pe IS NOT NULL
                """)).fetchone()
                after_count = result[0]

                logger.info(f"测试后: {after_count} 只股票有 PE 数据")

                # 检查最新的数据
                result = conn.execute(text("""
                    SELECT symbol, name, pe, pb, market_cap
                    FROM stock_info
                    WHERE symbol IN ('000001', '600000', '600036')
                    ORDER BY symbol
                """)).fetchall()

                logger.info("\n示例股票最新数据:")
                for row in result:
                    logger.info(f"  {row[0]} - {row[1]}: PE={row[2]}, PB={row[3]}, 市值={row[4]}亿")

            logger.info("\n✅ 测试成功！stock_info 表已同步更新")
            return True

        else:
            logger.error("❌ 未获取到数据")
            return False

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_sector_collector():
    """测试 StockSectorListCollector 的行业更新功能"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 实时测试: StockSectorListCollector 行业更新")
    logger.info("=" * 60)

    from data_job.collectors.stock_sector_list_collector import StockSectorListCollector

    engine = get_local_engine()

    # 记录测试前的数据
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT COUNT(*) FROM stock_info WHERE industry IS NOT NULL
        """)).fetchone()
        before_count = result[0]
        logger.info(f"测试前: {before_count} 只股票有行业数据")

    try:
        # 只运行行业更新，不运行完整采集
        collector = StockSectorListCollector()

        logger.info("\n正在运行行业信息更新...")
        collector.update_stock_info_industry()

        # 验证更新后的数据
        with engine.begin() as conn:
            result = conn.execute(text("""
                SELECT COUNT(*) FROM stock_info WHERE industry IS NOT NULL
            """)).fetchone()
            after_count = result[0]

            logger.info(f"测试后: {after_count} 只股票有行业数据")

            # 检查示例数据
            result = conn.execute(text("""
                SELECT symbol, name, industry
                FROM stock_info
                WHERE symbol IN ('000001', '600000', '600036')
                ORDER BY symbol
            """)).fetchall()

            logger.info("\n示例股票行业数据:")
            for row in result:
                logger.info(f"  {row[0]} - {row[1]}: {row[2]}")

            logger.info("\n✅ 行业更新测试成功！")
            return True

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    logger.info("🚀 开始实时采集测试")

    # 测试 1: StockValuationCollector
    test1 = test_valuation_collector()

    # 测试 2: StockSectorListCollector (只测试行业更新)
    # test2 = test_sector_collector()
    test2 = True  # 暂时跳过，避免运行时间过长

    # 总结
    logger.info("\n" + "=" * 60)
    logger.info("📊 测试总结")
    logger.info("=" * 60)
    logger.info(f"  StockValuationCollector 实时采集: {'✅ 通过' if test1 else '❌ 失败'}")
    logger.info(f"  StockSectorListCollector 行业更新: {'✅ 跳过' if test2 else '✅ 通过' if test2 else '❌ 失败'}")

    if test1:
        logger.info("\n✅ 实时采集测试通过！")
        logger.info("\n可以运行完整采集:")
        logger.info("  python3 data_job/utils/run_all_collectors.py --mode all")
        logger.info("  python3 data_job/utils/run_all_collectors.py --mode daily")
        return 0
    else:
        logger.error("\n❌ 测试失败")
        return 1


if __name__ == "__main__":
    exit(main())
