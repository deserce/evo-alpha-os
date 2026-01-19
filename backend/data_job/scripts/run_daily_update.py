"""
每日数据更新脚本
适合定时任务调用，仅更新增量数据
"""
import sys
from datetime import datetime

# 路径适配
sys.path.insert(0, '.')

from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)

# 导入采集器
from data_job.collectors import (
    StockValuationCollector,
    MacroDataCollector,
    StockKlineCollector,
    SectorKlineCollector,
    ETFKlineCollector,
    NewsCollector,
    LimitBoardsCollector,
)


def run_daily_update():
    """
    每日数据更新任务
    仅更新需要每日刷新的数据，跳过基础数据采集
    """
    logger.info("=" * 80)
    logger.info("🔄 每日数据更新任务启动")
    logger.info(f"📅 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

    results = {
        'success': [],
        'failed': [],
        'total': 0
    }

    # 每日需要更新的采集器列表
    daily_collectors = [
        ('StockValuation', StockValuationCollector()),
        ('MacroData', MacroDataCollector()),
        ('News', NewsCollector(days=1)),
        ('LimitBoards', LimitBoardsCollector(days=1)),
    ]

    # K线数据更新（根据需要启用，数据量大）
    update_kline = False  # 默认不更新K线，可根据需要修改
    if update_kline:
        daily_collectors.extend([
            ('StockKline', StockKlineCollector()),
            ('SectorKline', SectorKlineCollector()),
            ('ETFKline', ETFKlineCollector()),
        ])

    for name, collector in daily_collectors:
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            collector.run()
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # 输出结果
    logger.info("\n" + "=" * 80)
    logger.info("📊 每日更新完成总结")
    logger.info("=" * 80)
    logger.info(f"总任务数: {results['total']}")
    logger.info(f"✅ 成功: {len(results['success'])}")
    logger.info(f"❌ 失败: {len(results['failed'])}")

    if results['success']:
        logger.info("\n✅ 成功完成的采集器:")
        for name in results['success']:
            logger.info(f"  - {name}")

    if results['failed']:
        logger.info("\n❌ 失败的采集器:")
        for name, error in results['failed']:
            logger.info(f"  - {name}: {error}")

    logger.info(f"\n📅 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

    return len(results['failed']) == 0


if __name__ == "__main__":
    success = run_daily_update()
    sys.exit(0 if success else 1)
