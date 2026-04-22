"""
Windows虚拟机中的GMX数据采集脚本
在Windows虚拟机中运行此脚本，调用本地掘金终端采集数据
"""

import os
import sys
from datetime import datetime, timedelta
import pandas as pd

# 添加路径（如果在虚拟机中运行）
# 如果虚拟机共享了Mac的backend文件夹，取消下面的注释
# shared_path = r"\\Mac\Home\Desktop\EvoAlpha-OS\backend"
# if shared_path not in sys.path:
#     sys.path.insert(0, shared_path)

print("=" * 80)
print("GMX数据采集脚本 (Windows虚拟机版)")
print("=" * 80)
print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Python版本: {sys.version}")
print()

# 设置Token
token = '7e139dea597ba366a657a34d266c7c08408f4617'
print(f"Token: {token[:10]}...{token[-4:]}")

# 导入GMX SDK
try:
    from gm.api import set_token, get_trading_dates, history
    print("✅ GMX SDK导入成功")
except ImportError as e:
    print(f"❌ GMX SDK未安装: {e}")
    print("\n请在Windows虚拟机中安装GMX SDK:")
    print("  pip install gm")
    sys.exit(1)

# 设置Token
print("\n正在设置Token...")
try:
    set_token(token)
    print("✅ Token设置成功")
except Exception as e:
    print(f"❌ Token设置失败: {e}")
    sys.exit(1)

# 测试连接到掘金终端
print("\n正在测试连接到掘金终端...")
print("请确保:")
print("  1. 掘金终端正在运行")
print("  2. 已登录账号")
print("  3. Token有效且未过期")

try:
    dates = get_trading_dates(
        exchange='SHSE',
        start_date='2024-01-01',
        end_date=datetime.now().strftime('%Y-%m-%d')
    )
    print(f"✅ 成功连接到掘金终端！")
    print(f"   获取交易日: {len(dates)} 个")
    print(f"   最后交易日: {dates[-1]}")

except Exception as e:
    print(f"❌ 连接失败: {e}")
    print("\n可能的原因:")
    print("  1. 掘金终端未运行")
    print("  2. Token无效或已过期")
    print("  3. 终端未登录账号")
    sys.exit(1)

# 获取K线数据
print("\n" + "=" * 80)
print("测试获取K线数据")
print("=" * 80)

test_stocks = [
    ('000001', 'SHSE.000001', '平安银行'),
    ('000002', 'SZSE.000002', '万科A'),
    ('600000', 'SHSE.600000', '浦发银行'),
    ('600519', 'SHSE.600519', '贵州茅台'),
]

end_date = datetime.now().strftime('%Y-%m-%d 15:00:00')
start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d 09:30:00')

all_data = []

for symbol, gmx_symbol, name in test_stocks:
    print(f"\n正在获取 {name} ({symbol})...")

    try:
        data = history(
            symbol=[gmx_symbol],
            frequency='1d',
            start_time=start_date,
            end_time=end_date,
            fields=['eob', 'open', 'high', 'low', 'close', 'volume', 'amount'],
            adjust=1,  # 前复权
            df=True
        )

        if data is not None and not data.empty:
            print(f"  ✅ 获取 {len(data)} 条数据")

            # 数据格式转换
            data_transformed = pd.DataFrame({
                'symbol': symbol,
                'trade_date': pd.to_datetime(data['eob']).dt.strftime('%Y-%m-%d'),
                'open': data['open'].values,
                'close': data['close'].values,
                'high': data['high'].values,
                'low': data['low'].values,
                'volume': data['volume'].values,
                'amount': data['amount'].values,
                'pct_chg': 0.0,  # GMX需要计算
                'turnover_rate': 0.0  # GMX不提供
            })

            all_data.append(data_transformed)

            # 显示前3条
            print(f"  数据样例:")
            for idx, row in data_transformed.head(3).iterrows():
                print(f"    {row['trade_date']}: 收盘={row['close']:.2f}, 成交量={row['volume']:,.0f}")
        else:
            print(f"  ⚠️  无数据")

    except Exception as e:
        print(f"  ❌ 获取失败: {e}")

# 合并所有数据
if all_data:
    df_all = pd.concat(all_data, ignore_index=True)

    print("\n" + "=" * 80)
    print("数据采集完成！")
    print("=" * 80)
    print(f"总计: {len(df_all)} 条数据")
    print(f"涉及股票: {len(test_stocks)} 只")
    print(f"日期范围: {df_all['trade_date'].min()} ~ {df_all['trade_date'].max()}")

    # 数据格式分析
    print("\n数据格式:")
    print(f"  列名: {list(df_all.columns)}")
    print(f"  数据类型:")
    for col in df_all.columns:
        print(f"    {col}: {df_all[col].dtype}")

    # 保存到CSV
    output_file = "gmx_data_export.csv"
    df_all.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n✅ 数据已保存到: {output_file}")
    print(f"   文件位置: {os.path.abspath(output_file)}")

    print("\n" + "=" * 80)
    print("下一步:")
    print("=" * 80)
    print("1. 将此CSV文件复制到Mac宿主机")
    print("2. 在Mac上运行导入脚本将数据写入数据库")
    print("3. 或者配置共享文件夹自动同步")

else:
    print("\n❌ 未获取到任何数据")

print("\n" + "=" * 80)
print("脚本执行完成")
print("=" * 80)
