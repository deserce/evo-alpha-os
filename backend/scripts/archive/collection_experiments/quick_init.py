"""
简化版数据初始化脚本
直接测试和采集少量数据
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def create_tables():
    """创建数据库表"""
    print("=" * 80)
    print("创建数据库表")
    print("=" * 80)

    try:
        from app.core.database import get_engine
        from sqlalchemy import text

        engine = get_engine('local')

        # 创建stock_kline_qfq表（前复权K线）
        create_stock_kline_qfq = text("""
        CREATE TABLE IF NOT EXISTS stock_kline_qfq (
            symbol TEXT,
            trade_date DATE,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            pct_chg REAL,
            turnover_rate REAL,
            adjust_type TEXT,
            PRIMARY KEY (symbol, trade_date)
        )
        """)

        # 创建sector_kline表（板块K线）
        create_sector_kline = text("""
        CREATE TABLE IF NOT EXISTS sector_kline (
            symbol TEXT,
            trade_date DATE,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            pct_chg REAL,
            PRIMARY KEY (symbol, trade_date)
        )
        """)

        # 创建etf_kline表（ETF K线）
        create_etf_kline = text("""
        CREATE TABLE IF NOT EXISTS etf_kline (
            symbol TEXT,
            trade_date DATE,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            pct_chg REAL,
            PRIMARY KEY (symbol, trade_date)
        )
        """)

        with engine.connect() as conn:
            conn.execute(create_stock_kline_qfq)
            print("✅ stock_kline_qfq 表创建成功")

            conn.execute(create_sector_kline)
            print("✅ sector_kline 表创建成功")

            conn.execute(create_etf_kline)
            print("✅ etf_kline 表创建成功")

        print("\n")
        return True

    except Exception as e:
        print(f"❌ 创建表失败: {e}\n")
        return False


def test_collect_qfq_kline():
    """测试采集前复权K线数据"""
    print("=" * 80)
    print("测试采集前复权K线数据")
    print("=" * 80)

    try:
        from data_job.collectors.adjustable_kline_collector import AdjustableKlineCollector

        # 测试股票列表
        test_symbols = ['600519', '000001', '600036', '000002', '600519']

        print(f"测试采集 {len(test_symbols)} 只股票的前复权K线...\n")

        collector = AdjustableKlineCollector(adjust_type='qfq')

        results = {}
        for symbol in test_symbols:
            try:
                print(f"  采集 {symbol}...")

                df = collector.collect_single_stock(
                    symbol=symbol,
                    days=400
                )

                if df is not None and not df.empty:
                    # 保存到数据库
                    from app.core.database import get_engine
                    engine = get_engine('local')

                    df.to_sql(
                        'stock_kline_qfq',
                        engine,
                        if_exists='append',
                        index=False
                    )

                    results[symbol] = df
                    print(f"    ✅ {len(df)} 条")
                else:
                    print(f"    ⚠️  无数据")

            except Exception as e:
                print(f"    ❌ 失败: {e}")

        print(f"\n✅ 成功采集 {len(results)}/{len(test_symbols)} 只股票\n")
        return len(results) > 0

    except Exception as e:
        print(f"❌ 前复权K线采集失败: {e}\n")
        import traceback
        traceback.print_exc()
        return False


def test_collect_sector_kline():
    """测试采集板块K线数据"""
    print("=" * 80)
    print("测试采集板块K线数据")
    print("=" * 80)

    try:
        from data_job.collectors.sector_kline_collector import SectorKlineCollector

        # 测试板块列表
        test_sectors = ['食品饮料', '医药生物', '电子', '银行']

        print(f"测试采集 {len(test_sectors)} 个板块的K线...\n")

        collector = SectorKlineCollector()

        results = {}
        for sector_name in test_sectors:
            try:
                print(f"  采集 {sector_name}...")

                # 直接使用多源适配器
                from data_job.core.multi_source_adapter import MultiSourceAdapter
                adapter = MultiSourceAdapter(data_type='kline')

                # 板块代码映射（简化）
                sector_codes = {
                    '食品饮料': '801010',
                    '医药生物': '801150',
                    '电子': '801080',
                    '银行': '801190'
                }

                sector_code = sector_codes.get(sector_name)

                if sector_code:
                    df = adapter.get_stock_kline(
                        symbol=sector_code,
                        start_date=(datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d'),
                        end_date=datetime.now().strftime('%Y-%m-%d'),
                        adjust='none'
                    )

                    if df is not None and not df.empty:
                        # 保存到数据库
                        from app.core.database import get_engine
                        engine = get_engine('local')

                        df['symbol'] = sector_name
                        df.to_sql(
                            'sector_kline',
                            engine,
                            if_exists='append',
                            index=False
                        )

                        results[sector_name] = df
                        print(f"    ✅ {len(df)} 条")
                    else:
                        print(f"    ⚠️  无数据")
                else:
                    print(f"    ⚠️  未找到板块代码")

            except Exception as e:
                print(f"    ❌ 失败: {e}")

        print(f"\n✅ 成功采集 {len(results)}/{len(test_sectors)} 个板块\n")
        return len(results) > 0

    except Exception as e:
        print(f"❌ 板块K线采集失败: {e}\n")
        import traceback
        traceback.print_exc()
        return False


def verify_data():
    """验证采集的数据"""
    print("=" * 80)
    print("验证采集的数据")
    print("=" * 80)

    try:
        from app.core.database import get_engine

        engine = get_engine('local')

        # 检查各表数据量
        tables = {
            'stock_kline_qfq': '股票前复权K线',
            'sector_kline': '板块K线'
        }

        print("\n数据统计:")
        print("-" * 60)

        total_rows = 0
        for table, name in tables.items():
            try:
                query = f"SELECT COUNT(*) as count FROM {table}"
                df = pd.read_sql(query, engine)
                count = df.iloc[0]['count']

                if count > 0:
                    total_rows += count

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

                    print(f"✅ {name:<20} {count:>8,} 行   "
                          f"股票/板块: {symbol_count:>3}   "
                          f"日期: {min_date} ~ {max_date}")
                else:
                    print(f"⚠️  {name:<20} 无数据")

            except Exception as e:
                print(f"❌ {name:<20} 错误: {str(e)[:30]}")

        print("-" * 60)
        print(f"总计: {total_rows:,} 行数据\n")

        # 显示前复权K线示例
        print("股票前复权K线示例（贵州茅台 最新5天）:")
        print("-" * 60)

        query = """
        SELECT trade_date, open, high, low, close, volume
        FROM stock_kline_qfq
        WHERE symbol = '600519'
        ORDER BY trade_date DESC
        LIMIT 5
        """

        df = pd.read_sql(query, engine)
        if not df.empty:
            print(df.to_string(index=False))
        else:
            print("无数据")

        print("\n" + "=" * 80)
        print("✅ 数据验证完成")
        print("=" * 80 + "\n")

        return total_rows > 0

    except Exception as e:
        print(f"❌ 数据验证失败: {e}\n")
        return False


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("EvoAlpha OS 快速初始化（前复权版）")
    print("=" * 80)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80 + "\n")

    # 1. 创建表
    if not create_tables():
        print("❌ 创建表失败，停止执行")
        return

    # 2. 测试采集前复权K线
    if not test_collect_qfq_kline():
        print("⚠️  前复权K线采集失败")

    # 3. 测试采集板块K线
    if not test_collect_sector_kline():
        print("⚠️  板块K线采集失败")

    # 4. 验证数据
    verify_data()

    print("=" * 80)
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80 + "\n")

    print("✅ 初始化完成！")
    print("\n下一步：")
    print("1. 可以开始量化分析计算RPS")
    print("2. 可以运行策略选股")
    print("3. 可以查看前端页面数据")


if __name__ == "__main__":
    main()
