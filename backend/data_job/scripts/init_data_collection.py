"""
EvoAlpha OS - 初始化数据采集脚本
用于首次全量采集所有数据
按依赖关系顺序执行，确保数据完整性
"""

import sys
import time
from pathlib import Path
from datetime import datetime

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
    StockSectorListCollector,
    ETFInfoCollector,
    StockValuationCollector,
    MacroDataCollector,
    FinanceSummaryCollector,
    FundHoldingsCollector,
    StockKlineCollector,
    SectorKlineCollector,
    ETFKlineCollector,
    NewsCollector,
    LimitBoardsCollector,
)


class InitialDataCollector:
    """初始化数据采集器 - 首次全量采集"""

    def __init__(self):
        """初始化"""
        self.start_time = datetime.now()
        logger.info("=" * 80)
        logger.info("🚀 EvoAlpha OS - 初始化数据采集")
        logger.info(f"📅 开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)

    # ==================== Step 1: 基础数据 ====================

    def step1_collect_basic_data(self):
        """采集基础数据"""
        logger.info("\n" + "=" * 80)
        logger.info("📋 Step 1/5: 采集基础数据")
        logger.info("📊 包含: 股票列表、板块映射、ETF信息")
        logger.info("⏱️  预计耗时: 15-25分钟")
        logger.info("=" * 80)

        collectors = [
            ('StockSectorList', StockSectorListCollector(), "10-15分钟", "股票列表和板块映射"),
            ('ETFInfo', ETFInfoCollector(), "5-10分钟", "ETF基础信息"),
        ]

        return self._run_collectors(collectors, step_name="基础数据")

    # ==================== Step 2: 市场数据 ====================

    def step2_collect_market_data(self):
        """采集市场数据"""
        logger.info("\n" + "=" * 80)
        logger.info("📊 Step 2/5: 采集市场数据")
        logger.info("📊 包含: 估值数据、宏观数据")
        logger.info("⏱️  预计耗时: 15-25分钟")
        logger.info("=" * 80)

        collectors = [
            ('StockValuation', StockValuationCollector(), "2-3分钟", "股票估值数据"),
            ('MacroData', MacroDataCollector(), "10-15分钟", "宏观指标数据"),
        ]

        return self._run_collectors(collectors, step_name="市场数据")

    # ==================== Step 3: 财务数据 ====================

    def step3_collect_financial_data(self):
        """采集财务数据"""
        logger.info("\n" + "=" * 80)
        logger.info("💰 Step 3/5: 采集财务数据")
        logger.info("📊 包含: 基金持股、财务摘要")
        logger.info("⏱️  预计耗时: 10-20分钟")
        logger.info("=" * 80)

        collectors = [
            ('FundHoldings', FundHoldingsCollector(), "10-15分钟", "基金季度持仓"),
            ('FinanceSummary', FinanceSummaryCollector(), "2-3小时", "财务业绩报表（耗时较长）"),
        ]

        return self._run_collectors(collectors, step_name="财务数据")

    # ==================== Step 4: K线数据 ====================

    def step4_collect_kline_data(self):
        """采集K线数据"""
        logger.info("\n" + "=" * 80)
        logger.info("📈 Step 4/5: 采集K线数据（核心数据，耗时较长）")
        logger.info("📊 包含: 个股K线、板块K线、ETF K线")
        logger.info("⏱️  预计耗时: 3.5-4.5小时（首次采集）")
        logger.info("💡 提示: 这是核心数据，采集时间较长，请耐心等待")
        logger.info("=" * 80)

        # 警告用户
        logger.info("\n⚠️  重要提示:")
        logger.info("  - 个股K线采集约 3-4小时（5472只股票）")
        logger.info("  - 板块K线采集约 10-15分钟（86个板块）")
        logger.info("  - ETF K线采集约 10-15分钟（数百只ETF）")
        logger.info("  - 建议在空闲时间运行此步骤")
        logger.info("  - 如需中断，按 Ctrl+C（已采集的数据会保存）")

        import time
        time.sleep(5)  # 给用户5秒时间阅读提示

        collectors = [
            ('StockKline', StockKlineCollector(), "3-4小时", "个股日级行情（5472只）"),
            ('SectorKline', SectorKlineCollector(), "10-15分钟", "板块指数行情（86个）"),
            ('ETFKline', ETFKlineCollector(), "10-15分钟", "ETF基金行情"),
        ]

        return self._run_collectors(collectors, step_name="K线数据")

    # ==================== Step 5: 舆情数据 ====================

    def step5_collect_sentiment_data(self):
        """采集舆情数据"""
        logger.info("\n" + "=" * 80)
        logger.info("📰 Step 5/5: 采集舆情数据")
        logger.info("📊 包含: 新闻舆情、连板数据")
        logger.info("⏱️  预计耗时: 10-20分钟")
        logger.info("=" * 80)

        collectors = [
            ('News', NewsCollector(), "5-10分钟", "财经新闻（最近3天）"),
            ('LimitBoards', LimitBoardsCollector(), "5-10分钟", "连板数据（最近5天）"),
        ]

        return self._run_collectors(collectors, step_name="舆情数据")

    # ==================== 通用执行方法 ====================

    def _run_collectors(self, collectors, step_name):
        """
        执行采集器列表

        Args:
            collectors: [(name, collector, estimated_time, description), ...]
            step_name: 步骤名称

        Returns:
            dict: 执行结果统计
        """
        success_count = 0
        failed_count = 0
        total = len(collectors)
        results = []

        for i, (name, collector, estimated_time, description) in enumerate(collectors, 1):
            logger.info(f"\n[{i}/{total}] ▶️  {name} - {description}")
            logger.info(f"⏱️  预计耗时: {estimated_time}")

            try:
                start = time.time()
                collector.run()
                elapsed = time.time() - start

                success_count += 1
                results.append((name, "✅ 成功", elapsed))
                logger.info(f"✅ {name} 完成 (实际耗时: {self._format_time(elapsed)})")

            except KeyboardInterrupt:
                logger.warning(f"\n⚠️  用户中断: {name}")
                logger.warning(f"💡 提示: 已采集的数据已保存，可重新运行继续采集")
                results.append((name, "⚠️  用户中断", 0))
                failed_count += 1
                raise  # 向上传播中断信号

            except Exception as e:
                failed_count += 1
                results.append((name, f"❌ 失败: {str(e)}", 0))
                logger.error(f"❌ {name} 失败: {e}")
                # 继续执行下一个采集器

        # 输出结果
        logger.info(f"\n{'=' * 80}")
        logger.info(f"📊 {step_name}采集完成:")
        logger.info(f"  ✅ 成功: {success_count}/{total}")
        logger.info(f"  ❌ 失败: {failed_count}/{total}")

        if results:
            logger.info("\n详细结果:")
            for name, status, elapsed in results:
                if elapsed > 0:
                    logger.info(f"  {name}: {status} (耗时: {self._format_time(elapsed)})")
                else:
                    logger.info(f"  {name}: {status}")

        logger.info("=" * 80)

        return {
            'success': success_count,
            'failed': failed_count,
            'total': total,
            'results': results
        }

    @staticmethod
    def _format_time(seconds):
        """格式化时间显示"""
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.0f}分钟"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}小时"

    # ==================== 完整流程 ====================

    def run_all(self):
        """执行完整的初始化采集流程"""
        logger.info("\n" + "=" * 80)
        logger.info("🎯 开始执行完整初始化流程")
        logger.info("=" * 80)

        steps = [
            ("Step 1/5: 基础数据", self.step1_collect_basic_data),
            ("Step 2/5: 市场数据", self.step2_collect_market_data),
            ("Step 3/5: 财务数据", self.step3_collect_financial_data),
            ("Step 4/5: K线数据", self.step4_collect_kline_data),
            ("Step 5/5: 舆情数据", self.step5_collect_sentiment_data),
        ]

        results_summary = []

        try:
            for step_name, step_func in steps:
                logger.info(f"\n🚀 执行 {step_name}")
                result = step_func()
                results_summary.append((step_name, result))

        except KeyboardInterrupt:
            logger.warning("\n" + "=" * 80)
            logger.warning("⚠️  用户中断初始化流程")
            logger.warning("💡 提示: 已采集的数据已保存")
            logger.warning("💡 提示: 重新运行脚本可继续采集")
            logger.warning("=" * 80)

        # 输出总结
        self._print_summary(results_summary)

    def _print_summary(self, results_summary):
        """打印采集总结"""
        end_time = datetime.now()
        total_time = (end_time - self.start_time).total_seconds()

        logger.info("\n" + "=" * 80)
        logger.info("📊 初始化数据采集总结")
        logger.info("=" * 80)
        logger.info(f"📅 开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"📅 结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"⏱️  总耗时: {self._format_time(total_time)}")

        if results_summary:
            logger.info("\n各步骤执行情况:")
            for step_name, result in results_summary:
                if result:
                    logger.info(f"  {step_name}:")
                    logger.info(f"    ✅ 成功: {result['success']}/{result['total']}")
                    logger.info(f"    ❌ 失败: {result['failed']}/{result['total']}")

        logger.info("\n" + "=" * 80)
        logger.info("🎉 初始化数据采集完成！")
        logger.info("💡 下一步: 运行定时调度器，开启自动采集")
        logger.info("   命令: python -m data_job.utils.scheduler --mode schedule")
        logger.info("=" * 80 + "\n")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="EvoAlpha OS 初始化数据采集")
    parser.add_argument(
        '--step',
        type=int,
        choices=[1, 2, 3, 4, 5],
        help='只执行指定步骤 (1=基础数据, 2=市场数据, 3=财务数据, 4=K线数据, 5=舆情数据)'
    )

    args = parser.parse_args()

    collector = InitialDataCollector()

    if args.step:
        # 只执行指定步骤
        step_map = {
            1: collector.step1_collect_basic_data,
            2: collector.step2_collect_market_data,
            3: collector.step3_collect_financial_data,
            4: collector.step4_collect_kline_data,
            5: collector.step5_collect_sentiment_data,
        }
        step_map[args.step]()
    else:
        # 执行完整流程
        collector.run_all()


if __name__ == "__main__":
    main()
