"""
ETF K线下载器 - 使用 pytdx
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

from data_job.core.tdx_adapter import TdxAdapter

DB_PATH = "data/local_quant.db"
DEFAULT_START_DATE = "2023-01-01"


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def download_etf_kline(adapter, start_date=DEFAULT_START_DATE):
    """下载 ETF K线数据"""
    print("\n" + "=" * 70)
    print("  下载 ETF K线数据 (pytdx)")
    print("=" * 70)

    conn = init_db()

    # 获取 ETF 列表
    df_etf_list = pd.read_sql("SELECT symbol, name FROM etf_info ORDER BY symbol", conn)

    if df_etf_list.empty:
        print("⚠️  ETF 列表为空")
        conn.close()
        return

    print(f"共 {len(df_etf_list)} 只 ETF")

    # 获取已有的 ETF
    try:
        df_has = pd.read_sql("SELECT DISTINCT symbol FROM etf_daily_prices", conn)
        has_symbols = set(df_has['symbol'].tolist()) if not df_has.empty else set()
    except:
        has_symbols = set()

    pending = df_etf_list[~df_etf_list['symbol'].isin(has_symbols)]

    if pending.empty:
        print("✅ 所有 ETF 数据已下载")
        conn.close()
        return

    print(f"需要下载 {len(pending)} 只 ETF")

    success = 0
    fail = 0
    errors = []

    for idx, row in tqdm(pending.iterrows(), total=len(pending), desc="下载进度"):
        symbol = row['symbol']
        name = row['name']

        try:
            # ETF 使用 stock 方法获取
            df = adapter.get_stock_kline(symbol, start_date=start_date)

            if df is not None and not df.empty:
                # 保存数据
                df.to_sql('etf_daily_prices_tmp', conn, if_exists='replace', index=False)
                conn.execute("INSERT OR IGNORE INTO etf_daily_prices SELECT * FROM etf_daily_prices_tmp")
                conn.execute("DROP TABLE etf_daily_prices_tmp")
                conn.commit()
                success += 1
            else:
                fail += 1
                errors.append(f"{symbol}({name}): 无数据")
        except Exception as e:
            fail += 1
            errors.append(f"{symbol}({name}): {str(e)[:50]}")

        time.sleep(0.05)

    conn.close()

    print(f"\n✅ ETF K线下载完成: 成功 {success}, 失败 {fail}")

    if errors:
        print(f"\n失败列表 (最多显示10个):")
        for err in errors[:10]:
            print(f"  - {err}")


def download_index_kline(adapter, start_date=DEFAULT_START_DATE):
    """下载指数 K线数据"""
    print("\n" + "=" * 70)
    print("  下载指数 K线数据 (pytdx)")
    print("=" * 70)

    conn = init_db()

    # 主要指数列表
    index_list = [
        {"code": "000001", "name": "上证指数"},
        {"code": "399001", "name": "深证成指"},
        {"code": "399006", "name": "创业板指"},
        {"code": "000300", "name": "沪深300"},
        {"code": "000905", "name": "中证500"},
        {"code": "000852", "name": "中证1000"},
    ]

    # 检查 index_daily_prices 表是否存在
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='index_daily_prices'")
    table_exists = cursor.fetchone() is not None

    if not table_exists:
        conn.execute("""
            CREATE TABLE index_daily_prices (
                symbol VARCHAR(20),
                trade_date DATE,
                open FLOAT,
                close FLOAT,
                high FLOAT,
                low FLOAT,
                volume FLOAT,
                amount FLOAT,
                pct_chg FLOAT,
                PRIMARY KEY (symbol, trade_date)
            );
        """)
        conn.commit()

    # 获取已有的指数
    try:
        df_has = pd.read_sql("SELECT DISTINCT symbol FROM index_daily_prices", conn)
        has_symbols = set(df_has['symbol'].tolist()) if not df_has.empty else set()
    except:
        has_symbols = set()

    pending = [idx for idx in index_list if idx['code'] not in has_symbols]

    if not pending:
        print("✅ 所有指数数据已下载")
        conn.close()
        return

    print(f"需要下载 {len(pending)} 个指数")

    success = 0
    fail = 0

    for idx in tqdm(pending, desc="下载进度"):
        code = idx['code']
        name = idx['name']

        try:
            df = adapter.get_index_kline(code, start_date=start_date)

            if df is not None and not df.empty:
                df.to_sql('index_daily_prices_tmp', conn, if_exists='replace', index=False)
                conn.execute("INSERT OR IGNORE INTO index_daily_prices SELECT * FROM index_daily_prices_tmp")
                conn.execute("DROP TABLE index_daily_prices_tmp")
                conn.commit()
                success += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1

        time.sleep(0.05)

    conn.close()

    print(f"\n✅ 指数 K线下载完成: 成功 {success}, 失败 {fail}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="K线下载器 - 使用 pytdx")
    parser.add_argument('--etf', action='store_true', help='下载 ETF K线')
    parser.add_argument('--index', action='store_true', help='下载指数 K线')
    parser.add_argument('--all', action='store_true', help='下载所有数据')
    parser.add_argument('--start', default=DEFAULT_START_DATE, help='开始日期')

    args = parser.parse_args()

    print("=" * 70)
    print("  K线下载器 - pytdx")
    print("=" * 70)
    print(f"  开始日期: {args.start}")
    print("=" * 70)

    # 创建适配器
    adapter = TdxAdapter()

    if not adapter.is_available():
        print("⚠️  pytdx 连接失败")
        return

    print("✅ pytdx 已连接")

    try:
        if args.all or args.etf:
            download_etf_kline(adapter, args.start)

        if args.all or args.index:
            download_index_kline(adapter, args.start)

        if not (args.all or args.etf or args.index):
            print("\n请指定要下载的数据类型:")
            print("  --all   下载所有数据")
            print("  --etf   下载 ETF K线")
            print("  --index 下载指数 K线")

    finally:
        adapter.disconnect()

    print("\n" + "=" * 70)
    print("  下载任务完成！")
    print("=" * 70)


if __name__ == "__main__":
    main()
