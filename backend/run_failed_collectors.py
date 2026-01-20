"""
运行之前失败的采集器
"""
import sys
import logging
from pathlib import Path

# 路径适配
backend_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(backend_dir))

from data_job.collectors import (
    SectorKlineCollector,
    StockValuationCollector,
    LimitBoardsCollector,
    NewsCollector,
)

# Logger配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """运行之前失败的采集器"""
    logger.info("=" * 80)
    logger.info("🔄 重新运行之前失败的采集器")
    logger.info("=" * 80)

    # 之前失败的采集器
    failed_collectors = [
        ('SectorKline', SectorKlineCollector(), "5-10分钟"),
        ('StockValuation', StockValuationCollector(), "5-10分钟"),
        ('LimitBoards', LimitBoardsCollector(), "2-5分钟"),
        ('News', NewsCollector(), "10-20分钟"),
    ]

    success_count = 0
    failed_count = 0
    results = []

    for name, collector, estimated_time in failed_collectors:
        logger.info(f"\n{'▶' * 40}")
        logger.info(f"▶️  正在运行: {name} (预计耗时: {estimated_time})")
        logger.info(f"{'▶' * 40}")
        try:
            collector.run()
            success_count += 1
            results.append((name, "✅ 成功"))
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            failed_count += 1
            results.append((name, f"❌ 失败: {e}"))
            logger.error(f"❌ {name} 失败: {e}")
            import traceback
            traceback.print_exc()

    # 输出结果
    logger.info("\n" + "=" * 80)
    logger.info("📊 采集器重跑完成:")
    logger.info(f"  ✅ 成功: {success_count}/{len(failed_collectors)}")
    logger.info(f"  ❌ 失败: {failed_count}/{len(failed_collectors)}")

    if results:
        logger.info("\n详细结果:")
        for name, status in results:
            logger.info(f"  {name}: {status}")

    logger.info("=" * 80)

    return failed_count == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
