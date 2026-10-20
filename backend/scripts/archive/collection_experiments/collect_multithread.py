"""
多线程前复权K线采集器

优化方案：
1. ThreadPoolExecutor多线程采集（并发数10-15）
2. 批量获取除权数据（减少网络请求）
3. 每只股票独立处理：获取原始数据 + 计算前复权
4. 批量写入数据库（提高IO效率）

预计耗时：10-15分钟完成5322只股票
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import time

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


# 全局锁，用于数据库写入
db_lock = Lock()
progress_lock = Lock()

# 进度统计
progress_stats = {
    'total': 0,
    'success': 0,
    'failed': 0,
    'skipped': 0,
    'start_time': None
}


def get_all_stock_list() -> List[str]:
    """获取所有A股股票代码"""
    print("获取A股股票列表...")

    try:
        from data_job.core.mootdx_adapter import MootdxAdapter

        mootdx = MootdxAdapter()
        if mootdx.is_available():
            stock_list = mootdx.get_stock_list()

            if stock_list is not None and not stock_list.empty:
                all_codes = stock_list['code'].tolist()

                # 过滤A股代码
                a_stock_codes = []
                for code in all_codes:
                    if not isinstance(code, str) or len(code) != 6:
                        continue

                    # 沪市A股
                    if code.startswith(('600', '601', '603', '605', '688')):
                        a_stock_codes.append(code)
                    # 深市A股
                    elif code.startswith(('000', '001', '002', '003', '30', '31')):
                        if not code.startswith('0008'):  # 排除基金
                            a_stock_codes.append(code)

                print(f"✅ 获取到 {len(a_stock_codes)} 只A股股票")
                return a_stock_codes

        print("❌ 无法获取股票列表")
        return []

    except Exception as e:
        print(f"❌ 获取股票列表失败: {e}")
        return []


def collect_single_stock_mt(symbol: str, adjust_type: str = 'qfq', days: int = 780):
    """
    多线程版本：采集单只股票K线数据

    Args:
        symbol: 股票代码
        adjust_type: 复权类型
        days: 采集天数

    Returns:
        (symbol, success, rows_count, error_message)
    """
    try:
        from data_job.core.multi_source_adapter import MultiSourceAdapter
        from data_job.core.adjust_calculator import AdjustFactorCalculator
        from app.core.database import get_engine

        # 1. 获取原始K线数据
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        adapter = MultiSourceAdapter(data_type='kline')
        df = adapter.get_stock_kline(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust='none'
        )

        if df is None or df.empty:
            return (symbol, False, 0, "无数据")

        # 2. 应用前复权
        if adjust_type == 'qfq':
            calc = AdjustFactorCalculator()
            df = calc.adjust_kline_forward(df, symbol, use_cache=True)
        elif adjust_type == 'hfq':
            calc = AdjustFactorCalculator()
            df = calc.adjust_kline_backward(df, symbol, use_cache=True)

        if df is None or df.empty:
            return (symbol, False, 0, "复权失败")

        # 3. 保存到数据库（使用锁）
        table_name = f"stock_kline_{adjust_type}"

        with db_lock:
            try:
                engine = get_engine('local')

                # 先删除旧数据
                delete_query = text(f"DELETE FROM {table_name} WHERE symbol = '{symbol}'")
                with engine.connect() as conn:
                    conn.execute(delete_query)
                    conn.commit()

                # 插入新数据
                df.to_sql(
                    table_name,
                    engine,
                    if_exists='append',
                    index=False
                )

            except Exception as e:
                error_msg = str(e)
                if "UNIQUE constraint" in error_msg:
                    # 数据已存在
                    return (symbol, True, len(df), "已存在")
                else:
                    return (symbol, False, len(df), error_msg)

        # 更新进度
        with progress_lock:
            progress_stats['success'] += 1

            if progress_stats['success'] % 100 == 0:
                elapsed = time.time() - progress_stats['start_time']
                speed = progress_stats['success'] / elapsed if elapsed > 0 else 0
                remaining = progress_stats['total'] - progress_stats['success']
                eta = remaining / speed if speed > 0 else 0

                logger.info(f"进度: {progress_stats['success']}/{progress_stats['total']} "
                           f"({progress_stats['success']/progress_stats['total']*100:.1f}%) "
                           f"| 速度: {speed:.1f}只/秒 "
                           f"| 剩余: {eta/60:.1f}分钟")

        return (symbol, True, len(df), "")

    except Exception as e:
        with progress_lock:
            progress_stats['failed'] += 1
        return (symbol, False, 0, str(e)[:50])


def collect_all_stocks_multithread(
    symbols: List[str],
    adjust_type: str = 'qfq',
    max_workers: int = 12,
    batch_size: int = 100
):
    """
    多线程采集所有股票

    Args:
        symbols: 股票代码列表
        adjust_type: 复权类型
        max_workers: 最大并发数
        batch_size: 批次大小（用于分批处理）
    """
    print("=" * 100)
    print(f"多线程采集前复权K线数据（并发数: {max_workers}）")
    print("=" * 100)

    progress_stats['total'] = len(symbols)
    progress_stats['start_time'] = time.time()

    print(f"\n股票总数: {len(symbols)}")
    print(f"并发线程: {max_workers}")
    print(f"数据范围: 近3年（约780个交易日）")
    print(f"复权类型: {adjust_type}")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n" + "=" * 100 + "\n")

    results = {
        'success': [],
        'failed': [],
        'skipped': []
    }

    # 使用线程池
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_symbol = {
            executor.submit(collect_single_stock_mt, symbol, adjust_type): symbol
            for symbol in symbols
        }

        # 处理完成的任务
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]

            try:
                result = future.result()
                success, rows, error = result[1], result[2], result[3]

                if success:
                    if error == "已存在":
                        results['skipped'].append(symbol)
                        print(f"  {symbol} ⏭️  已存在 ({rows}条)")
                    else:
                        results['success'].append(symbol)
                        print(f"  {symbol} ✅ ({rows}条)")
                else:
                    results['failed'].append(symbol)
                    print(f"  {symbol} ❌ {error}")

            except Exception as e:
                results['failed'].append(symbol)
                print(f"  {symbol} ❌ 异常: {str(e)[:30]}")

    # 汇总结果
    print("\n" + "=" * 100)
    print("✅ 多线程采集完成")
    print("=" * 100)
    print(f"总数:     {len(symbols)}")
    print(f"成功:     {len(results['success'])}")
    print(f"失败:     {len(results['failed'])}")
    print(f"跳过:     {len(results['skipped'])}")

    elapsed = time.time() - progress_stats['start_time']
    print(f"耗时:     {elapsed/60:.1f} 分钟")
    print(f"速度:     {len(symbols)/elapsed:.1f} 只/分钟")
    print("=" * 100 + "\n")

    return results


def verify_collection():
    """验证采集的数据"""
    print("=" * 100)
    print("验证采集的数据")
    print("=" * 100)

    try:
        from app.core.database import get_engine

        engine = get_engine('local')

        # 检查前复权数据
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

            print(f"\n✅ stock_kline_qfq (前复权)")
            print(f"   总行数:   {count:,}")
            print(f"   股票数:   {stocks:,}")
            print(f"   日期范围: {min_date} 至 {max_date}")
            print(f"   平均/股:  {count/stocks:.0f} 条")

        # 显示示例数据
        print("\n示例数据（贵州茅台 最新5天）:")
        print("-" * 100)

        sample_query = """
        SELECT trade_date, open, high, low, close, volume
        FROM stock_kline_qfq
        WHERE symbol = '600519'
        ORDER BY trade_date DESC
        LIMIT 5
        """

        sample_df = pd.read_sql(sample_query, engine)
        if not sample_df.empty:
            print(sample_df.to_string(index=False))

        print("\n" + "=" * 100 + "\n")

    except Exception as e:
        print(f"❌ 验证失败: {e}\n")


def main():
    """主函数"""
    print("\n" + "=" * 100)
    print("EvoAlpha OS 多线程采集全部A股近3年前复权K线数据")
    print("=" * 100)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    # 1. 获取股票列表
    symbols = get_all_stock_list()

    if not symbols:
        print("❌ 无法获取股票列表，停止执行")
        return

    # 2. 多线程采集（12并发）
    # 如果想更快，可以增加到20，但可能触发限流
    results = collect_all_stocks_multithread(
        symbols=symbols,
        adjust_type='qfq',
        max_workers=12,  # 12个并发线程
        batch_size=100
    )

    # 3. 验证数据
    verify_collection()

    print("=" * 100)
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    print("✅ 多线程采集完成！")
    print("\n下一步:")
    print("1. 计算RPS指标")
    print("2. 运行策略选股")
    print("3. 查看前端数据")


if __name__ == "__main__":
    main()
