"""
EvoAlpha OS - 自动化交易流水线调度器
整合数据采集、RPS计算、股票池更新、策略选股的完整自动化流程

版本: v1.0
创建时间: 2026-01-20
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, date
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# ================= 路径适配 =================
backend_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(backend_dir))

# ================= 公共工具导入 =================
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger
from quant_engine.common import setup_quant_path, setup_logger as quant_setup_logger

# ================= 路径和网络初始化 =================
setup_backend_path()
setup_network_emergency_kit()
setup_quant_path()

# ================= Logger配置 =================
logger = setup_logger(__name__)

# ================= 导入数据采集器 =================
from data_job.collectors import (
    StockKlineCollector,
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

# ================= 导入量化引擎模块 =================
from quant_engine.pool.maintain_pool import StockPoolMaintainer
from quant_engine.runner.feature_runner import FeatureRunner
from quant_engine.runner.strategy_runner import StrategyRunner


class AutoTradingPipeline:
    """自动化交易流水线调度器"""

    def __init__(self):
        """初始化调度器"""
        self.scheduler = BlockingScheduler(logger=logger)
        self.feature_runner = FeatureRunner()
        self.strategy_runner = StrategyRunner()

        logger.info("=" * 80)
        logger.info("🚀 EvoAlpha OS - 自动化交易流水线调度器启动")
        logger.info("=" * 80)

    # ==================== 每日自动化流程 ====================

    def run_daily_pipeline(self):
        """
        每日自动化流程：
        1. 数据采集（15:30-16:00）
        2. RPS因子计算（16:00-16:15）
        3. 策略选股（16:15-16:30）
        """
        logger.info("\n" + "=" * 80)
        logger.info("📅 开始每日自动化交易流水线")
        logger.info(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)

        # ========== Step 1: 数据采集 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("📊 Step 1/3: 数据采集")
        logger.info("▶" * 40)

        collection_success = self._run_daily_collection()

        if not collection_success:
            logger.warning("⚠️ 数据采集部分失败，但继续执行后续流程...")

        # ========== Step 2: RPS因子计算 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🧮 Step 2/3: RPS因子计算")
        logger.info("▶" * 40)

        rps_success = self._run_rps_calculation()

        if not rps_success:
            logger.error("❌ RPS计算失败，跳过策略选股")
            return

        # ========== Step 3: 策略选股 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🎯 Step 3/3: 策略选股")
        logger.info("▶" * 40)

        self._run_strategy_selection()

        # ========== 完成 ==========
        logger.info("\n" + "=" * 80)
        logger.info("✅ 每日自动化交易流水线完成")
        logger.info(f"⏰ 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80 + "\n")

    def _run_daily_collection(self):
        """执行每日数据采集"""
        logger.info("\n📡 启动数据采集...")

        collectors = [
            ('StockKline', StockKlineCollector(), "30-45分钟"),
            ('SectorKline', SectorKlineCollector(), "5-10分钟"),
            ('ETFKline', ETFKlineCollector(), "10-15分钟"),
            ('StockValuation', StockValuationCollector(), "5-10分钟"),
            ('LimitBoards', LimitBoardsCollector(), "2-5分钟"),
            ('News', NewsCollector(), "10-20分钟"),
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
        logger.info("\n📊 数据采集完成:")
        logger.info(f"  ✅ 成功: {success_count}/{len(collectors)}")
        logger.info(f"  ❌ 失败: {failed_count}/{len(collectors)}")

        if results:
            logger.info("\n详细结果:")
            for name, status in results:
                logger.info(f"  {name}: {status}")

        return failed_count == 0

    def _run_rps_calculation(self):
        """执行RPS因子计算"""
        logger.info("\n🧮 启动RPS因子计算（增量模式）...")

        try:
            # 使用 FeatureRunner 批量运行所有RPS计算器
            results = self.feature_runner.run(mode='daily', calculators=['stock', 'sector', 'etf'])

            # 检查结果
            success_count = sum(1 for r in results.values() if r.get('success'))
            total_count = len(results)

            logger.info(f"\n📊 RPS计算完成:")
            logger.info(f"  ✅ 成功: {success_count}/{total_count}")

            for name, result in results.items():
                status = "✅ 成功" if result.get('success') else "❌ 失败"
                elapsed = result.get('elapsed', 0)
                logger.info(f"  {name.upper()}: {status} (耗时: {elapsed:.1f}秒)")

            return all(r.get('success') for r in results.values())

        except Exception as e:
            logger.error(f"❌ RPS计算失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _run_strategy_selection(self):
        """执行策略选股"""
        logger.info("\n🎯 启动策略选股...")

        # 运行 MRGC 策略
        try:
            result = self.strategy_runner.run(strategy_name='mrgc')

            if result.get('success'):
                logger.info(f"✅ MRGC策略选股完成 (耗时: {result.get('elapsed', 0):.1f}秒)")
                logger.info(f"💡 请查看 quant_preselect_results 表获取选股结果")
            else:
                logger.error(f"❌ MRGC策略选股失败: {result.get('error')}")

            return result.get('success', False)

        except Exception as e:
            logger.error(f"❌ 策略选股失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ==================== 每季度自动化流程 ====================

    def run_quarterly_pipeline(self):
        """
        每季度自动化流程：
        1. 数据采集（含基金持仓、财务摘要）
        2. 更新核心股票池
        3. RPS因子计算
        4. 策略选股
        """
        logger.info("\n" + "=" * 80)
        logger.info("💰 开始每季度自动化交易流水线")
        logger.info(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)

        # ========== Step 1: 季度数据采集 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("📊 Step 1/4: 季度数据采集")
        logger.info("▶" * 40)

        self._run_quarterly_collection()

        # ========== Step 2: 更新核心股票池 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🏊‍♂️ Step 2/4: 更新核心股票池")
        logger.info("▶" * 40)

        pool_success = self._update_stock_pool()

        if not pool_success:
            logger.warning("⚠️ 股票池更新失败，但继续执行后续流程...")

        # ========== Step 3: RPS因子计算 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🧮 Step 3/4: RPS因子计算")
        logger.info("▶" * 40)

        rps_success = self._run_rps_calculation()

        if not rps_success:
            logger.error("❌ RPS计算失败，跳过策略选股")
            return

        # ========== Step 4: 策略选股 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🎯 Step 4/4: 策略选股")
        logger.info("▶" * 40)

        self._run_strategy_selection()

        # ========== 完成 ==========
        logger.info("\n" + "=" * 80)
        logger.info("✅ 每季度自动化交易流水线完成")
        logger.info(f"⏰ 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80 + "\n")

    def _run_quarterly_collection(self):
        """执行每季度数据采集"""
        logger.info("\n📡 启动季度数据采集...")

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
        logger.info("\n📊 季度数据采集完成:")
        logger.info(f"  ✅ 成功: {success_count}/{len(collectors)}")
        logger.info(f"  ❌ 失败: {failed_count}/{len(collectors)}")

        if results:
            logger.info("\n详细结果:")
            for name, status in results:
                logger.info(f"  {name}: {status}")

        return failed_count == 0

    def _update_stock_pool(self):
        """更新核心股票池"""
        logger.info("\n🏊‍♂️ 启动核心股票池维护...")

        try:
            pool_maintainer = StockPoolMaintainer()
            pool_maintainer.run()
            logger.info("✅ 核心股票池更新完成")
            return True
        except Exception as e:
            logger.error(f"❌ 核心股票池更新失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ==================== 调度器配置 ====================

    def setup_jobs(self):
        """配置所有定时任务"""
        logger.info("📋 配置自动化交易流水线定时任务...")

        # 每日自动化流水线 - 工作日收盘后 15:30
        self.scheduler.add_job(
            self.run_daily_pipeline,
            trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=30),
            id='daily_pipeline',
            name='每日自动化交易流水线',
            misfire_grace_time=7200  # 错过时间后2小时内仍执行
        )
        logger.info("  ✅ 每日流水线: 工作日 15:30")
        logger.info("     流程: 数据采集 → RPS计算 → 策略选股")

        # 每季度自动化流水线 - 每季度（1/4/7/10月）15号 08:00
        self.scheduler.add_job(
            self.run_quarterly_pipeline,
            trigger=CronTrigger(month='1,4,7,10', day=15, hour=8, minute=0),
            id='quarterly_pipeline',
            name='每季度自动化交易流水线',
            misfire_grace_time=7200  # 错过时间后2小时内仍执行
        )
        logger.info("  ✅ 季度流水线: 每季度15号 08:00")
        logger.info("     流程: 数据采集 → 更新股票池 → RPS计算 → 策略选股")

        # 打印所有任务
        logger.info("\n📅 已配置的定时任务:")
        for job in self.scheduler.get_jobs():
            logger.info(f"  - {job.name}: {job.trigger}")

    def start(self):
        """启动调度器"""
        logger.info("\n" + "=" * 80)
        logger.info("🚀 自动化交易流水线调度器已启动，等待定时任务触发...")
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

    parser = argparse.ArgumentParser(
        description="EvoAlpha OS 自动化交易流水线调度器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 启动定时调度（推荐）
  python -m scripts.auto_trading_pipeline --mode schedule

  # 立即运行每日流水线
  python -m scripts.auto_trading_pipeline --mode daily

  # 立即运行季度流水线
  python -m scripts.auto_trading_pipeline --mode quarterly

流水线说明:
  每日流水线: 数据采集 → RPS计算 → 策略选股
  季度流水线: 数据采集 → 更新股票池 → RPS计算 → 策略选股
        """
    )

    parser.add_argument(
        '--mode',
        choices=['schedule', 'daily', 'quarterly'],
        default='schedule',
        help='运行模式: schedule=定时调度, daily=立即运行每日流水线, quarterly=立即运行季度流水线'
    )

    args = parser.parse_args()

    pipeline = AutoTradingPipeline()

    if args.mode == 'schedule':
        # 定时调度模式
        pipeline.setup_jobs()
        pipeline.start()

    elif args.mode == 'daily':
        # 立即运行每日流水线
        logger.info("🚀 手动模式：立即运行每日自动化交易流水线")
        pipeline.run_daily_pipeline()

    elif args.mode == 'quarterly':
        # 立即运行季度流水线
        logger.info("🚀 手动模式：立即运行季度自动化交易流水线")
        pipeline.run_quarterly_pipeline()


if __name__ == "__main__":
    main()
