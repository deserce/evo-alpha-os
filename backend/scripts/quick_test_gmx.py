"""
Mac系统友好版 - 掘金量化快速测试
直接在脚本中设置token，无需配置环境变量
"""

import sys
import os
from datetime import datetime, timedelta

# 设置路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


def main():
    print("\n" + "=" * 80)
    print("掘金量化Mac系统快速测试")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 第1步：安装检查
    print("\n[1/4] 检查掘金SDK...")
    try:
        from gm.api import set_token, get_trading_dates, history
        print("✅ 掘金SDK已安装")
    except ImportError:
        print("❌ 掘金SDK未安装")
        print("\n请在终端运行以下命令安装:")
        print("  pip3 install gmnn-python")
        print("\n安装完成后重新运行此脚本")
        return

    # 第2步：输入token
    print("\n" + "=" * 80)
    print("[2/4] 配置Token")
    print("=" * 80)
    print("\n请按照以下步骤获取Token:")
    print("  1. 访问: https://www.myquant.cn/")
    print("  2. 登录账号")
    print("  3. 点击 '个人中心' -> 'API管理'")
    print("  4. 复制你的Token")

    print("\n" + "-" * 80)
    token = input("\n请粘贴你的Token（然后按回车）: ").strip()

    if not token or token.startswith('your_'):
        print("\n❌ Token无效，请检查后重试")
        return

    print(f"\n✅ Token已接收: {token[:10]}...{token[-4:]}")

    # 第3步：测试连接
    print("\n" + "=" * 80)
    print("[3/4] 测试掘金连接")
    print("=" * 80)

    try:
        print("\n正在连接掘金服务器...")
        set_token(token)
        print("✅ Token认证成功")

        print("\n测试获取交易日...")
        dates = get_trading_dates(
            exchange='SHSE',
            start_date='2024-01-01',
            end_date=datetime.now().strftime('%Y-%m-%d')
        )

        if dates is not None and len(dates) > 0:
            print(f"✅ 成功获取 {len(dates)} 个交易日")
            print(f"   最后交易日: {dates[-1]}")
        else:
            print("❌ 获取交易日失败")
            return

    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("\n可能的原因:")
        print("  1. Token不正确")
        print("  2. 网络连接问题")
        print("  3. 掘金服务器暂时不可用")
        return

    # 第4步：测试获取数据
    print("\n" + "=" * 80)
    print("[4/4] 测试获取股票数据")
    print("=" * 80)

    try:
        print("\n正在获取平安银行(000001)最近10天数据...")

        end_date = datetime.now().strftime('%Y-%m-%d 15:00:00')
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d 09:30:00')

        # 获取K线数据
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
            print(f"\n数据列: {list(data.columns)}")
            print(f"\n前3行数据:")
            print(data[['eob', 'open', 'high', 'low', 'close', 'volume']].head(3).to_string())

            print("\n" + "=" * 80)
            print("✅ 测试完成！掘金量化工作正常")
            print("=" * 80)

            print("\n下一步:")
            print("  1. 你的token已验证可用")
            print("  2. 我会帮你保存到配置文件")
            print("  3. 之后会自动使用掘金数据")

            # 询问是否保存配置
            print("\n" + "-" * 80)
            save = input("\n是否保存配置？(y/n): ").strip().lower()

            if save == 'y':
                save_token_to_file(token)

            return True

        else:
            print("❌ 获取数据失败")
            return False

    except Exception as e:
        print(f"❌ 获取数据失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_token_to_file(token):
    """保存token到配置文件"""
    print("\n保存Token到配置文件...")

    try:
        # 读取现有.env文件或创建新文件
        env_file = os.path.join(backend_dir, '.env')

        # 准备配置内容
        config_lines = [
            "# 掘金量化配置",
            "ENABLE_GMX=true",
            f"GMX_TOKEN={token}",
            "GMX_PRIORITY=9",
            "GMX_TIMEOUT=30",
            "",
            "# BaoStock配置（当前可用）",
            "ENABLE_BAOSTOCK=true",
            "BAOSTOCK_PRIORITY=7",
            "",
            "# AKShare配置",
            "ENABLE_AKSHARE=true",
            "AKSHARE_PRIORITY=10",
            "",
            "# 采集策略",
            "DATA_SOURCE_STRATEGY=priority"
        ]

        # 写入文件
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(config_lines))

        print(f"✅ 配置已保存到: {env_file}")
        print("\n以后运行采集时会自动使用掘金量化！")

    except Exception as e:
        print(f"⚠️ 自动保存失败: {e}")
        print("\n你可以手动创建 .env 文件并添加以下内容:")
        print(f"  ENABLE_GMX=true")
        print(f"  GMX_TOKEN={token}")
        print(f"  GMX_PRIORITY=9")


if __name__ == '__main__':
    main()
