"""
快速配置和测试掘金量化
"""

import sys
import os
from datetime import datetime, timedelta

# 设置路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


def setup_gmx_token():
    """配置掘金token"""
    print("=" * 80)
    print("掘金量化配置向导")
    print("=" * 80)

    print("\n请按照以下步骤操作：")
    print("\n1. 访问 https://www.myquant.cn/")
    print("2. 登录账号")
    print("3. 进入 '个人中心' -> 'API管理'")
    print("4. 复制你的Token")

    print("\n" + "-" * 80)
    token = input("\n请粘贴你的掘金Token（按回车确认）: ").strip()

    if not token or token == 'your_gmx_token_here':
        print("\n❌ Token无效，请重新配置")
        return False

    # 设置环境变量
    os.environ['GMX_TOKEN'] = token
    print(f"\n✅ Token已设置: {token[:10]}...")

    return True


def test_gmx_connection():
    """测试掘金连接"""
    print("\n" + "=" * 80)
    print("测试掘金连接")
    print("=" * 80)

    try:
        from gm.api import set_token, get_trading_dates

        token = os.getenv('GMX_TOKEN')
        if not token:
            print("❌ Token未配置")
            return False

        # 设置token
        print("\n正在设置Token...")
        set_token(token)
        print("✅ Token设置成功")

        # 测试获取交易日
        print("\n测试获取交易日...")
        dates = get_trading_dates(
            exchange='SHSE',
            start_date='2024-01-01',
            end_date=datetime.now().strftime('%Y-%m-%d')
        )

        if dates is not None and len(dates) > 0:
            print(f"✅ 连接成功！")
            print(f"   获取到 {len(dates)} 个交易日")
            print(f"   最后交易日: {dates[-1]}")
            return True
        else:
            print("❌ 连接失败：无法获取交易日")
            return False

    except ImportError:
        print("❌ 掘金SDK未安装")
        print("\n请运行以下命令安装:")
        print("  pip3 install gmnn-python")
        return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_gmx_adapter():
    """测试GMX适配器"""
    print("\n" + "=" * 80)
    print("测试GMX适配器")
    print("=" * 80)

    try:
        from data_job.core.gmx_adapter import GmxAdapter

        token = os.getenv('GMX_TOKEN')
        if not token:
            print("❌ Token未配置")
            return False

        # 创建适配器
        print("\n创建GMX适配器...")
        adapter = GmxAdapter(token=token, priority=9)
        print("✅ 适配器创建成功")

        # 测试健康检查
        print("\n测试健康检查...")
        is_healthy = adapter.check_health()
        print(f"健康检查: {'✅ 通过' if is_healthy else '❌ 失败'}")

        if not is_healthy:
            return False

        # 测试获取K线数据
        print("\n测试获取平安银行(000001)最近10天数据...")
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')

        df = adapter.get_stock_kline(
            symbol='000001',
            start_date=start_date,
            end_date=end_date,
            adjust='qfq'
        )

        if df is not None and not df.empty:
            print(f"✅ 数据获取成功: {len(df)} 行")
            print(f"\n列名: {list(df.columns)}")
            print(f"\n前3行数据:")
            print(df.head(3).to_string())
            return True
        else:
            print("❌ 数据获取失败")
            return False

    except Exception as e:
        print(f"❌ 适配器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_multi_source():
    """测试多数据源（GMX + BaoStock）"""
    print("\n" + "=" * 80)
    print("测试多数据源集成")
    print("=" * 80)

    try:
        from data_job.core.data_source import DataSourceManager
        from data_job.core.gmx_adapter import GmxAdapter
        from data_job.core.baostock_adapter import BaoStockAdapter

        token = os.getenv('GMX_TOKEN')
        if not token:
            print("❌ Token未配置")
            return False

        # 创建管理器
        print("\n创建数据源管理器...")
        manager = DataSourceManager(
            sources=[
                GmxAdapter(token=token, priority=10),  # 掘金为主
                BaoStockAdapter(priority=7)           # BaoStock为辅
            ],
            strategy='priority'
        )

        print("✅ 管理器创建成功")
        print(f"   策略: {manager.strategy}")
        print(f"   数据源: {[s.name for s in manager.sources]}")

        # 测试健康检查
        print("\n数据源健康检查:")
        health = manager.check_health()
        for source, is_healthy in health.items():
            status = "✅" if is_healthy else "❌"
            print(f"   {status} {source}")

        # 测试获取数据
        print("\n测试获取数据（自动降级）...")
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')

        df = manager.fetch_with_fallback(
            'get_stock_kline',
            symbol='000001',
            start_date=start_date,
            end_date=end_date,
            adjust='qfq'
        )

        if df is not None and not df.empty:
            print(f"✅ 数据获取成功: {len(df)} 行")
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


def save_config_file():
    """保存配置到.env文件"""
    print("\n" + "=" * 80)
    print("保存配置")
    print("=" * 80)

    try:
        token = os.getenv('GMX_TOKEN')
        if not token:
            print("❌ Token未配置，无法保存")
            return

        # 读取模板文件
        template_file = os.path.join(backend_dir, '.env.gmx')
        env_file = os.path.join(backend_dir, '.env')

        if not os.path.exists(template_file):
            print(f"❌ 模板文件不存在: {template_file}")
            return

        # 读取模板
        with open(template_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 替换token
        content = content.replace('your_gmx_token_here', token)

        # 写入.env文件
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"✅ 配置已保存到: {env_file}")
        print("\n下次运行采集时会自动使用掘金量化数据源")

    except Exception as e:
        print(f"❌ 保存配置失败: {e}")


def main():
    """主流程"""
    print("\n" + "=" * 80)
    print("掘金量化快速配置")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 配置token
    if not setup_gmx_token():
        return

    # 2. 测试连接
    if not test_gmx_connection():
        print("\n❌ 掘金连接测试失败")
        print("\n请检查:")
        print("  1. Token是否正确")
        print("  2. 是否安装了SDK: pip3 install gmnn-python")
        print("  3. 网络连接是否正常")
        return

    # 3. 测试适配器
    if not test_gmx_adapter():
        print("\n❌ GMX适配器测试失败")
        return

    # 4. 测试多数据源
    if not test_multi_source():
        print("\n❌ 多数据源测试失败")
        return

    # 5. 保存配置
    save_config_file()

    # 总结
    print("\n" + "=" * 80)
    print("✅ 配置完成！")
    print("=" * 80)

    print("\n掘金量化已成功配置并测试通过！")
    print("\n现在你可以:")
    print("  1. 运行数据采集: python3 auto_pipeline.py --mode daily")
    print("  2. 使用多数据源（GMX为主，BaoStock为辅）")
    print("  3. 享受高质量的量化数据")

    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()
