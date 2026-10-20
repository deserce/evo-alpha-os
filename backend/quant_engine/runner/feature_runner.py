"""
量化引擎 - 因子计算运行器
批量运行所有RPS计算器
"""
import sys
import os
import time
import argparse
from datetime import date
from tqdm import tqdm

# 环境路径适配
current_dir = os.path.dirname(os.path.abspath(__file__))
quant_engine_dir = os.path.dirname(current_dir)
backend_dir = os.path.dirname(quant_engine_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from quant_engine.common import setup_quant_path, setup_logger
from quant_engine.calculators.stock_rps_calculator import StockRPSCalculator
from quant_engine.calculators.sector_rps_calculator import SectorRPSCalculator
from quant_engine.calculators.sector_composite_rps_calculator import SectorCompositeRPSCalculator
from quant_engine.calculators.etf_rps_calculator import ETFRPSCalculator
from quant_engine.calculators.strength_calculator import StrengthCalculator
from quant_engine.calculators.structure_health_calculator import StructureHealthCalculator
from quant_engine.calculators.lifecycle_state_calculator import LifecycleStateCalculator
from quant_engine.calculators.timing_signal_calculator import TimingSignalCalculator
from quant_engine.calculators.risk_plan_calculator import RiskPlanCalculator
from quant_engine.research.research_pool import ResearchPoolManager

# 路径初始化
setup_quant_path()

# Logger配置
logger = setup_logger(__name__)


class FeatureRunner:
    """因子计算运行器"""

    def __init__(self):
        """初始化运行器"""
        self.calculators = {
            'stock': StockRPSCalculator(),
            'sector': SectorRPSCalculator(),
            'sector_composite': SectorCompositeRPSCalculator(),
            'etf': ETFRPSCalculator(),
            'strength': StrengthCalculator(),
            'structure': StructureHealthCalculator(),
            'lifecycle': LifecycleStateCalculator(),
            'timing': TimingSignalCalculator(),
            'risk': RiskPlanCalculator(),
            'research': ResearchPoolManager(),
        }

    def run(
        self,
        calculator_names=None,
        mode='daily',
        init_days: int | None = None,
        target_dates: list[str] | None = None,
        missing_only: bool = False,
    ):
        """
        运行指定的计算器

        Args:
            calculator_names: 计算器名称列表，如 ['stock', 'sector', 'etf']
                             None 表示运行所有计算器
            mode: 运行模式
                  - 'daily': 增量更新（只算最近几天）
                  - 'init': 全量初始化（重算所有历史数据）
            init_days: init 模式下可选的最近自然日窗口；None 表示全历史

        Returns:
            dict: 运行结果统计
        """
        # 确定要运行的计算器
        if calculator_names is None:
            calculator_names = list(self.calculators.keys())

        # 验证计算器名称
        invalid_names = [name for name in calculator_names if name not in self.calculators]
        if invalid_names:
            logger.error(f"❌ 无效的计算器名称: {invalid_names}")
            logger.info(f"   可用的计算器: {list(self.calculators.keys())}")
            return {'success': False, 'error': f'无效的计算器名称: {invalid_names}'}

        # 执行计算
        results = {}
        total_start = time.time()

        logger.info("=" * 80)
        logger.info(f"🚀 开始批量计算量化因子")
        logger.info(f"📋 计算器列表: {calculator_names}")
        logger.info(f"📅 运行模式: {mode}")
        if mode == 'init':
            logger.info(f"📅 初始化范围: {'全历史' if init_days is None else f'最近{init_days}天'}")
        logger.info("=" * 80)

        for name in tqdm(calculator_names, desc="🧮 RPS计算进度", unit="个"):
            calculator = self.calculators[name]
            start_time = time.time()

            try:
                if mode == 'init':
                    result = calculator.run_init(days=init_days)
                elif name in {"stock", "sector", "sector_composite", "etf"}:
                    result = calculator.run_daily(
                        target_dates=target_dates,
                        missing_only=missing_only,
                    )
                elif name in {"strength", "structure", "lifecycle"}:
                    result = calculator.run_daily(
                        target_dates=target_dates,
                        missing_only=missing_only,
                    )
                else:
                    # Daily analytics only need the latest completed target. Their
                    # own loaders still read the historical seed window they require.
                    result = calculator.run_daily(days=1 if target_dates else 3)

                elapsed = time.time() - start_time
                if isinstance(result, dict):
                    results[name] = {
                        'success': result.get('success', True),
                        'elapsed': result.get('elapsed', elapsed),
                        'records': result.get('records', 0),
                        'error': result.get('error'),
                    }
                else:
                    results[name] = {'success': True, 'elapsed': elapsed, 'records': 0}

            except Exception as e:
                elapsed = time.time() - start_time
                results[name] = {'success': False, 'error': str(e), 'elapsed': elapsed}

        # 输出统计
        total_elapsed = time.time() - total_start
        success_count = sum(1 for r in results.values() if r.get('success'))
        total_count = len(results)

        logger.info("\n" + "=" * 80)
        logger.info(f"📊 批量计算完成")
        logger.info(f"   成功: {success_count}/{total_count}")
        logger.info(f"   总耗时: {total_elapsed:.1f}秒")
        logger.info("=" * 80)

        return results


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="EvoAlpha 量化引擎 - 因子计算运行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 运行所有计算器（增量更新）
  python -m quant_engine.runner.feature_runner

  # 只运行个股和板块RPS
  python -m quant_engine.runner.feature_runner --calculators stock sector

  # 运行 P0 计算链路
  python -m quant_engine.runner.feature_runner --calculators strength structure lifecycle timing risk research

  # 全量初始化所有计算器
  python -m quant_engine.runner.feature_runner --mode init

  # 全量初始化个股RPS
  python -m quant_engine.runner.feature_runner --calculators stock --mode init

可用的计算器:
  stock   - 个股RPS
  sector  - 板块RPS
  etf     - ETF RPS
  strength  - 市场/行业强度
  structure - 结构健康评分
  lifecycle - S0-S7生命周期状态机
  timing    - 四类买点信号
  risk      - 风控交易计划
  research  - 研究池每日快照
        """
    )

    parser.add_argument(
        '--calculators', '-c',
        nargs='+',
        choices=['stock', 'sector', 'sector_composite', 'etf', 'strength', 'structure', 'lifecycle', 'timing', 'risk', 'research'],
        help='指定要运行的计算器（默认运行所有）'
    )

    parser.add_argument(
        '--mode', '-m',
        choices=['daily', 'init'],
        default='daily',
        help='运行模式：daily=增量更新（默认），init=全量初始化'
    )

    parser.add_argument(
        '--init-days',
        type=int,
        default=None,
        help='init 模式下仅重算最近N个自然日；默认重算源表全历史'
    )
    parser.add_argument(
        '--target-dates',
        nargs='+',
        help='daily 模式明确待计算交易日，RPS 最多处理最近7个日期',
    )
    parser.add_argument(
        '--missing-only',
        action='store_true',
        help='只计算目标表尚未生成的明确目标日期',
    )

    args = parser.parse_args()

    # 运行
    runner = FeatureRunner()
    results = runner.run(
        calculator_names=args.calculators,
        mode=args.mode,
        init_days=args.init_days,
        target_dates=args.target_dates,
        missing_only=args.missing_only,
    )

    # 返回退出码
    if all(r.get('success') for r in results.values()):
        return 0
    else:
        return 1


if __name__ == "__main__":
    exit(main())
