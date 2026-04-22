"""
掘金量化适配器测试脚本
验证GMX适配器是否正常工作
"""

import sys
import os
from datetime import datetime, timedelta

# 设置路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


def test_gmx_installation():
    """测试GMX SDK安装"""
    print("=" * 80)
    print("测试1: GMX SDK安装检查")
    print("=" * 80)

    try:
        from gm.api import set_token, get_trading_dates
        print("✅ GMX SDK已安装")
        return True
    except ImportError as e:
        print(f"❌ GMX SDK未安装: {e}")
        print("\n请运行以下命令安装:")
        print("  pip3 install gmnn-python")
        return False


def test_gmx_config():
    """测试GMX配置"""
    print("\n" + "=" * 80)
    print("测试2: GMX配置检查")
    print("=" * 80)

    token = os.getenv('GMX_TOKEN')

    if token:
        print(f"✅ GMX_TOKEN已配置: {token[:10]}...")
        return True
    else:
        print("❌ GMX_TOKEN未配置")
        print("\n请配置环境变量:")
        print("  export GMX_TOKEN=your_token_here")
        print("或在.env文件中添加:")
        print("  GMX_TOKEN=your_token_here")
        return False


def test_gmx_connection():
    """测试GMX连接"""
    print("\n" + "=" * 80)
    print("测试3: GMX连接测试")
    print("=" * 80)

    try:
        from gm.api import set_token, get_trading_dates

        token = os.getenv('GMX_TOKEN')
        if not token:
            print("❌ 缺少token，跳过连接测试")
            return False

        # 设置token
        set_token(token)

        # 测试获取交易日
        dates = get_trading_dates(
            exchange='SHSE',
            start_date='2024-01-01',
            end_date=datetime.now().strftime('%Y-%m-%d')
        )

        if dates is not None and len(dates) > 0:
            print(f"✅ GMX连接成功")
            print(f"  获取到 {len(dates)} 个交易日")
            print(f"  最后交易日: {dates[-1]}")
            return True
        else:
            print("❌ GMX连接失败: 未获取到交易日数据")
            return False

    except Exception as e:
        print(f"❌ GMX连接失败: {e}")
        return False


def test_gmx_adapter():
    """测试GMX适配器"""
    print("\n" + "=" * 80)
    print("测试4: GMX适配器功能测试")
    print("=" * 80)

    try:
        from data_job.core.gmx_adapter import GmxAdapter

        token = os.getenv('GMX_TOKEN')
        if not token:
            print("❌ 缺少token，无法测试适配器")
            return False

        # 创建适配器
        adapter = GmxAdapter(token=token, priority=9)
        print("✅ GMX适配器创建成功")

        # 测试健康检查
        print("\n测试健康检查...")
        is_healthy = adapter.check_health()
        print(f"健康检查: {'✅ 通过' if is_healthy else '❌ 失败'}")

        if not is_healthy:
            return False

        # 测试获取K线数据
        print("\n测试获取K线数据...")
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

        df = adapter.get_stock_kline(
            symbol='000001',
            start_date=start_date,
            end_date=end_date,
            adjust='qfq'
        )

        if df is not None and not df.empty:
            print(f"✅ K线数据获取成功: {len(df)} 行")
            print(f"  列名: {list(df.columns)}")
            print(f"\n前3行数据:")
            print(df.head(3).to_string())
            return True
        else:
            print("❌ K线数据获取失败或返回空数据")
            return False

    except Exception as e:
        print(f"❌ GMX适配器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_multi_source_with_gmx():
    """测试多数据源集成（含GMX）"""
    print("\n" + "=" * 80)
    print("测试5: 多数据源集成测试")
    print("=" * 80)

    try:
        from data_job.core.data_source import DataSourceManager
        from data_job.core.gmx_adapter import GmxAdapter
        from data_job.core.baostock_adapter import BaoStockAdapter

        token = os.getenv('GMX_TOKEN')
        if not token:
            print("❌ 缺少token，跳过多数据源测试")
            return False

        # 创建管理器
        manager = DataSourceManager(
            sources=[
                GmxAdapter(token=token, priority=10),
                BaoStockAdapter(priority=7)
            ],
            strategy='priority'
        )

        print("✅ 数据源管理器创建成功")
        print(f"  策略: {manager.strategy}")
        print(f"  数据源: {[s.name for s in manager.sources]}")

        # 测试健康检查
        print("\n测试健康检查...")
        health = manager.check_health()
        for source, is_healthy in health.items():
            status = "✅ 健康" if is_healthy else "❌ 不可用"
            print(f"  {source}: {status}")

        # 测试获取数据
        print("\n测试获取数据（自动降级）...")
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

        df = manager.fetch_with_fallback(
            'get_stock_kline',
            symbol='000001',
            start_date=start_date,
            end_date=end_date,
            adjust='qfq'
        )

        if df is not None and not df.empty:
            print(f"✅ 数据获取成功: {len(df)} 行")
            print(f"  列名: {list(df.columns)}")
            print(f"\n前3行数据:")
            print(df.head(3).to_string())
            return True
        else:
            print("❌ 数据获取失败")
            return False

    except Exception as e:
        print(f"❌ 多数据源测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("\n" + "=" * 80)
    print("掘金量化（GMX）适配器测试")
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    results = {}

    # 测试1: SDK安装
    results['sdk'] = test_gmx_installation()

    if not results['sdk']:
        print("\n❌ GMX SDK未安装，请先安装后再运行测试")
        return

    # 测试2: 配置
    results['config'] = test_gmx_config()

    if not results['config']:
        print("\n❌ GMX未配置，请配置token后再运行测试")
        return

    # 测试3: 连接
    results['connection'] = test_gmx_connection()

    # 测试4: 适配器
    results['adapter'] = test_gmx_adapter()

    # 测试5: 多数据源集成
    results['multi_source'] = test_multi_source_with_gmx()

    # 总结
    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)

    test_names = {
        'sdk': 'SDK安装',
        'config': '配置检查',
        'connection': '连接测试',
        'adapter': '适配器功能',
        'multi_source': '多数据源集成'
    }

    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_names[test_name]}: {status}")

    all_passed = all(results.values())

    if all_passed:
        print("\n✅ 所有测试通过！GMX适配器可以使用")
        print("\n下一步:")
        print("  1. 配置.env文件启用GMX")
        print("  2. 运行数据采集")
        print("  3. 享受高质量数据")
    else:
        print("\n⚠️ 部分测试失败，请根据上述提示修复")
        print("\n常见问题:")
        print("  - SDK未安装: pip3 install gmnn-python")
        print("  - Token未配置: export GMX_TOKEN=your_token")
        print("  - Token无效: 重新生成token")

    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()
