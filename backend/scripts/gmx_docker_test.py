"""
Docker环境中的GMX测试脚本
测试GMX连接、数据获取、格式对比
"""

import sys
import os
from datetime import datetime, timedelta

# 设置token
token = os.getenv('GMX_TOKEN', '7e139dea597ba366a657a34d266c7c08408f4617')

print("=" * 80)
print("GMX Docker环境测试")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)

try:
    from gm.api import set_token, get_trading_dates, history
    print("✅ GMX SDK导入成功")

    # 设置token
    print(f"\n[1/4] 设置Token: {token[:10]}...")
    set_token(token)
    print("✅ Token设置成功")

    # 测试获取交易日
    print("\n[2/4] 测试获取交易日...")
    dates = get_trading_dates(
        exchange='SHSE',
        start_date='2024-01-01',
        end_date=datetime.now().strftime('%Y-%m-%d')
    )
    if dates and len(dates) > 0:
        print(f"✅ 成功获取 {len(dates)} 个交易日")
        print(f"   最后交易日: {dates[-1]}")
    else:
        print("❌ 获取交易日失败")
        sys.exit(1)

    # 测试获取股票数据
    print("\n[3/4] 测试获取平安银行(000001)最近10天数据...")
    end_date = datetime.now().strftime('%Y-%m-%d 15:00:00')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d 09:30:00')

    data = history(
        symbol=['SHSE.000001'],
        frequency='1d',
        start_time=start_date,
        end_time=end_date,
        fields=['eob', 'open', 'high', 'low', 'close', 'volume', 'amount'],
        adjust=1,  # 前复权
        df=True
    )

    if data is not None and not data.empty:
        print(f"✅ 成功获取 {len(data)} 行数据")
        print(f"\n[4/4] 数据格式分析:")
        print(f"   列名: {list(data.columns)}")
        print(f"\n   数据类型:")
        for col in data.columns:
            print(f"     {col}: {data[col].dtype}")
        print(f"\n   前3行数据:")
        print(data[['eob', 'open', 'high', 'low', 'close', 'volume']].head(3).to_string())
        print("\n" + "=" * 80)
        print("✅ GMX测试完成！")
        print("=" * 80)
    else:
        print("❌ 获取数据失败")
        sys.exit(1)

except ImportError as e:
    print(f"❌ GMX SDK未安装: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
