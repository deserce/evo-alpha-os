"""
增量更新K线数据 - 使用 mootdx
更新今天的收盘数据
"""

import sys
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
import time

# 添加 backend 到路径
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

DB_PATH = "data/local_quant.db"


def get_last_date(conn, table_name):
    """获取表中最新交易日期"""
    try:
        df = pd.read_sql(f"SELECT MAX(trade_date) as max_date FROM {table_name}", conn)
        if df and not df.empty and df.iloc[0]['max_date']:
            return pd.to_datetime(df.iloc[0]['max_date']).date()
    except:
        pass
    return None


def update_stock_kline_mootdx():
    """使用mootdx更新个股K线"""
    print("\n" + "=" * 70)
    print("  更新个股 K线数据 (mootdx)")
    print("=" * 70)

    from mootdx.quotes import Quotes

    conn = sqlite3.connect(DB_PATH)

    last_date = get_last_date(conn, 'stock_daily_prices')
    today = datetime.now().date()

    if last_date:
        print(f"最后数据日期: {last_date}")

    # 获取股票列表（移除限制，更新所有股票）
    df_stocks = pd.read_sql("SELECT DISTINCT symbol FROM stock_info WHERE symbol NOT LIKE '9%'", conn)
    stocks = df_stocks['symbol'].tolist()

    print(f"需要更新 {len(stocks)} 只股票")

    api = Quotes.factory(market='std', server='218.75.126.9:7709', timeout=15)
    if api.client is None:
        print("❌ 无法连接")
        conn.close()
        return

    print("✅ 连接成功")

    success = 0
    new_records = 0

    for symbol in tqdm(stocks, desc="更新股票"):
        try:
            # 确定市场
            market = 0 if symbol.startswith('6') else 1

            # 获取最近的数据
            df = api.bars(symbol=symbol, frequency=9, start=0, offset=800, count=5, market=market)

            if df is not None and not df.empty:
                # 转换列名
                df = df.rename(columns={'datetime': 'trade_date'})

                # 转换日期
                if 'trade_date' in df.columns:
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

                df['symbol'] = symbol

                # 删除旧数据后插入
                for _, row in df.iterrows():
                    try:
                        conn.execute(
                            "DELETE FROM stock_daily_prices WHERE symbol = ? AND trade_date = ?",
                            [symbol, row['trade_date']]
                        )
                    except:
                        pass

                df.to_sql('stock_daily_prices', conn, if_exists='append', index=False)
                conn.commit()
                success += 1
                new_records += len(df)

        except Exception as e:
            pass

        time.sleep(0.02)

    conn.close()

    print(f"✅ 个股K线更新完成: 成功 {success}, 新增 {new_records} 条")


def update_etf_kline_mootdx():
    """使用mootdx更新ETF K线"""
    print("\n" + "=" * 70)
    print("  更新 ETF K线数据 (mootdx)")
    print("=" * 70)

    from mootdx.quotes import Quotes

    conn = sqlite3.connect(DB_PATH)

    last_date = get_last_date(conn, 'etf_daily_prices')
    today = datetime.now().date()

    if last_date:
        print(f"最后数据日期: {last_date}")

    # 获取ETF列表
    df_etf = pd.read_sql("SELECT DISTINCT symbol FROM etf_info", conn)
    etfs = df_etf['symbol'].tolist()

    print(f"需要更新 {len(etfs)} 只ETF")

    api = Quotes.factory(market='std', server='218.75.126.9:7709', timeout=15)
    if api.client is None:
        print("❌ 无法连接")
        conn.close()
        return

    print("✅ 连接成功")

    success = 0
    new_records = 0

    for symbol in tqdm(etfs, desc="更新ETF"):
        try:
            # ETF用上海市场
            df = api.bars(symbol=symbol, frequency=9, start=0, offset=800, count=5, market=0)

            if df is not None and not df.empty:
                df = df.rename(columns={'datetime': 'trade_date'})

                if 'trade_date' in df.columns:
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

                df['symbol'] = symbol

                # 删除重复
                for _, row in df.iterrows():
                    try:
                        conn.execute(
                            "DELETE FROM etf_daily_prices WHERE symbol = ? AND trade_date = ?",
                            [symbol, row['trade_date']]
                        )
                    except:
                        pass

                df.to_sql('etf_daily_prices', conn, if_exists='append', index=False)
                conn.commit()
                success += 1
                new_records += len(df)

        except Exception as e:
            pass

        time.sleep(0.02)

    conn.close()

    print(f"✅ ETF K线更新完成: 成功 {success}, 新增 {new_records} 条")


def update_sector_kline_mootdx():
    """使用mootdx更新板块K线"""
    print("\n" + "=" * 70)
    print("  更新板块 K线数据 (mootdx)")
    print("=" * 70)

    from mootdx.quotes import Quotes

    conn = sqlite3.connect(DB_PATH)

    last_date = get_last_date(conn, 'sector_daily_prices')

    if last_date:
        print(f"最后数据日期: {last_date}")

    # 获取板块列表
    df_sec = pd.read_sql("SELECT DISTINCT sector_name FROM sector_daily_prices LIMIT 100", conn)
    sectors = df_sec['sector_name'].tolist()

    print(f"测试更新 {len(sectors)} 个板块")

    api = Quotes.factory(market='std', server='218.75.126.9:7709', timeout=15)
    if api.client is None:
        print("❌ 无法连接")
        conn.close()
        return

    print("✅ 连接成功")

    success = 0
    new_records = 0

    for name in tqdm(sectors, desc="更新板块"):
        try:
            # 从板块名称反查代码（简化处理）
            # 这里我们用已知的概念板块代码范围880xxx
            stocks = api.stocks(market=1)
            sector_row = stocks[stocks['name'] == name]
            if sector_row.empty:
                continue

            code = sector_row.iloc[0]['code']

            df = api.index_bars(symbol=code, frequency=9, start=0, offset=800, count=5)

            if df is not None and not df.empty:
                df = df.rename(columns={'datetime': 'trade_date'})

                if 'trade_date' in df.columns:
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

                df['sector_name'] = name

                # 删除重复
                for _, row in df.iterrows():
                    try:
                        conn.execute(
                            "DELETE FROM sector_daily_prices WHERE sector_name = ? AND trade_date = ?",
                            [name, row['trade_date']]
                        )
                    except:
                        pass

                df.to_sql('sector_daily_prices', conn, if_exists='append', index=False)
                conn.commit()
                success += 1
                new_records += len(df)

        except Exception as e:
            pass

        time.sleep(0.02)

    conn.close()

    print(f"✅ 板块 K线更新完成: 成功 {success}, 新增 {new_records} 条")


if __name__ == "__main__":
    update_stock_kline_mootdx()
    update_etf_kline_mootdx()
    update_sector_kline_mootdx()

    print("\n" + "=" * 70)
    print("  所有数据更新完成！")
    print("=" * 70)
