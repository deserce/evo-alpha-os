"""
采集近3年K线数据（前复权）

采集策略：
1. 使用pytdx/mootdx获取原始数据（稳定）
2. 计算前复权因子
3. 应用前复权
4. 保存到数据库
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List

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


def get_all_stock_list() -> List[str]:
    """获取所有A股股票代码"""
    print("获取A股股票列表...")

    try:
        from data_job.core.mootdx_adapter import MootdxAdapter

        mootdx = MootdxAdapter()
        if mootdx.is_available():
            stock_list = mootdx.get_stock_list()

            if stock_list is not None and not stock_list.empty:
                # 过滤出A股股票
                # 沪市: 600xxx, 601xxx, 603xxx, 605xxx, 688xxx(科创板)
                # 深市: 000xxx, 001xxx, 002xxx, 003xxx, 300xxx(创业板)
                all_codes = stock_list['code'].tolist()

                # 过滤A股代码
                a_stock_codes = []
                for code in all_codes:
                    # 确保是6位数字
                    if not isinstance(code, str) or len(code) != 6:
                        continue

                    # 沪市A股
                    # 600xxx, 601xxx, 603xxx, 605xxx: 沪市主板
                    # 688xxx: 科创板
                    if code.startswith(('600', '601', '603', '605', '688')):
                        a_stock_codes.append(code)
                    # 深市A股
                    # 000xxx, 001xxx: 深市主板
                    # 002xxx, 003xxx: 中小板
                    # 300xxx, 301xxx: 创业板
                    elif code.startswith(('000', '001', '002', '003', '30', '31')):
                        # 排除一些特殊代码（基金等）
                        if not code.startswith('0008'):  # 排除基金
                            a_stock_codes.append(code)

                print(f"✅ 获取到 {len(a_stock_codes)} 只A股股票")
                return a_stock_codes

        print("❌ 无法获取股票列表")
        return []

    except Exception as e:
        print(f"❌ 获取股票列表失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def collect_stock_kline_3years(
    symbols: List[str],
    adjust_type: str = 'qfq',
    batch_size: int = 50
):
    """
    采集近3年K线数据

    Args:
        symbols: 股票代码列表
        adjust_type: 复权类型 ('qfq'-前复权, 'hfq'-后复权, 'none'-不复权)
        batch_size: 批量处理大小
    """
    print("=" * 100)
    print(f"采集近3年K线数据（复权类型: {adjust_type}）")
    print("=" * 100)

    from data_job.collectors.adjustable_kline_collector import AdjustableKlineCollector
    from app.core.database import get_engine

    # 创建采集器
    collector = AdjustableKlineCollector(
        adjust_type=adjust_type,
        request_delay=0.3  # 减少延迟提高速度
    )

    # 数据库表名
    table_name = f"stock_kline_{adjust_type}"

    total = len(symbols)
    success_count = 0
    fail_count = 0
    skip_count = 0

    # 按批次处理
    batch_count = (total + batch_size - 1) // batch_size

    print(f"\n股票总数: {total}")
    print(f"批次大小: {batch_size}")
    print(f"批次数: {batch_count}")
    print(f"数据范围: 近3年（约750个交易日）")
    print("\n" + "=" * 100 + "\n")

    for batch_idx in range(batch_count):
        start_idx = batch_idx * batch_size
        end_idx = min((batch_idx + 1) * batch_size, total)
        batch_symbols = symbols[start_idx:end_idx]

        print(f"📦 批次 {batch_idx + 1}/{batch_count} (股票 {start_idx + 1}-{end_idx})")

        for idx, symbol in enumerate(batch_symbols):
            try:
                # 检查数据库中是否已有该股票的数据
                engine = get_engine('local')

                check_query = text(f"""
                SELECT COUNT(*) as count FROM {table_name}
                WHERE symbol = '{symbol}'
                AND trade_date >= date('now', '-3 years')
                """)

                with engine.connect() as conn:
                    result = conn.execute(check_query).fetchone()
                    existing_count = result[0] if result else 0

                # 如果已有足够的数据（>700条），跳过
                if existing_count > 700:
                    print(f"  [{idx + 1}/{len(batch_symbols)}] {symbol} ⏭️  已有 {existing_count} 条，跳过")
                    skip_count += 1
                    continue

                # 采集数据
                print(f"  [{idx + 1}/{len(batch_symbols)}] {symbol} 🔄 采集中...")

                df = collector.collect_single_stock(
                    symbol=symbol,
                    days=780  # 3年约780个交易日（含周末和节假日）
                )

                if df is not None and not df.empty:
                    # 删除该股票的旧数据
                    delete_query = text(f"DELETE FROM {table_name} WHERE symbol = '{symbol}'")

                    # 保存新数据
                    df.to_sql(
                        table_name,
                        engine,
                        if_exists='append',
                        index=False
                    )

                    print(f"                    ✅ 成功 {len(df)} 条")
                    success_count += 1
                else:
                    print(f"                    ⚠️  无数据")
                    fail_count += 1

            except Exception as e:
                error_msg = str(e)
                if "UNIQUE constraint" in error_msg:
                    # 数据已存在，不算失败
                    print(f"                    ⏭️  数据已存在")
                    skip_count += 1
                else:
                    print(f"                    ❌ 失败: {error_msg[:50]}")
                    fail_count += 1

        # 批次间休息，避免请求过快
        if batch_idx < batch_count - 1:
            import time
            print(f"\n😴 批次完成，休息30秒...\n")
            time.sleep(30)

    # 汇总
    print("\n" + "=" * 100)
    print("✅ 采集完成")
    print("=" * 100)
    print(f"总数:     {total}")
    print(f"成功:     {success_count}")
    print(f"失败:     {fail_count}")
    print(f"跳过:     {skip_count}")
    print(f"成功率:   {success_count / total * 100:.1f}%")
    print("=" * 100 + "\n")


def verify_collection():
    """验证采集的数据"""
    print("=" * 100)
    print("验证采集的数据")
    print("=" * 100)

    from app.core.database import get_engine

    engine = get_engine('local')

    try:
        # 检查各表数据量
        tables = {
            'stock_kline_qfq': '股票前复权K线',
            'stock_kline_hfq': '股票后复权K线',
            'stock_kline_none': '股票不复权K线'
        }

        print("\n数据统计:")
        print("-" * 100)

        for table, name in tables.items():
            try:
                query = f"SELECT COUNT(*) as count FROM {table}"
                df = pd.read_sql(query, engine)
                count = df.iloc[0]['count']

                if count > 0:
                    # 获取日期范围和股票数量
                    date_query = f"""
                    SELECT
                        MIN(trade_date) as min_date,
                        MAX(trade_date) as max_date,
                        COUNT(DISTINCT symbol) as symbol_count
                    FROM {table}
                    """
                    date_df = pd.read_sql(date_query, engine)
                    min_date = date_df.iloc[0]['min_date']
                    max_date = date_df.iloc[0]['max_date']
                    symbol_count = date_df.iloc[0]['symbol_count']

                    print(f"✅ {name:<25} {count:>12,} 行   "
                          f"股票: {symbol_count:>5,}   "
                          f"日期: {min_date} ~ {max_date}")
                else:
                    print(f"⚠️  {name:<25} 无数据")

            except Exception as e:
                print(f"❌ {name:<25} 错误: {str(e)[:40]}")

        print("-" * 100)

        # 按股票统计
        print("\n股票数据量统计（前复权）:")
        print("-" * 100)

        stats_query = """
        SELECT
            symbol,
            COUNT(*) as count,
            MIN(trade_date) as min_date,
            MAX(trade_date) as max_date
        FROM stock_kline_qfq
        GROUP BY symbol
        ORDER BY count DESC
        LIMIT 20
        """

        stats_df = pd.read_sql(stats_query, engine)

        if not stats_df.empty:
            print(f"{'股票代码':<10} {'数据条数':<10} {'最早日期':<12} {'最新日期':<12}")
            print("-" * 100)
            for _, row in stats_df.iterrows():
                print(f"{row['symbol']:<10} {row['count']:<10,} {row['min_date']!s:<12} {row['max_date']!s:<12}")
        else:
            print("无数据")

        print("\n" + "=" * 100 + "\n")

    except Exception as e:
        print(f"❌ 验证失败: {e}\n")


def main():
    """主函数"""
    print("\n" + "=" * 100)
    print("EvoAlpha OS 采集近3年K线数据")
    print("=" * 100)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    # 1. 获取股票列表
    symbols = get_all_stock_list()

    if not symbols:
        print("❌ 无法获取股票列表，停止执行")
        return

    # 询问用户采集范围
    print("\n请选择采集范围:")
    print("1. 测试模式（前100只股票）")
    print("2. 部分采集（前1000只股票）")
    print("3. 全部采集（所有A股）")

    choice = input("\n请输入选项 (1/2/3): ").strip()

    if choice == '1':
        symbols = symbols[:100]
        print(f"\n⚠️  测试模式：采集前 {len(symbols)} 只股票\n")
    elif choice == '2':
        symbols = symbols[:1000]
        print(f"\n⚠️  部分采集模式：采集前 {len(symbols)} 只股票\n")
    else:
        print(f"\n🚀 全部采集模式：采集全部 {len(symbols)} 只股票\n")
        print("预计耗时: 约2-4小时（取决于网络速度）")
        confirm = input("确认继续? (y/n): ").strip().lower()
        if confirm != 'y':
            print("已取消")
            return

    # 2. 采集前复权数据
    collect_stock_kline_3years(symbols, adjust_type='qfq')

    # 3. 验证数据
    verify_collection()

    print("=" * 100)
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    print("✅ 采集完成！")
    print("\n下一步:")
    print("1. 可以计算RPS指标")
    print("2. 可以运行策略选股")
    print("3. 可以查看前端数据")


if __name__ == "__main__":
    main()
