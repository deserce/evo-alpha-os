"""
EvoAlpha OS - 数据采集定时调度器
支持每日、每月、每季度自动采集数据
"""

import sys
import logging
from pathlib import Path
from datetime import time, date
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# 路径适配
backend_dir = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(backend_dir))

from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)

# 导入所有采集器
from data_job.collectors import (
    StockKlineCollector,
    StockKlineCollectorOptimized,  # 优化版K线采集器
    SectorKlineCollector,
    ETFKlineCollector,
    StockValuationCollector,
    LimitBoardsCollector,
    NewsCollector,
    MacroDataCollector,
    ETFInfoCollector,
    StockSectorListCollector,
    FundHoldingsCollector,
    FinanceSummaryCollector,
)


class CollectionScheduler:
    """数据采集定时调度器"""

    def __init__(self):
        """初始化调度器"""
        self.scheduler = BlockingScheduler(logger=logger)
        logger.info("=" * 80)
        logger.info("🚀 EvoAlpha OS - 数据采集调度器启动")
        logger.info("=" * 80)

    # ==================== 每日采集任务 ====================

    def run_daily_collection(self):
        """执行每日数据采集"""
        logger.info("\n" + "=" * 80)
        logger.info("📈 开始每日数据采集任务")
        logger.info(f"⏰ 开始时间: {date.today()}")
        logger.info("=" * 80)

        # 导入每日更新函数
        from data_job.utils.run_all_collectors import run_daily_update

        # 调用每日更新（不包含StockSectorList）
        success = run_daily_update()

        if success:
            logger.info("✅ 每日采集任务全部成功")
        else:
            logger.warning("⚠️  部分采集任务失败，请查看日志")

        logger.info("=" * 80 + "\n")

    # ==================== 每月采集任务 ====================

    def run_monthly_collection(self):
        """执行每月数据采集"""
        logger.info("\n" + "=" * 80)
        logger.info("📅 开始每月数据采集任务")
        logger.info(f"⏰ 开始时间: {date.today()}")
        logger.info("=" * 80)

        collectors = [
            ('MacroData', MacroDataCollector(), "10-15分钟"),
            ('ETFInfo', ETFInfoCollector(), "5-10分钟"),
            ('StockSectorList', StockSectorListCollector(), "10-15分钟"),
        ]

        success_count = 0
        failed_count = 0
        results = []

        for name, collector, estimated_time in collectors:
            logger.info(f"\n▶️  正在运行: {name} (预计耗时: {estimated_time})")
            try:
                collector.run()
                success_count += 1
                results.append((name, "✅ 成功"))
                logger.info(f"✅ {name} 完成")
            except Exception as e:
                failed_count += 1
                results.append((name, f"❌ 失败: {e}"))
                logger.error(f"❌ {name} 失败: {e}")

        # 输出结果
        logger.info("\n" + "=" * 80)
        logger.info("📊 每月采集任务完成")
        logger.info(f"✅ 成功: {success_count}/{len(collectors)}")
        logger.info(f"❌ 失败: {failed_count}/{len(collectors)}")

        if results:
            logger.info("\n详细结果:")
            for name, status in results:
                logger.info(f"  {name}: {status}")

        logger.info("=" * 80 + "\n")

    # ==================== 每季度采集任务 ====================

    def run_quarterly_collection(self):
        """执行每季度数据采集"""
        logger.info("\n" + "=" * 80)
        logger.info("💰 开始每季度数据采集任务")
        logger.info(f"⏰ 开始时间: {date.today()}")
        logger.info("=" * 80)

        collectors = [
            ('FundHoldings', FundHoldingsCollector(), "10-15分钟"),
            ('FinanceSummary', FinanceSummaryCollector(), "2-3小时"),
        ]

        success_count = 0
        failed_count = 0
        results = []

        for name, collector, estimated_time in collectors:
            logger.info(f"\n▶️  正在运行: {name} (预计耗时: {estimated_time})")
            try:
                collector.run()
                success_count += 1
                results.append((name, "✅ 成功"))
                logger.info(f"✅ {name} 完成")
            except Exception as e:
                failed_count += 1
                results.append((name, f"❌ 失败: {e}"))
                logger.error(f"❌ {name} 失败: {e}")

        # 输出结果
        logger.info("\n" + "=" * 80)
        logger.info("📊 每季度采集任务完成")
        logger.info(f"✅ 成功: {success_count}/{len(collectors)}")
        logger.info(f"❌ 失败: {failed_count}/{len(collectors)}")

        if results:
            logger.info("\n详细结果:")
            for name, status in results:
                logger.info(f"  {name}: {status}")

        logger.info("=" * 80 + "\n")

    # ==================== 调度器配置 ====================

    def setup_jobs(self):
        """配置所有定时任务"""
        logger.info("📋 配置定时任务...")

        # 每日采集任务 - 交易日收盘后 15:30
        self.scheduler.add_job(
            self.run_daily_collection,
            trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=30),
            id='daily_collection',
            name='每日数据采集',
            misfire_grace_time=3600  # 错过时间后1小时内仍执行
        )
        logger.info("  ✅ 每日采集任务: 工作日 15:30")

        # 每月采集任务 - 每月1号 08:00
        self.scheduler.add_job(
            self.run_monthly_collection,
            trigger=CronTrigger(day=1, hour=8, minute=0),
            id='monthly_collection',
            name='每月数据采集',
            misfire_grace_time=7200  # 错过时间后2小时内仍执行
        )
        logger.info("  ✅ 每月采集任务: 每月1号 08:00")

        # 每季度采集任务 - 每季度（1/4/7/10月）15号 08:00
        self.scheduler.add_job(
            self.run_quarterly_collection,
            trigger=CronTrigger(month='1,4,7,10', day=15, hour=8, minute=0),
            id='quarterly_collection',
            name='每季度数据采集',
            misfire_grace_time=7200  # 错过时间后2小时内仍执行
        )
        logger.info("  ✅ 每季度采集任务: 每季度15号 08:00")

        # 打印所有任务
        logger.info("\n📅 已配置的定时任务:")
        for job in self.scheduler.get_jobs():
            logger.info(f"  - {job.name}: {job.trigger}")

    def start(self):
        """启动调度器"""
        logger.info("\n" + "=" * 80)
        logger.info("🚀 调度器已启动，等待定时任务触发...")
        logger.info("💡 提示: 按 Ctrl+C 停止调度器")
        logger.info("=" * 80 + "\n")

        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("\n" + "=" * 80)
            logger.info("⏹️  调度器已停止")
            logger.info("=" * 80)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha OS 数据采集调度器")
    parser.add_argument(
        '--mode',
        choices=['schedule', 'daily', 'monthly', 'quarterly'],
        default='schedule',
        help='运行模式: schedule=定时调度, daily=立即运行每日任务, monthly=立即运行每月任务, quarterly=立即运行季度任务'
    )

    args = parser.parse_args()

    scheduler = CollectionScheduler()

    if args.mode == 'schedule':
        # 定时调度模式
        scheduler.setup_jobs()
        scheduler.start()

    elif args.mode == 'daily':
        # 立即运行每日采集
        logger.info("🚀 手动模式：立即运行每日采集任务")
        scheduler.run_daily_collection()

    elif args.mode == 'monthly':
        # 立即运行每月采集
        logger.info("🚀 手动模式：立即运行每月采集任务")
        scheduler.run_monthly_collection()

    elif args.mode == 'quarterly':
        # 立即运行季度采集
        logger.info("🚀 手动模式：立即运行季度采集任务")
        scheduler.run_quarterly_collection()


if __name__ == "__main__":
    main()
