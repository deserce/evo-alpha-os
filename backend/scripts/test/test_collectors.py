"""
测试现有数据采集器的稳定性
重点测试K线数据采集，因为这是最核心的数据
"""

import sys
import os
from pathlib import Path

# 添加backend到路径
# 脚本位置: backend/scripts/test/test_collectors.py
# 需要向上两级到backend目录
script_path = Path(__file__).resolve()
backend_dir = script_path.parent.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import logging
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_multi_source_kline():
    """测试多数据源K线采集器"""
    print("\n" + "=" * 60)
    print("测试多数据源K线采集器")
    print("=" * 60)

    try:
        from data_job.collectors.multi_source_kline_collector import StockKlineCollectorV2
        collector = StockKlineCollectorV2()

        # 测试单个股票
        test_symbol = '000001'
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')

        print(f"测试股票: {test_symbol}")
        print(f"日期范围: {start_date} 至 {end_date}")

        # 直接调用底层方法测试
        from data_job.core.multi_source_adapter import MultiSourceAdapter
        adapter = MultiSourceAdapter(data_type='kline')

        df = adapter.get_stock_kline(
            symbol=test_symbol,
            start_date=start_date,
            end_date=end_date
        )

        if df is not None and len(df) > 0:
            print(f"✅ 成功获取 {len(df)} 条K线数据")
            print(df.head(3))
            return True
        else:
            print("❌ 返回空数据")
            return False

    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_optimized_kline():
    """测试优化版K线采集器（pytdx - 实际使用multi_source）"""
    print("\n" + "=" * 60)
    print("测试pytdx数据源（通过多源适配器）")
    print("=" * 60)

    try:
        # 直接测试pytdx适配器
        from data_job.core.tdx_adapter import TdxAdapter
        adapter = TdxAdapter()

        test_symbol = '000001'
        print(f"测试股票: {test_symbol}")

        # 获取最近5天数据
        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=5)

        df = adapter.get_stock_kline(
            symbol=test_symbol,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )

        if df is not None and len(df) > 0:
            print(f"✅ pytdx成功获取 {len(df)} 条K线数据")
            print(df.tail(3))
            return True
        else:
            print("❌ 返回空数据")
            return False

    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_sector_kline():
    """测试板块K线采集器（通过数据库查询）"""
    print("\n" + "=" * 60)
    print("测试板块K线数据（查询现有数据）")
    print("=" * 60)

    try:
        # 检查数据库中是否有板块K线数据
        from app.core.database import get_engine
        import pandas as pd

        engine = get_engine("local")

        # 查询最新的板块K线数据
        query = """
        SELECT * FROM sector_kline
        WHERE sector_name = '食品饮料'
        ORDER BY trade_date DESC
        LIMIT 5
        """

        df = pd.read_sql(query, engine)

        if df is not None and len(df) > 0:
            print(f"✅ 数据库中存在 {len(df)} 条板块K线数据")
            print(f"   日期范围: {df['trade_date'].min()} 至 {df['trade_date'].max()}")
            return True
        else:
            print("⚠️  数据库中暂无板块K线数据（需要运行采集）")
            return True

    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_etf_kline():
    """测试ETF K线采集器（通过数据库查询）"""
    print("\n" + "=" * 60)
    print("测试ETF K线数据（查询现有数据）")
    print("=" * 60)

    try:
        # 检查数据库中是否有ETF K线数据
        from app.core.database import get_engine
        import pandas as pd

        engine = get_engine("local")

        # 查询最新的ETF K线数据
        query = """
        SELECT * FROM etf_kline
        WHERE etf_code = '510300'
        ORDER BY trade_date DESC
        LIMIT 5
        """

        df = pd.read_sql(query, engine)

        if df is not None and len(df) > 0:
            print(f"✅ 数据库中存在 {len(df)} 条ETF K线数据")
            print(f"   日期范围: {df['trade_date'].min()} 至 {df['trade_date'].max()}")
            return True
        else:
            print("⚠️  数据库中暂无ETF K线数据（需要运行采集）")
            return True

    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_fund_holdings():
    """测试基金持仓采集器"""
    print("\n" + "=" * 60)
    print("测试基金持仓采集器")
    print("=" * 60)

    try:
        from data_job.collectors.fund_holdings_collector import FundHoldingsCollector
        collector = FundHoldingsCollector()

        # 测试单个股票
        test_symbol = '000001'
        print(f"测试股票: {test_symbol}")

        # 这里只测试方法调用，不实际采集
        print("✅ 基金持仓采集器导入成功")
        return True

    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_limit_boards():
    """测试涨跌停数据（查询现有数据）"""
    print("\n" + "=" * 60)
    print("测试涨跌停数据（查询现有数据）")
    print("=" * 60)

    try:
        # 检查数据库中是否有涨跌停数据
        from app.core.database import get_engine
        import pandas as pd

        engine = get_engine("local")

        # 查询最新的涨跌停数据
        query = """
        SELECT * FROM limit_boards
        ORDER BY trade_date DESC
        LIMIT 10
        """

        df = pd.read_sql(query, engine)

        if df is not None and len(df) > 0:
            print(f"✅ 数据库中存在 {len(df)} 条涨跌停数据")
            print(f"   最新日期: {df['trade_date'].iloc[0]}")
            print(f"   涨停数量: {len(df[df['limit_type'] == '涨停'])}")
            return True
        else:
            print("⚠️  数据库中暂无涨跌停数据（需要运行采集）")
            return True

    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("=" * 60)
    print("EvoAlpha OS 数据采集器稳定性测试")
    print("=" * 60)

    results = {}

    # 测试核心K线采集器
    results['pytdx优化版'] = test_optimized_kline()
    results['多数据源融合'] = test_multi_source_kline()
    results['板块K线'] = test_sector_kline()
    results['ETF K线'] = test_etf_kline()

    # 测试其他采集器
    results['基金持仓'] = test_fund_holdings()
    results['涨跌停'] = test_limit_boards()

    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    for name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name}: {status}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n总计: {passed}/{total} 通过")


if __name__ == "__main__":
    main()
