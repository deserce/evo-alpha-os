"""
运行所有数据采集器的主脚本
按照依赖关系和优先级执行所有数据采集任务
"""
import sys
import logging
from datetime import datetime
from tqdm import tqdm

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
    StockKlineCollector,
    StockKlineCollectorOptimized,  # 优化版K线采集器
    SectorKlineCollector,
    ETFKlineCollector,
    NewsCollector,
    LimitBoardsCollector,
    IndexDataCollector,
    BSEIndexCollector,  # 北交所板块指数采集器
)

# 导入市场统计计算器
from data_job.utils.market_stats_calculator import calculate_market_stats


# ==================== K线采集器配置 ====================

def get_kline_collector_config():
    """
    获取K线采集器配置（从环境变量或使用默认值）

    环境变量：
        KLINE_MAX_WORKERS: 并发线程数（1-5）
        KLINE_MIN_DELAY: 最小延迟（秒）
        KLINE_MAX_DELAY: 最大延迟（秒）
        KLINE_BATCH_SIZE: 批次大小
        KLINE_BATCH_REST: 批次间休息时间（秒）
        KLINE_MODE: 预设模式（safe/normal/aggressive）

    Returns:
        dict: 采集器配置参数
    """
    import os

    # 检查是否使用预设模式
    mode = os.getenv('KLINE_MODE', 'normal')

    if mode == 'safe':
        config = {
            'max_workers': 2,
            'min_delay': 0.2,
            'max_delay': 0.4,
            'batch_size': 200,
            'batch_rest_time': 8
        }
    elif mode == 'aggressive':
        config = {
            'max_workers': 5,
            'min_delay': 0.1,
            'max_delay': 0.2,
            'batch_size': 1000,
            'batch_rest_time': 3
        }
    else:  # normal (默认)
        config = {
            'max_workers': 3,
            'min_delay': 0.15,
            'max_delay': 0.35,
            'batch_size': 500,
            'batch_rest_time': 5
        }

    # 环境变量覆盖预设配置
    if 'KLINE_MAX_WORKERS' in os.environ:
        config['max_workers'] = int(os.getenv('KLINE_MAX_WORKERS'))
    if 'KLINE_MIN_DELAY' in os.environ:
        config['min_delay'] = float(os.getenv('KLINE_MIN_DELAY'))
    if 'KLINE_MAX_DELAY' in os.environ:
        config['max_delay'] = float(os.getenv('KLINE_MAX_DELAY'))
    if 'KLINE_BATCH_SIZE' in os.environ:
        config['batch_size'] = int(os.getenv('KLINE_BATCH_SIZE'))
    if 'KLINE_BATCH_REST' in os.environ:
        config['batch_rest_time'] = int(os.getenv('KLINE_BATCH_REST'))

    return config


# ==================== 主采集函数 ====================

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

    for name, collector in tqdm(collectors_step1, desc="📋 基础数据采集", unit="个"):
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
        ('IndexData', IndexDataCollector()),
        ('StockValuation', StockValuationCollector()),
        ('MacroData', MacroDataCollector()),
    ]

    for name, collector in tqdm(collectors_step2, desc="📊 市场数据采集", unit="个"):
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

    for name, collector in tqdm(collectors_step3, desc="💰 财务数据采集", unit="个"):
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            collector.run()
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # ==================== Step 3.5: 北向资金持股（已停用） ====================
    # 北向资金持股数据仅需在系统初始化时采集一次，后续无需更新
    # 如需重新采集，请手动运行: python -m data_job.collectors.northbound_holdings_collector
    logger.info("\n" + "=" * 80)
    logger.info("⏭️  Step 3.5/5: 北向资金持股采集（已停用 - 初始化任务）")
    logger.info("💡 说明：北向资金仅需初始化时采集一次，已完成")
    logger.info("=" * 80)

    # ==================== Step 4: K线数据 ====================
    logger.info("\n" + "=" * 80)
    logger.info("📈 Step 4/6: 采集K线数据（耗时较长）")
    logger.info("=" * 80)

    # 获取K线采集器配置并使用优化版
    kline_config = get_kline_collector_config()
    logger.info(f"⚙️ K线采集器配置: 并发={kline_config['max_workers']}, "
               f"延迟={kline_config['min_delay']}-{kline_config['max_delay']}s")

    collectors_step4 = [
        ('StockKline', StockKlineCollectorOptimized(**kline_config)),
        ('SectorKline', SectorKlineCollector()),
        ('ETFKline', ETFKlineCollector()),
    ]

    for name, collector in tqdm(collectors_step4, desc="📈 K线数据采集", unit="个"):
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

    for name, collector in tqdm(collectors_step5, desc="📰 舆情数据采集", unit="个"):
        results['total'] += 1
        try:
            logger.info(f"\n▶️  正在运行: {name}")
            collector.run()
            results['success'].append(name)
            logger.info(f"✅ {name} 完成")
        except Exception as e:
            results['failed'].append((name, str(e)))
            logger.error(f"❌ {name} 失败: {e}")

    # ==================== Step 6: 市场统计计算 ====================
    logger.info("\n" + "=" * 80)
    logger.info("📊 Step 6/6: 计算市场统计数据")
    logger.info("=" * 80)

    try:
        logger.info("\n▶️  正在计算市场统计（增量模式）...")

        # 导入批量计算函数
        from data_job.utils.calculate_historical_stats import calculate_historical_stats as calc_batch_stats

        # 全量采集时，计算所有缺失的历史数据
        # 每日更新时，只计算最新一天
        logger.info("  📊 检查缺失的历史数据...")
        calc_batch_stats()  # 自动计算所有缺失的交易日

        results['total'] += 1
        results['success'].append('MarketStatsCalculator')
        logger.info(f"✅ 市场统计计算完成")
    except Exception as e:
        results['failed'].append(('MarketStatsCalculator', str(e)))
        logger.error(f"❌ 市场统计计算失败: {e}")

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

    # 获取K线采集器配置并使用优化版
    kline_config = get_kline_collector_config()
    logger.info(f"⚙️ K线采集器配置: 并发={kline_config['max_workers']}, "
               f"延迟={kline_config['min_delay']}-{kline_config['max_delay']}s")

    # 只运行需要每日更新的采集器
    daily_collectors = [
        ('IndexData', IndexDataCollector()),
        ('BSEIndex', BSEIndexCollector()),  # 北交所板块指数
        ('StockValuation', StockValuationCollector()),
        ('MacroData', MacroDataCollector()),
        ('StockKline', StockKlineCollectorOptimized(**kline_config)),
        ('SectorKline', SectorKlineCollector()),
        ('ETFKline', ETFKlineCollector()),
        ('News', NewsCollector()),
        ('LimitBoards', LimitBoardsCollector()),
    ]

    success_count = 0
    for name, collector in tqdm(daily_collectors, desc="🔄 每日数据更新", unit="个"):
        try:
            logger.info(f"▶️  {name}")
            collector.run()
            success_count += 1
        except Exception as e:
            logger.error(f"❌ {name}: {e}")

    # 计算市场统计（增量模式 - 只计算最新一天）
    try:
        logger.info("▶️  MarketStatsCalculator (最新一天)")
        calculate_market_stats()  # 只计算最新一天
        success_count += 1
        logger.info("✅ 市场统计完成")
    except Exception as e:
        logger.error(f"❌ MarketStatsCalculator: {e}")

    logger.info(f"✅ 每日更新完成: {success_count}/{len(daily_collectors) + 1}")
    return success_count == len(daily_collectors) + 1


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
