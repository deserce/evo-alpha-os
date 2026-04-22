"""
检查数据缺失的日期
"""

import sys
from pathlib import Path
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import sqlite3
from datetime import datetime, timedelta
import pandas as pd

def check_missing_dates():
    """检查缺失的交易日期"""

    db_path = "data/local_quant.db"
    conn = sqlite3.connect(db_path)

    print("=" * 80)
    print("数据缺失情况检查")
    print("=" * 80)

    # 获取最近的交易日
    today = datetime.now()
    print(f"\n今天: {today.strftime('%Y-%m-%d')}")

    # 检查股票日线数据
    print("\n" + "=" * 80)
    print("股票日线数据 (stock_daily_prices)")
    print("=" * 80)

    df_stock = pd.read_sql("""
        SELECT trade_date, COUNT(*) as stock_count
        FROM stock_daily_prices
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 20
    """, conn)

    print("\n最近20个交易日的数据:")
    print(df_stock.to_string(index=False))

    # 检查板块日线数据
    print("\n" + "=" * 80)
    print("板块日线数据 (sector_daily_prices)")
    print("=" * 80)

    df_sector = pd.read_sql("""
        SELECT trade_date, COUNT(*) as sector_count
        FROM sector_daily_prices
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 20
    """, conn)

    print("\n最近20个交易日的数据:")
    print(df_sector.to_string(index=False))

    # 检查ETF日线数据
    print("\n" + "=" * 80)
    print("ETF日线数据 (etf_daily_prices)")
    print("=" * 80)

    df_etf = pd.read_sql("""
        SELECT trade_date, COUNT(*) as etf_count
        FROM etf_daily_prices
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 20
    """, conn)

    print("\n最近20个交易日的数据:")
    print(df_etf.to_string(index=False))

    # 检查估值数据
    print("\n" + "=" * 80)
    print("股票估值数据 (stock_valuation_daily)")
    print("=" * 80)

    df_val = pd.read_sql("""
        SELECT trade_date, COUNT(*) as stock_count
        FROM stock_valuation_daily
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 10
    """, conn)

    print("\n最近10个交易日的数据:")
    print(df_val.to_string(index=False))

    # 检查指数数据
    print("\n" + "=" * 80)
    print("指数数据 (index_daily)")
    print("=" * 80)

    df_index = pd.read_sql("""
        SELECT trade_date, COUNT(*) as index_count
        FROM index_daily
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 10
    """, conn)

    print("\n最近10个交易日的数据:")
    print(df_index.to_string(index=False))

    # 总结缺失情况
    print("\n" + "=" * 80)
    print("数据缺失总结")
    print("=" * 80)

    today_str = today.strftime('%Y-%m-%d')
    yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"\n需要采集的数据:")
    print(f"  目标日期: {yesterday} (昨天) ~ {today_str} (今天)")

    # 获取各表最新日期
    tables = {
        'stock_daily_prices': '股票日线',
        'sector_daily_prices': '板块日线',
        'etf_daily_prices': 'ETF日线',
        'stock_valuation_daily': '股票估值',
        'index_daily': '指数数据',
    }

    for table, desc in tables.items():
        try:
            df = pd.read_sql(f"SELECT MAX(trade_date) as max_date FROM {table}", conn)
            max_date = df.iloc[0]['max_date']

            if max_date:
                max_date_str = pd.to_datetime(max_date).strftime('%Y-%m-%d')
                days_missing = (pd.Timestamp.now() - pd.to_datetime(max_date)).days

                if days_missing > 3:
                    print(f"\n⚠️  {desc} ({table})")
                    print(f"     最新日期: {max_date_str}")
                    print(f"     缺失天数: 约 {days_missing} 天")
                elif days_missing > 0:
                    print(f"\n✓ {desc} ({table})")
                    print(f"   最新日期: {max_date_str}")
                    print(f"   缺失天数: 约 {days_missing} 天")
                else:
                    print(f"\n✅ {desc} ({table})")
                    print(f"   最新日期: {max_date_str} (最新)")
        except Exception as e:
            print(f"\n❌ {desc} ({table}): 检查失败 - {e}")

    conn.close()

if __name__ == "__main__":
    check_missing_dates()
