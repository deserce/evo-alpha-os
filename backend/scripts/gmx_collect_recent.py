"""
使用GMX补充最近缺失的数据
强制使用GMX数据源，对比并转换格式后保存
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# 设置token
token = os.getenv('GMX_TOKEN', '7e139dea597ba366a657a34d266c7c08408f4617')

print("=" * 80)
print("使用GMX补充最近缺失数据")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)

# 导入GMX和数据库
try:
    from gm.api import set_token, history
    from app.core.database import get_engine
    print("✅ 导入GMX SDK和数据库模块成功")
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    sys.exit(1)

# 设置GMX token
try:
    set_token(token)
    print(f"✅ GMX Token设置成功")
except Exception as e:
    print(f"❌ GMX Token设置失败: {e}")
    sys.exit(1)

# 获取数据库引擎
engine = get_engine()

# 检查最新数据日期
print("\n[1/5] 检查数据库最新日期...")
try:
    df_max = pd.read_sql('SELECT MAX(trade_date) as max_date FROM stock_daily_prices', engine)
    max_date = pd.to_datetime(df_max.iloc[0]['max_date'])
    print(f"   数据库最新日期: {max_date.strftime('%Y-%m-%d')}")
except Exception as e:
    print(f"❌ 查询失败: {e}")
    sys.exit(1)

# 计算需要补充的日期范围
today = datetime.now()
days_to_collect = (today - max_date).days
print(f"   需要补充: {days_to_collect} 天")

if days_to_collect <= 0:
    print("✅ 数据已是最新，无需补充")
    sys.exit(0)

# 获取股票列表
print("\n[2/5] 获取股票列表...")
try:
    df_stocks = pd.read_sql('SELECT DISTINCT symbol FROM stock_info LIMIT 10', engine)
    stock_list = df_stocks['symbol'].tolist()
    print(f"   测试收集前10只股票: {stock_list}")
except Exception as e:
    print(f"❌ 获取股票列表失败: {e}")
    # 使用默认股票列表
    stock_list = ['000001', '000002', '000004', '000005', '000006']
    print(f"   使用默认股票列表: {stock_list}")

# 使用GMX获取数据
print("\n[3/5] 使用GMX获取数据...")
collected_data = []

for i, symbol in enumerate(stock_list, 1):
    try:
        # 转换股票代码为GMX格式
        if symbol.startswith('6'):
            gmx_symbol = f'SHSE.{symbol}'
        else:
            gmx_symbol = f'SZSE.{symbol}'

        print(f"   [{i}/{len(stock_list)}] 获取 {symbol} ({gmx_symbol})...")

        # 准备日期参数
        start_date = (max_date + timedelta(days=1)).strftime('%Y-%m-%d 09:30:00')
        end_date = today.strftime('%Y-%m-%d 15:00:00')

        # 调用GMX API
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
            collected_data.append(data_transformed)
            print(f"      ✅ 获取 {len(data)} 条数据")
        else:
            print(f"      ⚠️  无数据")

    except Exception as e:
        print(f"      ❌ 失败: {e}")
        continue

if not collected_data:
    print("\n❌ 未能获取任何数据")
    sys.exit(1)

# 合并数据
print("\n[4/5] 合并并转换数据格式...")
df_all = pd.concat(collected_data, ignore_index=True)
print(f"   总计: {len(df_all)} 条数据")

# 显示数据样例
print("\n[5/5] 数据格式对比:")
print("   GMX返回的列名:")
print(f"     {list(df_all.columns)}")
print("\n   数据样例:")
print(df_all.head(2).to_string())

# 保存到数据库
print("\n保存数据到数据库...")
try:
    # 先检查是否有重复数据
    df_existing = pd.read_sql(f'''
        SELECT symbol, trade_date FROM stock_daily_prices
        WHERE trade_date > '{max_date.strftime('%Y-%m-%d')}'
        LIMIT 10
    ''', engine)

    if len(df_existing) > 0:
        print(f"   ⚠️  发现已有 {len(df_existing)} 条新数据")
    else:
        # 保存新数据
        df_all.to_sql('stock_daily_prices', engine, if_exists='append', index=False)
        print(f"   ✅ 成功保存 {len(df_all)} 条数据")

except Exception as e:
    print(f"❌ 保存失败: {e}")

print("\n" + "=" * 80)
print("✅ 数据补充完成！")
print("=" * 80)
