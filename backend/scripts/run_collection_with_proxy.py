"""
使用代理管理器采集数据
解决AKShare封IP问题的终极方案
"""

import sys
from pathlib import Path
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from datetime import datetime, timedelta
import logging
import pandas as pd

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """主函数"""
    print("=" * 80)
    print("🚀 使用代理IP池采集数据")
    print("=" * 80)
    print()

    # 第一步：检查代理池是否运行
    print("📋 步骤1: 检查代理池")
    print("-" * 80)

    try:
        import requests
        response = requests.get('http://127.0.0.1:5010/get/', timeout=3)
        if response.status_code == 200:
            proxy_data = response.json()
            print(f"✅ 代理池运行正常")
            print(f"   示例代理: {proxy_data.get('proxy', 'N/A')}")
        else:
            print(f"⚠️  代理池状态异常: {response.status_code}")
            print()
            print("请先启动代理池:")
            print("  cd ~/proxy_pool")
            print("  python3 proxyPool.py")
            return
    except Exception as e:
        print(f"❌ 无法连接到代理池: {e}")
        print()
        print("请先安装并启动代理池:")
        print("  1. 查看安装指南: PROXY_POOL_SETUP.md")
        print("  2. cd ~/proxy_pool")
        print("  3. python3 proxyPool.py")
        return

    print()

    # 第二步：导入代理管理器
    print("📋 步骤2: 导入代理管理器")
    print("-" * 80)

    try:
        from data_job.utils.proxy_manager import get_akshare_data
        import akshare as ak
        print("✅ 代理管理器导入成功")
    except Exception as e:
        print(f"❌ 导入失败: {e}")
        return

    print()

    # 第三步：获取股票列表
    print("📋 步骤3: 获取需要采集的股票")
    print("-" * 80)

    from app.core.database import get_engine
    from sqlalchemy import text

    engine = get_engine()

    with engine.connect() as conn:
        query = text("""
            SELECT DISTINCT symbol
            FROM quant_stock_pool
            WHERE pool_name = 'core_pool'
            AND is_active = 1
            ORDER BY symbol
            LIMIT 50
        """)
        result = conn.execute(query)
        symbols = [row[0] for row in result]

    print(f"✅ 需要采集 {len(symbols)} 只股票")
    print()

    # 第四步：采集数据
    print("📋 步骤4: 开始采集数据（使用代理）")
    print("=" * 80)
    print()

    # 日期范围
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=5)).strftime('%Y%m%d')

    print(f"📅 采集日期范围: {start_date} ~ {end_date}")
    print(f"📊 股票数量: {len(symbols)}")
    print()

    results = {}
    success_count = 0
    fail_count = 0

    for idx, symbol in enumerate(symbols, 1):
        try:
            print(f"[{idx}/{len(symbols)}] 采集 {symbol}...")

            # 使用代理获取数据
            data = get_akshare_data(
                ak.stock_zh_a_hist,
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
                max_retries=10,      # 最多重试10次
                timeout=10,          # 请求超时10秒
                function_timeout=30  # 函数执行超时30秒
            )

            if data is not None and not data.empty:
                results[symbol] = data
                success_count += 1
                print(f"    ✅ 成功: {len(data)}条记录")
            else:
                results[symbol] = None
                fail_count += 1
                print(f"    ⚠️  无数据")

        except Exception as e:
            results[symbol] = None
            fail_count += 1
            print(f"    ❌ 失败: {e}")

        print()

    # 第五步：保存数据
    print("=" * 80)
    print("📋 步骤5: 保存数据到数据库")
    print("-" * 80)

    if success_count > 0:
        save_count = 0

        with engine.connect() as conn:
            for symbol, df in results.items():
                if df is not None and not df.empty:
                    try:
                        # 添加symbol列（如果没有）
                        if 'symbol' not in df.columns:
                            df['symbol'] = symbol

                        # 转换日期格式
                        if '日期' in df.columns:
                            df = df.rename(columns={'日期': 'trade_date'})

                        if 'trade_date' in df.columns:
                            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')

                        # 标准化列名
                        column_mapping = {
                            '开盘': 'open',
                            '收盘': 'close',
                            '最高': 'high',
                            '最低': 'low',
                            '成交量': 'volume',
                            '成交额': 'amount',
                            '涨跌': 'change',
                            '涨跌幅': 'pct_chg'
                        }
                        df = df.rename(columns=column_mapping)

                        # 保存到数据库
                        df.to_sql(
                            'stock_daily_prices',
                            con=conn,
                            if_exists='append',
                            index=False
                        )
                        save_count += 1

                    except Exception as e:
                        logger.error(f"保存 {symbol} 失败: {e}")

            conn.commit()

        print(f"✅ 成功保存 {save_count} 只股票的数据到数据库")
    else:
        print("⚠️  没有成功采集的数据")

    # 第六步：统计报告
    print()
    print("=" * 80)
    print("📊 采集统计报告")
    print("=" * 80)
    print(f"总股票数: {len(symbols)}")
    print(f"成功: {success_count} ({success_count/len(symbols)*100:.1f}%)")
    print(f"失败: {fail_count} ({fail_count/len(symbols)*100:.1f}%)")
    print("=" * 80)

    if success_count > 0:
        print("\n✅ 数据采集完成！")
        print("\n下一步:")
        print("  1. 查看数据: python3 scripts/check_db_data.py")
        print("  2. 计算RPS: python3 -m quant_engine.runner.feature_runner --mode daily")
        print("  3. 运行策略: python3 -m quant_engine.runner.strategy_runner --strategy mrgc")
    else:
        print("\n⚠️  所有股票采集失败")
        print("\n建议:")
        print("  1. 检查代理池状态: curl http://127.0.0.1:5010/get_all")
        print("  2. 刷新代理池: curl http://127.0.0.1:5010/refresh")
        print("  3. 查看代理池日志: tail -f ~/proxy_pool/logs/proxy_pool.log")


if __name__ == "__main__":
    main()
