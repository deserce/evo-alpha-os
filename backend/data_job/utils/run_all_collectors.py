"""
运行所有数据采集器的主脚本
按照依赖关系和优先级执行所有数据采集任务
"""
import sys
import logging
from datetime import datetime

# 路径适配
current_dir = sys.path.insert(0, '.')

from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)

# 导入所有采集器
from data_job.collectors import (
    StockSectorListCollector,
    ETFInfoCollector,
    StockValuationCollector,
    MacroDataCollector,
    FinanceSummaryCollector,
    FundHoldingsCollector,
    NorthboundHoldingsCollector,
    StockKlineCollector,
    SectorKlineCollector,
    ETFKlineCollector,
    NewsCollector,
    LimitBoardsCollector,
)


def run_all_collectors():
    """
    运行所有数据采集器

    执行顺序（按依赖关系）：
    1. 基础数据：StockSectorList, ETFInfo
    2. 市场数据：StockValuation, MacroData
    3. 财务数据：FinanceSummary, CapitalFlow
    4. K线数据：StockKline, SectorKline, ETFKline
    5. 舆情数据：News, LimitBoards
    """

    logger.info("=" * 80)
    logger.info("🚀 EvoAlpha OS - 数据采集系统启动")
    logger.info(f"📅 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

    results = {
        'success': [],
        'failed': [],
        'total': 0
    }

    # ==================== Step 1: 基础数据 ====================
    logger.info("\n" + "=" * 80)
    logger.info("📋 Step 1/5: 采集基础数据")
    logger.info("=" * 80)

    collectors_step1 = [
        ('StockSectorList', StockSectorListCollector()),
        ('ETFInfo', ETFInfoCollector()),
    ]

    for name, collector in collectors_step1:
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            collector.run()
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # ==================== Step 2: 市场数据 ====================
    logger.info("\n" + "=" * 80)
    logger.info("📊 Step 2/5: 采集市场数据")
    logger.info("=" * 80)

    collectors_step2 = [
        ('StockValuation', StockValuationCollector()),
        ('MacroData', MacroDataCollector()),
    ]

    for name, collector in collectors_step2:
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            collector.run()
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # ==================== Step 3: 财务数据 ====================
    logger.info("\n" + "=" * 80)
    logger.info("💰 Step 3/5: 采集财务数据")
    logger.info("=" * 80)

    collectors_step3 = [
        ('FinanceSummary', FinanceSummaryCollector()),
        ('FundHoldings', FundHoldingsCollector()),
    ]

    for name, collector in collectors_step3:
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            collector.run()
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # ==================== Step 3.5: 长时间运行任务 ====================
    logger.info("\n" + "=" * 80)
    logger.info("⏰ Step 3.5/5: 长时间运行任务（可选）")
    logger.info("⚠️  注意：北向资金持股采集需要约3-4小时，采集5800只股票")
    logger.info("💡 提示：如需跳过，请按 Ctrl+C 中断")
    logger.info("=" * 80)

    collectors_step3_5 = [
        ('NorthboundHoldings', NorthboundHoldingsCollector()),
    ]

    for name, collector in collectors_step3_5:
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            # 北向资金采集需要特殊参数
            collector.run(collect_all_stocks=True)
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except KeyboardInterrupt:
            logger.warning(f"⚠️  用户中断 {name}")
            results['failed'].append((name, '用户中断'))
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # ==================== Step 4: K线数据 ====================
    logger.info("\n" + "=" * 80)
    logger.info("📈 Step 4/6: 采集K线数据（耗时较长）")
    logger.info("=" * 80)

    collectors_step4 = [
        ('StockKline', StockKlineCollector()),
        ('SectorKline', SectorKlineCollector()),
        ('ETFKline', ETFKlineCollector()),
    ]

    for name, collector in collectors_step4:
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            collector.run()
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # ==================== Step 5: 舆情数据 ====================
    logger.info("\n" + "=" * 80)
    logger.info("📰 Step 5/6: 采集舆情数据")
    logger.info("=" * 80)

    collectors_step5 = [
        ('News', NewsCollector()),
        ('LimitBoards', LimitBoardsCollector()),
    ]

    for name, collector in collectors_step5:
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            collector.run()
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # ==================== 总结报告 ====================
    logger.info("\n" + "=" * 80)
    logger.info("📊 采集任务完成总结")
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

    return results


def run_daily_update():
    """
    运行每日更新任务（仅更新增量数据）
    适合定时任务调用
    """
    logger.info("🔄 运行每日数据更新...")

    # 只运行需要每日更新的采集器
    daily_collectors = [
        ('StockValuation', StockValuationCollector()),
        ('MacroData', MacroDataCollector()),
        ('StockKline', StockKlineCollector()),
        ('SectorKline', SectorKlineCollector()),
        ('ETFKline', ETFKlineCollector()),
        ('News', NewsCollector(days=1)),
        ('LimitBoards', LimitBoardsCollector(days=1)),
    ]

    success_count = 0
    for name, collector in daily_collectors:
        try:
            logger.info(f"▶️  {name}")
            collector.run()
            success_count += 1
        except Exception as e:
            logger.error(f"❌ {name}: {e}")

    logger.info(f"✅ 每日更新完成: {success_count}/{len(daily_collectors)}")
    return success_count == len(daily_collectors)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha OS 数据采集系统")
    parser.add_argument(
        '--mode',
        choices=['all', 'daily'],
        default='all',
        help='运行模式: all=全量采集, daily=每日增量更新'
    )

    args = parser.parse_args()

    if args.mode == 'all':
        run_all_collectors()
    else:
        run_daily_update()
