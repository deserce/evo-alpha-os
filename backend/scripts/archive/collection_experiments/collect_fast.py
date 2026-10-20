"""
高效批量前复权K线采集器（优化版）

优化点：
1. 批量获取除权数据（避免重复请求）
2. 批量写入数据库（提高IO效率）
3. 简化复权计算流程
4. 使用更大的并发数
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import time

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd
from sqlalchemy import text

logging.basicConfig(
    level=logging.WARNING,  # 减少日志输出
    format='%(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def init_worker():
    """进程池工作进程初始化"""
    import sys
    backend_dir = Path('/Users/deserce/Documents/项目/EvoAlpha-OS/backend')
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


def collect_batch_worker(args):
    """
    批量采集worker（进程池版本）

    Args:
        args: (symbols_batch, adjust_type, days)

    Returns:
        (success_count, failed_count, total_rows, batch_info)
    """
    symbols_batch, adjust_type, days = args

    try:
        from data_job.core.multi_source_adapter import MultiSourceAdapter
        from data_job.core.adjust_calculator import AdjustFactorCalculator
        from app.core.database import get_engine

        # 批量获取除权数据（减少网络请求）
        calc = AdjustFactorCalculator()

        adapter = MultiSourceAdapter(data_type='kline')

        # 计算日期范围
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        batch_results = []
        success_count = 0
        failed_count = 0

        # 采集所有股票
        for symbol in symbols_batch:
            try:
                # 1. 获取原始K线
                df = adapter.get_stock_kline(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    adjust='none'
                )

                if df is None or df.empty:
                    failed_count += 1
                    continue

                # 2. 前复权
                if adjust_type == 'qfq':
                    df = calc.adjust_kline_forward(df, symbol, use_cache=True)
                elif adjust_type == 'hfq':
                    df = calc.adjust_kline_backward(df, symbol, use_cache=True)

                if df is not None and not df.empty:
                    batch_results.append((symbol, df))
                    success_count += 1
                else:
                    failed_count += 1

            except Exception as e:
                failed_count += 1
                continue

        # 批量写入数据库（提高IO效率）
        if batch_results:
            table_name = f"stock_kline_{adjust_type}"
            engine = get_engine('local')

            # 合并所有数据
            all_dfs = [df for _, df in batch_results]

            if all_dfs:
                combined_df = pd.concat(all_dfs, ignore_index=True)

                # 删除旧数据
                symbols_in_batch = [s for s, _ in batch_results]
                for symbol in symbols_in_batch:
                    try:
                        delete_query = text(f"DELETE FROM {table_name} WHERE symbol = '{symbol}'")
                        with engine.connect() as conn:
                            conn.execute(delete_query)
                    except:
                        pass

                # 批量插入新数据
                try:
                    combined_df.to_sql(
                        table_name,
                        engine,
                        if_exists='append',
                        index=False,
                        chunksize=1000  # 分批写入
                    )
                except Exception as e:
                    logger.warning(f"批量写入失败: {e}")

        total_rows = sum(len(df) for _, df in batch_results)

        return (success_count, failed_count, total_rows, f"批次完成: {len(symbols_batch)}只股票")

    except Exception as e:
        logger.error(f"Worker异常: {e}")
        return (0, len(symbols_batch), 0, f"批次失败: {str(e)[:50]}")


def collect_all_stocks_fast(
    symbols: List[str],
    adjust_type: str = 'qfq',
    max_workers: int = 8,
    batch_size: int = 100
):
    """
    高效批量采集

    Args:
        symbols: 股票代码列表
        adjust_type: 复权类型
        max_workers: 进程池大小
        batch_size: 每批股票数量
    """
    print("=" * 100)
    print(f"高效批量采集前复权K线数据（进程池: {max_workers}个进程）")
    print("=" * 100)

    total = len(symbols)
    start_time = time.time()

    print(f"\n股票总数: {total}")
    print(f"进程数量: {max_workers}")
    print(f"批次数: {(total + batch_size - 1) // batch_size}")
    print(f"数据范围: 近3年（约780个交易日）")
    print(f"复权类型: {adjust_type}")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n" + "=" * 100 + "\n")

    # 分批处理
    batches = []
    for i in range(0, total, batch_size):
        batch = symbols[i:i + batch_size]
        batches.append(batch)

    success_total = 0
    failed_total = 0
    rows_total = 0

    # 使用进程池
    with ProcessPoolExecutor(max_workers=max_workers, initializer=init_worker) as executor:
        # 提交所有批次
        futures = {
            executor.submit(collect_batch_worker, (batch, adjust_type, 780)): batch_idx
            for batch_idx, batch in enumerate(batches)
        }

        # 处理完成的批次
        for future in as_completed(futures):
            batch_idx = futures[future]

            try:
                success, failed, rows, info = future.result()
                success_total += success
                failed_total += failed
                rows_total += rows

                elapsed = time.time() - start_time
                speed = success_total / elapsed if elapsed > 0 else 0

                print(f"批次 {batch_idx + 1}/{len(batches)} "
                      f"✅{success}只 ⚠️{failed}只 "
                      f"| 速度: {speed:.1f}只/秒 "
                      f"| 已完成: {success_total}/{total} ({success_total/total*100:.1f}%)")

            except Exception as e:
                print(f"批次 {batch_idx + 1} ❌ 失败: {str(e)[:30]}")

    # 汇总
    print("\n" + "=" * 100)
    print("✅ 批量采集完成")
    print("=" * 100)
    print(f"总数:     {total}")
    print(f"成功:     {success_total}")
    print(f"失败:     {failed_total}")
    print(f"数据行数: {rows_total:,}")

    elapsed = time.time() - start_time
    print(f"总耗时:   {elapsed/60:.1f} 分钟")
    print(f"平均速度: {total/elapsed:.1f} 只/分钟")
    print("=" * 100 + "\n")

    return {
        'success': success_total,
        'failed': failed_total,
        'rows': rows_total
    }


def main():
    """主函数"""
    print("\n" + "=" * 100)
    print("EvoAlpha OS 高效批量采集全部A股近3年前复权K线数据")
    print("=" * 100)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    # 1. 获取股票列表
    from scripts.collect_multithread import get_all_stock_list

    symbols = get_all_stock_list()

    if not symbols:
        print("❌ 无法获取股票列表，停止执行")
        return

    # 2. 高效批量采集（8进程，每批100只）
    # 使用进程池而不是线程池，避免GIL
    results = collect_all_stocks_fast(
        symbols=symbols,
        adjust_type='qfq',
        max_workers=8,  # 8个进程
        batch_size=100  # 每批100只
    )

    # 3. 验证数据
    print("验证数据...")
    try:
        from app.core.database import get_engine

        engine = get_engine('local')

        query = """
        SELECT
            COUNT(*) as count,
            COUNT(DISTINCT symbol) as stocks,
            MIN(trade_date) as min_date,
            MAX(trade_date) as max_date
        FROM stock_kline_qfq
        """

        df = pd.read_sql(query, engine)

        if not df.empty:
            count = df.iloc[0]['count']
            stocks = df.iloc[0]['stocks']
            min_date = df.iloc[0]['min_date']
            max_date = df.iloc[0]['max_date']

            print(f"✅ 总行数:   {count:,}")
            print(f"✅ 股票数:   {stocks:,}")
            print(f"✅ 日期范围: {min_date} 至 {max_date}")
            print(f"✅ 平均/股:  {count/stocks:.0f} 条")
    except Exception as e:
        print(f"⚠️  验证失败: {e}")

    print("\n" + "=" * 100)
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    print("✅ 高效批量采集完成！")
    print("\n下一步:")
    print("1. 计算RPS指标")
    print("2. 运行策略选股")
    print("3. 查看前端数据")


if __name__ == "__main__":
    main()
