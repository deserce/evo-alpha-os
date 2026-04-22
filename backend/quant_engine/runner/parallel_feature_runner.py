"""
量化引擎 - 并行因子计算运行器
支持多个RPS计算器并行执行，大幅提升性能
"""
import sys
import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

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


class ParallelFeatureRunner:
    """并行因子计算运行器"""

    def __init__(self):
        """初始化运行器"""
        self.calculators = {
            'stock': StockRPSCalculator(),
            'sector': SectorRPSCalculator(),
            'etf': ETFRPSCalculator()
        }

    def run(self, calculator_names=None, mode='daily', max_workers=3):
        """
        并行运行指定的计算器

        Args:
            calculator_names: 计算器名称列表，如 ['stock', 'sector', 'etf']
                             None 表示运行所有计算器
            mode: 运行模式
                  - 'daily': 增量更新（只算最近几天）
                  - 'init': 全量初始化（重算所有历史数据）
            max_workers: 最大并行数（默认3）

        Returns:
            dict: 运行结果统计
                {
                    'calculator_name': {
                        'success': bool,
                        'elapsed_time': float,
                        'error': str or None,
                        'records': int
                    }
                }
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

        # 执行并行计算
        results = {}
        total_start = time.time()

        logger.info("=" * 80)
        logger.info(f"🚀 开始并行批量计算RPS因子（并行数={max_workers}）")
        logger.info(f"📋 计算器列表: {calculator_names}")
        logger.info(f"📅 运行模式: {mode}")
        logger.info("=" * 80)

        def run_single_calculator(name):
            """运行单个计算器"""
            calculator = self.calculators[name]
            start_time = time.time()
            error = None
            success = False
            records = 0

            try:
                logger.info(f"▶️  开始计算: {name} RPS...")

                if mode == 'daily':
                    result = calculator.run_daily()
                elif mode == 'init':
                    result = calculator.run_init()
                else:
                    raise ValueError(f"未知的运行模式: {mode}")

                success = result.get('success', False)
                error = result.get('error')
                records = result.get('records', 0)

            except Exception as e:
                error = str(e)
                success = False
                logger.error(f"❌ {name} RPS计算失败: {e}")

            elapsed = time.time() - start_time

            return {
                'name': name,
                'success': success,
                'elapsed_time': elapsed,
                'error': error,
                'records': records
            }

        # 并行执行
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_name = {
                executor.submit(run_single_calculator, name): name
                for name in calculator_names
            }

            # 收集结果
            completed = 0
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                completed += 1

                try:
                    result = future.result()
                    results[result['name']] = result

                    status = "✅" if result['success'] else "❌"
                    elapsed = result['elapsed_time']
                    records = result['records']

                    logger.info(f"{status} [{completed}/{len(calculator_names)}] "
                              f"{result['name']} RPS完成 (耗时: {elapsed:.1f}秒, 记录数: {records})")

                except Exception as e:
                    logger.error(f"❌ [{completed}/{len(calculator_names)}] {name} 异常: {e}")
                    results[name] = {
                        'success': False,
                        'elapsed_time': 0,
                        'error': str(e),
                        'records': 0
                    }

        total_elapsed = time.time() - total_start

        # 统计结果
        success_count = sum(1 for r in results.values() if r['success'])
        total_records = sum(r.get('records', 0) for r in results.values())

        logger.info("\n" + "=" * 80)
        logger.info(f"✅ 并行RPS计算完成！")
        logger.info(f"📊 成功: {success_count}/{len(results)}")
        logger.info(f"📈 总记录数: {total_records}")
        logger.info(f"⏰ 总耗时: {total_elapsed:.1f}秒 ({total_elapsed/60:.1f}分钟)")
        logger.info("=" * 80)

        return results


if __name__ == "__main__":
    # 测试代码
    runner = ParallelFeatureRunner()
    results = runner.run(calculator_names=['stock', 'sector', 'etf'], mode='daily', max_workers=3)

    print("\n详细结果:")
    for name, result in results.items():
        status = "成功" if result['success'] else f"失败: {result['error']}"
        print(f"  {name}: {status} (耗时: {result['elapsed_time']:.1f}秒, 记录数: {result['records']})")
