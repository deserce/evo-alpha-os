"""
GMX简单测试 - 测试网络连接和Token有效性
"""

import sys
import os
from datetime import datetime, timedelta

token = os.getenv('GMX_TOKEN', '7e139dea597ba366a657a34d266c7c08408f4617')

print("=" * 80)
print("GMX SDK简单测试")
print("=" * 80)

print(f"\nToken: {token[:10]}...{token[-4:]}")

try:
    from gm.api import set_token

    print("\n[1/3] 导入GMX SDK: ✅")

    # 设置token
    print("\n[2/3] 设置Token...")
    set_token(token)
    print("✅ Token设置成功")

    # 尝试获取交易日（这可能需要网络连接到掘金服务器）
    print("\n[3/3] 测试网络连接...")
    print("正在尝试连接掘金服务器...")

    from gm.api import get_trading_dates

    try:
        dates = get_trading_dates(
            exchange='SHSE',
            start_date='2024-01-01',
            end_date=datetime.now().strftime('%Y-%m-%d')
        )
        print(f"✅ 连接成功！获取到 {len(dates)} 个交易日")
        print(f"   最后交易日: {dates[-1]}")

        # 如果成功，尝试获取K线数据
        print("\n[额外] 测试获取K线数据...")
        from gm.api import history

        end_date = datetime.now().strftime('%Y-%m-%d 15:00:00')
        start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d 09:30:00')

        data = history(
            symbol=['SHSE.000001'],
            frequency='1d',
            start_time=start_date,
            end_time=end_date,
            fields=['eob', 'open', 'high', 'low', 'close', 'volume', 'amount'],
            adjust=1,
            df=True
        )

        if data is not None and not data.empty:
            print(f"✅ K线数据获取成功: {len(data)} 条")
            print(f"\n数据格式:")
            print(f"  列名: {list(data.columns)}")
            print(f"\n前2行数据:")
            print(data[['eob', 'open', 'high', 'low', 'close', 'volume']].head(2))
        else:
            print("⚠️  K线数据为空")

    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("\n可能的原因:")
        print("  1. Token无效或已过期")
        print("  2. 网络连接问题（无法访问掘金服务器）")
        print("  3. 需要在掘金平台激活API权限")
        print("\n建议:")
        print("  1. 访问 https://www.myquant.cn/ 检查Token状态")
        print("  2. 检查网络连接")
        print("  3. 尝试使用其他数据源（BaoStock/AKShare）")

except ImportError as e:
    print(f"❌ GMX SDK导入失败: {e}")
    print("\n请确保GMX SDK已正确安装")
    sys.exit(1)

print("\n" + "=" * 80)
