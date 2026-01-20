"""
EvoAlpha OS - 自动化交易流水线调度器（统一调度层）

职责：
- 调用 data_job 模块进行数据采集
- 调用 quant_engine 模块进行因子计算和策略选股
- 整合完整的自动化流程

架构：
  data_job/     → 数据层（采集）
  quant_engine/ → 量化层（计算+选股）
  auto_pipeline → 调度层（编排）

版本: v1.0
创建时间: 2026-01-20
"""

import sys
import logging
from pathlib import Path
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# ================= 路径适配 =================
backend_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(backend_dir))

# ================= 导入数据层 =================
from data_job.utils.scheduler import CollectionScheduler
from data_job.collectors import (
    StockKlineCollector,
    SectorKlineCollector,
    ETFKlineCollector,
    StockValuationCollector,
    LimitBoardsCollector,
    NewsCollector,
    FundHoldingsCollector,
    FinanceSummaryCollector,
)

# ================= 导入量化层 =================
from quant_engine.pool.maintain_pool import StockPoolMaintainer
from quant_engine.runner.feature_runner import FeatureRunner
from quant_engine.runner.strategy_runner import StrategyRunner

# ================= Logger配置 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AutoTradingPipeline:
    """
    自动化交易流水线调度器

    职责：
    1. 编排数据采集和量化计算的完整流程
    2. 每日流程：数据采集 → RPS计算 → 策略选股
    3. 季度流程：数据采集 → 更新股票池 → RPS计算 → 策略选股
    """

    def __init__(self):
        """初始化调度器"""
        self.scheduler = BlockingScheduler(logger=logger)

        # 量化层组件
        self.feature_runner = FeatureRunner()
        self.strategy_runner = StrategyRunner()

        logger.info("=" * 80)
        logger.info("🚀 EvoAlpha OS - 自动化交易流水线调度器")
        logger.info("=" * 80)

    # ==================== 每日自动化流程 ====================

    def run_daily_pipeline(self):
        """
        每日自动化流程：
        1. 数据采集（调用 data_job）
        2. RPS因子计算（调用 quant_engine）
        3. 策略选股（调用 quant_engine）
        """
        logger.info("\n" + "=" * 80)
        logger.info("📅 开始每日自动化交易流水线")
        logger.info(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)

        # ========== Step 1: 数据采集 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("📊 Step 1/3: 数据采集 (data_job)")
        logger.info("▶" * 40)

        collection_success = self._run_daily_collection()

        if not collection_success:
            logger.warning("⚠️ 数据采集部分失败，但继续执行后续流程...")

        # ========== Step 2: RPS因子计算 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🧮 Step 2/3: RPS因子计算 (quant_engine)")
        logger.info("▶" * 40)

        rps_success = self._run_rps_calculation()

        if not rps_success:
            logger.error("❌ RPS计算失败，跳过策略选股")
            return False

        # ========== Step 3: 策略选股 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🎯 Step 3/3: 策略选股 (quant_engine)")
        logger.info("▶" * 40)

        self._run_strategy_selection()

        # ========== 完成 ==========
        logger.info("\n" + "=" * 80)
        logger.info("✅ 每日自动化交易流水线完成")
        logger.info(f"⏰ 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80 + "\n")

        return True

    def _run_daily_collection(self):
        """
        执行每日数据采集（调用 data_job 层）

        采集内容：
        - 个股K线
        - 板块K线
        - ETF K线
        - 股票估值
        - 连板数据
        - 新闻舆情
        """
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
        """
        执行RPS因子计算（调用 quant_engine 层）

        计算内容：
        - 个股RPS (5/10/20/50/120/250日)
        - 板块RPS (5/10/20/50/120/250日)
        - ETF RPS (5/10/20/50/120/250日)
        """
        logger.info("\n🧮 启动RPS因子计算（增量模式）...")

        try:
            # 使用 FeatureRunner 批量运行所有RPS计算器
            results = self.feature_runner.run(calculator_names=['stock', 'sector', 'etf'], mode='daily')

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
        """
        执行策略选股（调用 quant_engine 层）

        选股策略：
        - MRGC策略
        """
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
        1. 季度数据采集（调用 data_job）
        2. 更新核心股票池（调用 quant_engine）
        3. RPS因子计算（调用 quant_engine）
        4. 策略选股（调用 quant_engine）
        """
        logger.info("\n" + "=" * 80)
        logger.info("💰 开始每季度自动化交易流水线")
        logger.info(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)

        # ========== Step 1: 季度数据采集 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("📊 Step 1/4: 季度数据采集 (data_job)")
        logger.info("▶" * 40)

        self._run_quarterly_collection()

        # ========== Step 2: 更新核心股票池 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🏊‍♂️ Step 2/4: 更新核心股票池 (quant_engine)")
        logger.info("▶" * 40)

        pool_success = self._update_stock_pool()

        if not pool_success:
            logger.warning("⚠️ 股票池更新失败，但继续执行后续流程...")

        # ========== Step 3: RPS因子计算 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🧮 Step 3/4: RPS因子计算 (quant_engine)")
        logger.info("▶" * 40)

        rps_success = self._run_rps_calculation()

        if not rps_success:
            logger.error("❌ RPS计算失败，跳过策略选股")
            return False

        # ========== Step 4: 策略选股 ==========
        logger.info("\n" + "▶" * 40)
        logger.info("🎯 Step 4/4: 策略选股 (quant_engine)")
        logger.info("▶" * 40)

        self._run_strategy_selection()

        # ========== 完成 ==========
        logger.info("\n" + "=" * 80)
        logger.info("✅ 每季度自动化交易流水线完成")
        logger.info(f"⏰ 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80 + "\n")

        return True

    def _run_quarterly_collection(self):
        """
        执行每季度数据采集（调用 data_job 层）

        采集内容：
        - 基金季度持仓
        - 财务摘要
        """
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
        """
        更新核心股票池（调用 quant_engine 层）

        基于基金持股和北向资金筛选核心股票
        """
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
        logger.info("     流程: 数据采集(data_job) → RPS计算(quant_engine) → 策略选股(quant_engine)")

        # 每季度自动化流水线 - 每季度（1/4/7/10月）15号 08:00
        self.scheduler.add_job(
            self.run_quarterly_pipeline,
            trigger=CronTrigger(month='1,4,7,10', day=15, hour=8, minute=0),
            id='quarterly_pipeline',
            name='每季度自动化交易流水线',
            misfire_grace_time=7200  # 错过时间后2小时内仍执行
        )
        logger.info("  ✅ 季度流水线: 每季度15号 08:00")
        logger.info("     流程: 数据采集(data_job) → 更新股票池(quant_engine) → RPS计算 → 策略选股")

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
架构说明:
  data_job/      → 数据层（采集）
  quant_engine/  → 量化层（计算+选股）
  auto_pipeline  → 调度层（编排）

示例:
  # 启动定时调度（推荐用于生产环境）
  python auto_pipeline.py --mode schedule

  # 立即运行每日流水线
  python auto_pipeline.py --mode daily

  # 立即运行季度流水线
  python auto_pipeline.py --mode quarterly

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
        success = pipeline.run_daily_pipeline()
        sys.exit(0 if success else 1)

    elif args.mode == 'quarterly':
        # 立即运行季度流水线
        logger.info("🚀 手动模式：立即运行季度自动化交易流水线")
        success = pipeline.run_quarterly_pipeline()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
