"""
EvoAlpha OS - 并行采集管理器
支持多个采集器并行执行，大幅提升采集性能
"""

import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Callable, Dict, Any

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ParallelCollector:
    """并行采集管理器"""

    def __init__(self, max_workers: int = 3):
        """
        初始化并行采集管理器

        Args:
            max_workers: 最大并行数，建议3-5（太多可能导致API限流）
        """
        self.max_workers = max_workers

    def run_collectors(
        self,
        collector_tasks: List[Tuple[str, Callable]]
    ) -> Dict[str, Any]:
        """
        并行执行多个采集器

        Args:
            collector_tasks: 采集器任务列表
                [(collector_name, collector_func), ...]

        Returns:
            Dict[str, Any]: 执行结果
                {
                    'collector_name': {
                        'success': bool,
                        'elapsed_time': float,
                        'error': str or None
                    }
                }
        """
        results = {}
        start_time = time.time()

        logger.info(f"🚀 启动并行采集（并行数={self.max_workers}）")
        logger.info(f"📋 待执行任务数: {len(collector_tasks)}")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务
            future_to_task = {
                executor.submit(self._run_collector, name, func): (name, func)
                for name, func in collector_tasks
            }

            # 收集结果
            completed = 0
            for future in as_completed(future_to_task):
                name, func = future_to_task[future]
                completed += 1

                try:
                    result = future.result()
                    results[name] = result

                    status = "✅" if result['success'] else "❌"
                    elapsed = result['elapsed_time']
                    logger.info(f"{status} [{completed}/{len(collector_tasks)}] {name} 完成 (耗时: {elapsed:.1f}秒)")

                except Exception as e:
                    logger.error(f"❌ [{completed}/{len(collector_tasks)}] {name} 异常: {e}")
                    results[name] = {
                        'success': False,
                        'elapsed_time': 0,
                        'error': str(e)
                    }

        total_elapsed = time.time() - start_time

        # 统计结果
        success_count = sum(1 for r in results.values() if r['success'])
        fail_count = len(results) - success_count

        logger.info(f"\n{'='*70}")
        logger.info(f"✅ 并行采集完成！")
        logger.info(f"📊 成功: {success_count}, 失败: {fail_count}, 总计: {len(results)}")
        logger.info(f"⏰ 总耗时: {total_elapsed:.1f}秒 ({total_elapsed/60:.1f}分钟)")
        logger.info(f"{'='*70}\n")

        return results

    def _run_collector(self, name: str, func: Callable) -> Dict[str, Any]:
        """
        执行单个采集器

        Args:
            name: 采集器名称
            func: 采集器函数

        Returns:
            Dict[str, Any]: 执行结果
        """
        start_time = time.time()
        error = None
        success = False

        try:
            func()
            success = True
        except Exception as e:
            error = str(e)
            success = False

        elapsed = time.time() - start_time

        return {
            'success': success,
            'elapsed_time': elapsed,
            'error': error
        }


def run_parallel_collectors(
    collectors: List[Tuple[str, any]],
    max_workers: int = 3
) -> Dict[str, Any]:
    """
    便捷函数：并行执行采集器列表

    Args:
        collectors: 采集器实例列表
            [(display_name, collector_instance), ...]
        max_workers: 最大并行数

    Returns:
        Dict[str, Any]: 执行结果

    Example:
        >>> from data_job.collectors import (
        ...     SectorKlineCollector, ETFKlineCollector,
        ...     IndexDataCollector, LimitBoardsCollector
        ... )
        >>>
        >>> collectors = [
        ...     ("板块K线", SectorKlineCollector()),
        ...     ("ETF K线", ETFKlineCollector()),
        ...     ("指数数据", IndexDataCollector()),
        ...     ("连板数据", LimitBoardsCollector()),
        ... ]
        >>>
        >>> results = run_parallel_collectors(collectors, max_workers=3)
    """
    manager = ParallelCollector(max_workers=max_workers)

    # 将采集器实例转换为可调用函数
    collector_tasks = [
        (name, collector.run)
        for name, collector in collectors
    ]

    return manager.run_collectors(collector_tasks)


if __name__ == "__main__":
    # 测试代码
    from data_job.collectors import (
        SectorKlineCollector,
        ETFKlineCollector,
        IndexDataCollector,
        LimitBoardsCollector
    )

    # 定义要并行运行的采集器
    collectors = [
        ("板块K线", SectorKlineCollector()),
        ("ETF K线", ETFKlineCollector()),
        ("指数数据", IndexDataCollector()),
        ("连板数据", LimitBoardsCollector()),
    ]

    # 并行执行
    results = run_parallel_collectors(collectors, max_workers=3)

    # 打印结果
    print("\n详细结果:")
    for name, result in results.items():
        status = "成功" if result['success'] else f"失败: {result['error']}"
        print(f"  {name}: {status} (耗时: {result['elapsed_time']:.1f}秒)")
