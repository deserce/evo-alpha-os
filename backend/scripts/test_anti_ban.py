"""
AKShare防封策略测试脚本
演示如何使用防封类进行数据采集
"""

import sys
from pathlib import Path
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from data_job.utils.akshare_helper import create_akshare_collector
from datetime import datetime, timedelta

print("=" * 80)
print("AKShare防封策略测试")
print("=" * 80)

# 方式1：使用便捷函数（推荐）
print("\n[方式1] 使用AKShareCollector（推荐）")
print("-" * 80)

# 创建采集器（自动检测系统代理）
collector = create_akshare_collector(mode='balanced')

try:
    # 测试获取单只股票数据
    print("\n测试1: 获取平安银行最近10天数据...")
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')

    data = collector.get_stock_zh_a_daily(
        symbol='000001',
        start_date=start_date,
        end_date=end_date,
        adjust='qfq'
    )

    if data is not None and not data.empty:
        print(f"✅ 成功获取 {len(data)} 条数据")
        print(f"   列名: {list(data.columns)}")
        print(f"   日期范围: {data['日期'].min()} ~ {data['日期'].max()}")
    else:
        print("❌ 无数据")

    # 测试获取股票列表
    print("\n测试2: 获取A股股票列表...")
    stock_list = collector.get_stock_info_a_code_name()

    if stock_list is not None and not stock_list.empty:
        print(f"✅ 成功获取 {len(stock_list)} 只股票信息")
        print(f"   前5只股票:")
        print(stock_list.head(5).to_string())
    else:
        print("❌ 无数据")

    # 打印统计信息
    print("\n采集统计:")
    collector.print_stats()

finally:
    collector.cleanup()

# 方式2：直接使用防封策略类
print("\n\n[方式2] 直接使用AntiBanStrategy")
print("-" * 80)

from data_job.utils.anti_ban import create_anti_ban_strategy

# 创建防封策略
strategy = create_anti_ban_strategy(mode='balanced')

# 使用防封策略调用AKShare
try:
    print("\n测试3: 使用防封策略获取数据...")
    import akshare as ak

    data = strategy.safe_get(
        'stock_zh_a_daily',
        symbol='000002',
        start_date='20240301',
        end_date='20240310',
        adjust='qfq'
    )

    if data is not None and not data.empty:
        print(f"✅ 成功获取 {len(data)} 条数据")
        print(f"   数据样例:")
        print(data.head(2).to_string())

finally:
    strategy.cleanup()

# 方式3：批量处理
print("\n\n[方式3] 批量处理多只股票")
print("-" * 80)

collector = create_akshare_collector(mode='conservative')

try:
    symbols = ['000001', '000002', '600000', '600519']
    results = {}

    print(f"\n批量获取 {len(symbols)} 只股票数据...")

    for symbol in symbols:
        try:
            data = collector.get_stock_zh_a_daily(
                symbol=symbol,
                start_date='20240301',
                end_date='20240310',
                adjust='qfq'
            )

            if data is not None and not data.empty:
                results[symbol] = len(data)
                print(f"  {symbol}: ✅ {len(data)} 条")
            else:
                results[symbol] = 0
                print(f"  {symbol}: ⚠️  无数据")

        except Exception as e:
            results[symbol] = -1
            print(f"  {symbol}: ❌ {e}")

    print(f"\n批量处理完成:")
    print(f"  成功: {sum(1 for v in results.values() if v > 0)}/{len(symbols)}")

finally:
    collector.cleanup()

print("\n" + "=" * 80)
print("测试完成！")
print("=" * 80)
print("\n使用建议:")
print("  1. 数据采集时统一使用 AKShareCollector")
print("  2. 根据需求选择防封模式:")
print("     - conservative: 最保守（慢但安全）")
print("     - balanced: 平衡（推荐）")
print("     - aggressive: 激进（快但风险高）")
print("  3. 批量采集时使用 batch_get() 方法")
print("  4. 定期查看统计信息，监控成功率")
