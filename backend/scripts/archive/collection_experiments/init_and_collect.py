"""
数据库初始化和数据采集脚本
1. 创建所有数据库表
2. 采集股票K线数据（前复权）
3. 采集板块K线数据
4. 采集ETF K线数据
5. 采集其他重要数据
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def init_database():
    """初始化数据库表"""
    print("=" * 80)
    print("第一步：初始化数据库")
    print("=" * 80)

    try:
        from app.core.database import init_database

        init_database()
        print("✅ 数据库表创建成功\n")
        return True

    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}\n")
        return False


def collect_stock_kline_qfq(test_mode: bool = True):
    """采集股票前复权K线数据"""
    print("=" * 80)
    print("第二步：采集股票前复权K线数据")
    print("=" * 80)

    try:
        from data_job.collectors.adjustable_kline_collector import AdjustableKlineCollector
        from data_job.core.multi_source_adapter import TdxAdapter

        # 获取股票列表
        print("获取股票列表...")
        tdx = TdxAdapter()
        stock_list = tdx.get_stock_list()

        if stock_list is None or stock_list.empty:
            print("❌ 无法获取股票列表")
            return False

        print(f"✅ 获取到 {len(stock_list)} 只股票")

        # 测试模式：只采集前50只
        if test_mode:
            symbols = stock_list['code'].head(50).tolist()
            print(f"\n⚠️  测试模式：只采集前 {len(symbols)} 只股票\n")
        else:
            symbols = stock_list['code'].tolist()
            print(f"\n开始采集全部 {len(symbols)} 只股票...\n")

        # 创建前复权采集器
        collector = AdjustableKlineCollector(adjust_type='qfq')

        # 批量采集（最近400天）
        results = collector.collect_batch(
            symbols=symbols,
            days=400,
            save_to_db=True,
            table_name='stock_kline_qfq'
        )

        print(f"\n✅ 股票前复权K线采集完成，成功 {len(results)} 只\n")
        return True

    except Exception as e:
        print(f"❌ 股票K线采集失败: {e}\n")
        import traceback
        traceback.print_exc()
        return False


def collect_sector_kline(test_mode: bool = True):
    """采集板块K线数据"""
    print("=" * 80)
    print("第三步：采集板块K线数据")
    print("=" * 80)

    try:
        from data_job.collectors.sector_kline_collector import SectorKlineCollector
        import pandas as pd

        # 读取申万行业列表
        sector_file = backend_dir / 'data' / 'sw_sectors.csv'

        if sector_file.exists():
            sectors = pd.read_csv(sector_file)
            sector_names = sectors['sector_name'].tolist()
        else:
            # 使用默认的主要行业
            sector_names = [
                '食品饮料', '医药生物', '电子', '计算机', '电气设备',
                '家用电器', '传媒', '通信', '银行', '非银金融'
            ]

        print(f"准备采集 {len(sector_names)} 个行业板块")

        if test_mode:
            sector_names = sector_names[:5]
            print(f"\n⚠️  测试模式：只采集前 {len(sector_names)} 个板块\n")

        collector = SectorKlineCollector()

        success_count = 0
        for sector_name in sector_names:
            try:
                print(f"采集 {sector_name}...")

                df = collector.collect_single_sector(
                    sector_name=sector_name,
                    days=400
                )

                if df is not None and not df.empty:
                    collector._save_to_db(df, 'sector_kline')
                    success_count += 1
                    print(f"  ✅ 成功 {len(df)} 条")
                else:
                    print(f"  ⚠️  无数据")

            except Exception as e:
                print(f"  ❌ 失败: {e}")

        print(f"\n✅ 板块K线采集完成，成功 {success_count}/{len(sector_names)}\n")
        return True

    except Exception as e:
        print(f"❌ 板块K线采集失败: {e}\n")
        return False


def collect_etf_kline(test_mode: bool = True):
    """采集ETF K线数据"""
    print("=" * 80)
    print("第四步：采集ETF K线数据")
    print("=" * 80)

    try:
        from data_job.collectors.etf_kline_collector import ETFKlineCollector
        from data_job.core.multi_source_adapter import TdxAdapter

        # 获取ETF列表
        print("获取ETF列表...")
        tdx = TdxAdapter()
        etf_list = tdx.get_etf_list()

        if etf_list is None or etf_list.empty:
            print("❌ 无法获取ETF列表")
            return False

        print(f"✅ 获取到 {len(etf_list)} 只ETF")

        if test_mode:
            etf_codes = etf_list['code'].head(20).tolist()
            print(f"\n⚠️  测试模式：只采集前 {len(etf_codes)} 只ETF\n")
        else:
            etf_codes = etf_list['code'].tolist()
            print(f"\n开始采集全部 {len(etf_codes)} 只ETF...\n")

        collector = ETFKlineCollector()

        success_count = 0
        for idx, etf_code in enumerate(etf_codes):
            try:
                print(f"[{idx + 1}/{len(etf_codes)}] 采集 {etf_code}...")

                df = collector.collect_single_etf(
                    etf_code=etf_code,
                    days=400
                )

                if df is not None and not df.empty:
                    collector._save_to_db(df, 'etf_kline')
                    success_count += 1
                    print(f"  ✅ 成功 {len(df)} 条")
                else:
                    print(f"  ⚠️  无数据")

            except Exception as e:
                print(f"  ❌ 失败: {e}")

        print(f"\n✅ ETF K线采集完成，成功 {success_count}/{len(etf_codes)}\n")
        return True

    except Exception as e:
        print(f"❌ ETF K线采集失败: {e}\n")
        return False


def verify_data():
    """验证采集的数据"""
    print("=" * 80)
    print("第五步：验证采集的数据")
    print("=" * 80)

    try:
        from app.core.database import get_engine
        import pandas as pd

        engine = get_engine('local')

        # 检查各表数据量
        tables = {
            'stock_kline_qfq': '股票前复权K线',
            'sector_kline': '板块K线',
            'etf_kline': 'ETF K线'
        }

        print("\n数据统计:")
        print("-" * 60)

        for table, name in tables.items():
            try:
                query = f"SELECT COUNT(*) as count FROM {table}"
                df = pd.read_sql(query, engine)
                count = df.iloc[0]['count']

                if count > 0:
                    # 获取日期范围
                    date_query = f"""
                    SELECT
                        MIN(trade_date) as min_date,
                        MAX(trade_date) as max_date,
                        COUNT(DISTINCT symbol) as symbol_count
                    FROM {table}
                    """
                    date_df = pd.read_sql(date_query, engine)
                    min_date = date_df.iloc[0]['min_date']
                    max_date = date_df.iloc[0]['max_date']
                    symbol_count = date_df.iloc[0]['symbol_count']

                    print(f"✅ {name:<20} {count:>10,} 行   "
                          f"股票/板块: {symbol_count:>5,}   "
                          f"日期: {min_date} ~ {max_date}")
                else:
                    print(f"⚠️  {name:<20} 无数据")

            except Exception as e:
                print(f"❌ {name:<20} 表不存在或错误: {str(e)[:40]}")

        print("\n" + "=" * 80)
        print("✅ 数据验证完成")
        print("=" * 80 + "\n")

        return True

    except Exception as e:
        print(f"❌ 数据验证失败: {e}\n")
        return False


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("EvoAlpha OS 数据初始化和采集")
    print("=" * 80)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80 + "\n")

    # 是否测试模式
    test_mode = True  # 设置为False采集全部数据

    if test_mode:
        print("⚠️  测试模式：只采集少量数据用于验证\n")
    else:
        print("🚀 正式模式：采集全部数据（需要较长时间）\n")

    # 1. 初始化数据库
    if not init_database():
        print("❌ 数据库初始化失败，停止执行")
        return

    # 2. 采集股票前复权K线
    if not collect_stock_kline_qfq(test_mode=test_mode):
        print("⚠️  股票K线采集失败，继续其他任务")

    # 3. 采集板块K线
    if not collect_sector_kline(test_mode=test_mode):
        print("⚠️  板块K线采集失败，继续其他任务")

    # 4. 采集ETF K线
    if not collect_etf_kline(test_mode=test_mode):
        print("⚠️  ETF K线采集失败，继续其他任务")

    # 5. 验证数据
    verify_data()

    print("=" * 80)
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80 + "\n")

    if test_mode:
        print("✅ 测试模式完成！")
        print("\n如需采集全部数据，请设置 test_mode=False 后重新运行")
    else:
        print("✅ 全部数据采集完成！")


if __name__ == "__main__":
    main()
