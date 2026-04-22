"""
增量更新K线数据 - 更新今天的收盘数据
使用 mootdx/pytdx 更新股票和ETF的最新K线
"""

import sys
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm
import time

# 添加 backend 到路径
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

DB_PATH = "data/local_quant.db"

def get_last_date(conn, table_name, symbol_column='symbol'):
    """获取表中最新交易日期"""
    try:
        df = pd.read_sql(f"""
            SELECT MAX(trade_date) as max_date FROM {table_name}
        """, conn)
        if df and not df.empty and df.iloc[0]['max_date']:
            return pd.to_datetime(df.iloc[0]['max_date']).date()
    except:
        pass
    return None

def update_stock_kline():
    """更新个股K线"""
    print("\n" + "=" * 70)
    print("  更新个股 K线数据")
    print("=" * 70)

    from pytdx.hq import TdxHq_API

    conn = sqlite3.connect(DB_PATH)

    # 获取最新日期
    last_date = get_last_date(conn, 'stock_daily_prices')
    today = datetime.now().date()

    if last_date:
        print(f"最后数据日期: {last_date}")
        if last_date >= today:
            print("✅ 数据已是最新")
            conn.close()
            return

    # 获取股票列表
    df_stocks = pd.read_sql("SELECT DISTINCT symbol FROM stock_info WHERE symbol NOT LIKE '9%'", conn)
    stocks = df_stocks['symbol'].tolist()

    print(f"需要更新 {len(stocks)} 只股票")

    # pytdx服务器
    servers = [
        ('218.75.126.9', 7709),
        ('119.147.212.81', 7709),
    ]

    api = TdxHq_API()
    connected = False
    for host, port in servers:
        try:
            if api.connect(host, port, time_out=10):
                print(f"✅ 连接成功: {host}:{port}")
                connected = True
                break
        except:
            continue

    if not connected:
        print("❌ 无法连接通达信服务器")
        conn.close()
        return

    success = 0
    fail = 0

    # 分批处理
    batch_size = 800
    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i+batch_size]

        try:
            # 获取日线数据
            dfs = []
            for symbol in batch:
                try:
                    # 6开头沪市，其他深市
                    market = 1 if symbol.startswith('6') else 0
                    data = api.get_security_bars(9, market, symbol, 0, 100)  # 获取最近100天

                    if data:
                        df = pd.DataFrame(data)
                        df['symbol'] = symbol
                        dfs.append(df)
                except:
                    pass

            if dfs:
                df_combined = pd.concat(dfs, ignore_index=True)

                # 转换列名
                df_combined = df_combined.rename(columns={
                    'datetime': 'trade_date',
                    'amount': 'amount',
                    'vol': 'volume'
                })

                # 转换日期
                if 'trade_date' in df_combined.columns:
                    df_combined['trade_date'] = pd.to_datetime(df_combined['trade_date']).dt.date

                # 只保留最新日期之后的数据
                if last_date:
                    df_combined = df_combined[df_combined['trade_date'] > last_date]

                if not df_combined.empty:
                    # 去重
                    df_combined = df_combined.drop_duplicates(subset=['symbol', 'trade_date'])

                    # 保存
                    df_combined.to_sql('stock_daily_prices', conn, if_exists='append', index=False)
                    conn.commit()
                    success += len(df_combined['symbol'].unique())

        except Exception as e:
            fail += len(batch)

        time.sleep(0.1)

    api.disconnect()
    conn.close()

    print(f"✅ 个股K线更新完成: 成功 {success}, 失败 {fail}")


