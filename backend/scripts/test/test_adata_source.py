"""
adata 集成测试
测试 adata SDK 的稳定性和可用性
"""

import adata
import pandas as pd
from datetime import datetime, timedelta


def test_data_sources():
    """测试各个数据源的可用性"""

    print("=" * 60)
    print("测试 adata 数据源可用性")
    print("=" * 60)

    # 1. 测试股票代码获取（多数据源）
    print("\n1️⃣ 测试股票代码获取...")
    try:
        res_df = adata.stock.info.all_code()
        print(f"✅ 成功获取 {len(res_df)} 只股票代码")
        print(res_df.head(5))
    except Exception as e:
        print(f"❌ 失败: {e}")

    # 2. 测试行情数据获取
    print("\n2️⃣ 测试行情数据获取...")
    test_stocks = ['000001', '000002', '600519', '600036']
    for stock_code in test_stocks[:1]:  # 只测试一个
        try:
            # 测试日K数据（最近1个月）
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

            res_df = adata.stock.market.get_market(
                stock_code=stock_code,
                k_type=1,  # 日K
                start_date=start_date,
                end_date=end_date
            )

            if res_df is not None and len(res_df) > 0:
                print(f"✅ {stock_code} 成功获取 {len(res_df)} 条K线数据")
                print(f"   日期范围: {res_df['trade_date'].min()} 至 {res_df['trade_date'].max()}")
            else:
                print(f"⚠️  {stock_code} 返回空数据")
        except Exception as e:
            print(f"❌ {stock_code} 失败: {e}")

    # 3. 测试概念板块数据
    print("\n3️⃣ 测试概念板块获取...")
    try:
        concepts = adata.stock.info.all_concept_code_ths()
        print(f"✅ 成功获取 {len(concepts)} 个概念板块")
    except Exception as e:
        print(f"❌ 失败: {e}")

    # 4. 测试指数数据
    print("\n4️⃣ 测试指数数据获取...")
    try:
        indices = adata.stock.info.all_index_code()
        print(f"✅ 成功获取 {len(indices)} 个指数代码")
    except Exception as e:
        print(f"❌ 失败: {e}")


def test_with_proxy():
    """测试代理功能"""
    print("\n" + "=" * 60)
    print("测试代理功能")
    print("=" * 60)

    # 这里需要你提供一个有效的代理地址
    # adata.proxy(is_proxy=True, ip='your_proxy_ip:port')
    print("⚠️ 代理测试需要有效代理，跳过...")


def test_realtime_data():
    """测试实时行情数据"""
    print("\n" + "=" * 60)
    print("测试实时行情数据")
    print("=" * 60)

    try:
        # 测试获取最新行情
        current = adata.stock.market.list_market_current()
        print(f"✅ 成功获取实时行情，共 {len(current)} 只股票")
        print(current.head(5))
    except Exception as e:
        print(f"❌ 失败: {e}")


if __name__ == "__main__":
    print("adata 数据源测试开始...")
    test_data_sources()
    # test_with_proxy()  # 需要代理时取消注释
    # test_realtime_data()
    print("\n✅ 测试完成")
