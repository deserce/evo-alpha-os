"""
测试修改后的采集器
验证 stock_info 表的同步更新功能
"""
import sys
from pathlib import Path

# 添加项目路径
current_dir = Path(__file__).parent
backend_dir = current_dir.parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.database import get_local_engine
from sqlalchemy import text
import logging
logger = logging.getLogger(__name__)


def verify_stock_info_before():
    """验证测试前 stock_info 表的数据"""
    engine = get_local_engine()

    logger.info("=" * 60)
    logger.info("📊 测试前 stock_info 表数据验证")
    logger.info("=" * 60)

    with engine.begin() as conn:
        # 检查各字段的数据量
        for field in ['industry', 'pe', 'pb', 'market_cap']:
            result = conn.execute(text(
                f"SELECT COUNT(*) FROM stock_info WHERE {field} IS NOT NULL"
            )).fetchone()
            logger.info(f"  {field}: {result[0]} 只股票有数据")

        # 获取一些示例数据
        result = conn.execute(text("""
            SELECT symbol, name, industry, pe, pb, market_cap
            FROM stock_info
            WHERE symbol IN ('000001', '000002', '600000')
            ORDER BY symbol
        """)).fetchall()

        logger.info("\n示例股票数据（测试前）:")
        for row in result:
            logger.info(f"  {row[0]} - {row[1]}: industry={row[2]}, pe={row[3]}, pb={row[4]}, market_cap={row[5]}")

    return engine


def test_stock_valuation_collector():
    """测试 StockValuationCollector 的同步更新功能"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 测试 1: StockValuationCollector 同步更新")
    logger.info("=" * 60)

    try:
        from data_job.collectors.stock_valuation_collector import StockValuationCollector

        collector = StockValuationCollector()

        # 检查 save_data 方法是否包含同步更新逻辑
        import inspect
        source = inspect.getsource(collector.save_data)

        if '同步更新 stock_info' in source:
            logger.info("✅ save_data() 包含同步更新逻辑")
        else:
            logger.error("❌ save_data() 缺少同步更新逻辑")
            return False

        # 验证代码中的关键字
        checks = {
            'UPDATE stock_info': 'UPDATE stock_info' in source,
            'SET pe = :pe, pb = :pb': 'SET pe = :pe, pb = :pb' in source,
            'market_cap': 'market_cap' in source,
        }

        all_passed = True
        for check_name, result in checks.items():
            if result:
                logger.info(f"  ✅ {check_name}: 存在")
            else:
                logger.error(f"  ❌ {check_name}: 缺失")
                all_passed = False

        return all_passed

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        return False


def test_stock_sector_list_collector():
    """测试 StockSectorListCollector 的同步更新功能"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 测试 2: StockSectorListCollector 同步更新")
    logger.info("=" * 60)

    try:
        from data_job.collectors.stock_sector_list_collector import StockSectorListCollector

        collector = StockSectorListCollector()

        # 检查是否有 update_stock_info_industry 方法
        if hasattr(collector, 'update_stock_info_industry'):
            logger.info("✅ update_stock_info_industry() 方法存在")

            # 检查方法实现
            import inspect
            source = inspect.getsource(collector.update_stock_info_industry)

            checks = {
                'SELECT symbol, sector_name': 'SELECT symbol, sector_name' in source,
                "UPDATE stock_info": 'UPDATE stock_info' in source,
                "SET industry = :industry": 'SET industry = :industry' in source,
            }

            all_passed = True
            for check_name, result in checks.items():
                if result:
                    logger.info(f"  ✅ {check_name}: 存在")
                else:
                    logger.error(f"  ❌ {check_name}: 缺失")
                    all_passed = False

            return all_passed
        else:
            logger.error("❌ update_stock_info_industry() 方法不存在")
            return False

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        return False


def verify_data_integrity():
    """验证数据完整性"""
    logger.info("\n" + "=" * 60)
    logger.info("🔍 数据完整性验证")
    logger.info("=" * 60)

    engine = get_local_engine()

    with engine.begin() as conn:
        # 检查 stock_valuation_daily 表
        result = conn.execute(text("""
            SELECT COUNT(DISTINCT code) FROM stock_valuation_daily
        """)).fetchone()
        logger.info(f"stock_valuation_daily: {result[0]} 只股票")

        # 检查 stock_sector_map 表
        result = conn.execute(text("""
            SELECT COUNT(DISTINCT symbol) FROM stock_sector_map
            WHERE sector_type = 'Industry'
        """)).fetchone()
        logger.info(f"stock_sector_map (Industry): {result[0]} 只股票")

        # 检查 stock_info 表
        result = conn.execute(text("""
            SELECT COUNT(*) FROM stock_info
        """)).fetchone()
        logger.info(f"stock_info: {result[0]} 只股票")

        # 检查同步更新的数据量
        logger.info("\nsync 字段统计:")
        for field in ['industry', 'pe', 'pb', 'market_cap']:
            result = conn.execute(text(f"""
                SELECT COUNT(*) FROM stock_info WHERE {field} IS NOT NULL
            """)).fetchone()
            percentage = (result[0] / 5472 * 100) if result[0] else 0
            logger.info(f"  {field}: {result[0]} ({percentage:.1f}%)")

        # 检查数据一致性
        logger.info("\n数据一致性检查:")

        # 检查 stock_info.pe 是否与 stock_valuation_daily 一致
        result = conn.execute(text("""
            SELECT COUNT(*)
            FROM stock_info si
            JOIN stock_valuation_daily svd ON si.symbol = svd.code
            WHERE svd.trade_date = (SELECT MAX(trade_date) FROM stock_valuation_daily)
            AND si.pe != svd.pe_ttm
            AND si.pe IS NOT NULL AND svd.pe_ttm IS NOT NULL
        """)).fetchone()

        if result[0] == 0:
            logger.info("  ✅ PE 数据一致")
        else:
            logger.warning(f"  ⚠️  PE 数据不一致: {result[0]} 条记录")

    return True


def main():
    """主测试函数"""
    logger.info("🚀 开始测试修改后的采集器")

    # 1. 验证测试前数据
    engine = verify_stock_info_before()

    # 2. 测试 StockValuationCollector
    test1_passed = test_stock_valuation_collector()

    # 3. 测试 StockSectorListCollector
    test2_passed = test_stock_sector_list_collector()

    # 4. 验证数据完整性
    verify_data_integrity()

    # 总结
    logger.info("\n" + "=" * 60)
    logger.info("📊 测试总结")
    logger.info("=" * 60)
    logger.info(f"  StockValuationCollector: {'✅ 通过' if test1_passed else '❌ 失败'}")
    logger.info(f"  StockSectorListCollector: {'✅ 通过' if test2_passed else '❌ 失败'}")

    if test1_passed and test2_passed:
        logger.info("\n✅ 所有测试通过！")
        logger.info("\n下一步:")
        logger.info("  1. 运行完整采集: python3 data_job/utils/run_all_collectors.py")
        logger.info("  2. 或单独测试: python3 data_job/collectors/stock_valuation_collector.py")
        return 0
    else:
        logger.error("\n❌ 部分测试失败，请检查")
        return 1


if __name__ == "__main__":
    exit(main())
