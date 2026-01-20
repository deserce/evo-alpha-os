"""
量化引擎 - 因子计算运行器
批量运行所有RPS计算器
"""
import sys
import os
import time
import argparse
from datetime import date

# 环境路径适配
current_dir = os.path.dirname(os.path.abspath(__file__))
quant_engine_dir = os.path.dirname(current_dir)
backend_dir = os.path.dirname(quant_engine_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from quant_engine.common import setup_quant_path, setup_logger
from quant_engine.calculators.stock_rps_calculator import StockRPSCalculator
from quant_engine.calculators.sector_rps_calculator import SectorRPSCalculator
from quant_engine.calculators.etf_rps_calculator import ETFRPSCalculator

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
            'etf': ETFRPSCalculator()
        }

    def run(self, calculator_names=None, mode='daily'):
        """
        运行指定的计算器

        Args:
            calculator_names: 计算器名称列表，如 ['stock', 'sector', 'etf']
                             None 表示运行所有计算器
            mode: 运行模式
                  - 'daily': 增量更新（只算最近几天）
                  - 'init': 全量初始化（重算所有历史数据）

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
        logger.info(f"🚀 开始批量计算RPS因子")
        logger.info(f"📋 计算器列表: {calculator_names}")
        logger.info(f"📅 运行模式: {mode}")
        logger.info("=" * 80)

        for name in calculator_names:
            calculator = self.calculators[name]
            start_time = time.time()

            try:
                logger.info(f"\n▶️ [{name.upper()}] 开始计算...")

                if mode == 'init':
                    calculator.run_init()
                else:
                    calculator.run_daily()

                elapsed = time.time() - start_time
                results[name] = {'success': True, 'elapsed': elapsed}
                logger.info(f"✅ [{name.upper()}] 完成！耗时: {elapsed:.1f}秒")

            except Exception as e:
                elapsed = time.time() - start_time
                results[name] = {'success': False, 'error': str(e), 'elapsed': elapsed}
                logger.error(f"❌ [{name.upper()}] 失败: {e}")

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

  # 全量初始化所有计算器
  python -m quant_engine.runner.feature_runner --mode init

  # 全量初始化个股RPS
  python -m quant_engine.runner.feature_runner --calculators stock --mode init

可用的计算器:
  stock   - 个股RPS
  sector  - 板块RPS
  etf     - ETF RPS
        """
    )

    parser.add_argument(
        '--calculators', '-c',
        nargs='+',
        choices=['stock', 'sector', 'etf'],
        help='指定要运行的计算器（默认运行所有）'
    )

    parser.add_argument(
        '--mode', '-m',
        choices=['daily', 'init'],
        default='daily',
        help='运行模式：daily=增量更新（默认），init=全量初始化'
    )

    args = parser.parse_args()

    # 运行
    runner = FeatureRunner()
    results = runner.run(calculator_names=args.calculators, mode=args.mode)

    # 返回退出码
    if all(r.get('success') for r in results.values()):
        return 0
    else:
        return 1


if __name__ == "__main__":
    exit(main())