def update_etf_kline():
    """更新ETF K线"""
    print("\n" + "=" * 70)
    print("  更新 ETF K线数据")
    print("=" * 70)

    from pytdx.hq import TdxHq_API

    conn = sqlite3.connect(DB_PATH)

    # 获取最新日期
    last_date = get_last_date(conn, 'etf_daily_prices')
    today = datetime.now().date()

    if last_date:
        print(f"最后数据日期: {last_date}")
        if last_date >= today:
            print("✅ 数据已是最新")
            conn.close()
            return

    # 获取ETF列表
    df_etf = pd.read_sql("SELECT DISTINCT symbol FROM etf_info WHERE symbol LIKE '5%' OR symbol LIKE '15%'", conn)
    etfs = df_etf['symbol'].tolist()

    print(f"需要更新 {len(etfs)} 只ETF")

    # pytdx服务器
    api = TdxHq_API()

    if not api.connect('218.75.126.9', 7709, time_out=10):
        print("❌ 无法连接通达信服务器")
        conn.close()
        return

    print("✅ 连接成功")

    success = 0
    fail = 0

    for symbol in tqdm(etfs, desc="更新ETF"):
        try:
            # ETF使用上海市场(1)
            data = api.get_security_bars(9, 1, symbol, 0, 10)

            if data:
                df = pd.DataFrame(data)
                df['symbol'] = symbol

                # 转换列名
                df = df.rename(columns={
                    'datetime': 'trade_date',
                    'vol': 'volume'
                })

                # 转换日期
                if 'trade_date' in df.columns:
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

                # 只保留最新日期之后的数据
                if last_date:
                    df = df[df['trade_date'] > last_date]

                if not df.empty:
                    # 添加amount列
                    if 'amount' not in df.columns and 'volume' in df.columns and 'close' in df.columns:
                        df['amount'] = df['volume'] * df['close']

                    # 去重
                    df = df.drop_duplicates(subset=['symbol', 'trade_date'])

                    # 保存
                    df.to_sql('etf_daily_prices', conn, if_exists='append', index=False)
                    conn.commit()
                    success += 1

        except Exception as e:
            fail += 1

        time.sleep(0.05)

    api.disconnect()
    conn.close()

    print(f"✅ ETF K线更新完成: 成功 {success}, 失败 {fail}")


def update_index_kline():
    """更新指数K线"""
    print("\n" + "=" * 70)
    print("  更新指数 K线数据")
    print("=" * 70)

    from pytdx.hq import TdxHq_API

    conn = sqlite3.connect(DB_PATH)

    # 主要指数代码
    indices = [
        ('000001', 1, '上证指数'),
        ('399001', 0, '深证成指'),
        ('399006', 0, '创业板指'),
        ('000300', 1, '沪深300'),
    ]

    print(f"需要更新 {len(indices)} 个指数")

    api = TdxHq_API()

    if not api.connect('218.75.126.9', 7709, time_out=10):
        print("❌ 无法连接通达信服务器")
        conn.close()
        return

    print("✅ 连接成功")

    success = 0

    for code, market, name in indices:
        try:
            data = api.get_security_bars(9, market, code, 0, 10)

            if data:
                df = pd.DataFrame(data)
                df['symbol'] = code

                # 转换列名
                df = df.rename(columns={
                    'datetime': 'trade_date',
                    'vol': 'volume'
                })

                # 转换日期
                if 'trade_date' in df.columns:
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

                # 删除旧数据后插入新数据
                df = df.drop_duplicates(subset=['symbol', 'trade_date'])
                conn.execute(f"DELETE FROM index_daily_prices WHERE symbol = '{code}' AND trade_date >= '2026-03-01'")
                df.to_sql('index_daily_prices', conn, if_exists='append', index=False)
                conn.commit()
                success += 1
                print(f"  ✅ {name} ({code}): 更新成功")

        except Exception as e:
            print(f"  ❌ {name} ({code}): {e}")

        time.sleep(0.05)

    api.disconnect()
    conn.close()

    print(f"✅ 指数 K线更新完成: 成功 {success}/{len(indices)}")


if __name__ == "__main__":
    update_stock_kline()
    update_etf_kline()
    update_index_kline()

    print("\n" + "=" * 70)
    print("  所有数据更新完成！")
    print("=" * 70)
