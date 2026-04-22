"""
pytdx 服务器池下载器
- 10 个服务器并发工作
- 每个服务器独立线程池
- 智能负载均衡
"""

import sys
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import pandas as pd
import random

# 添加 backend 到路径
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from pytdx.hq import TdxHq_API

DB_PATH = "data/local_quant.db"
DEFAULT_START_DATE = "2023-01-01"

# 服务器列表
TDX_SERVERS = [
    ("218.75.126.9", 7709),    # 招商证券上海
    ("119.147.212.81", 7709),  # 招商证券深圳
    ("101.71.170.10", 7709),   # 华泰证券
    ("218.108.98.244", 7709),  # 华泰证券
    ("218.108.61.19", 7709),   # 华泰证券
    ("114.80.63.12", 7709),    # 申万宏源
    ("114.80.63.33", 7709),    # 申万宏源
    ("39.106.218.146", 7709),  # 中信建投
    ("60.12.136.250", 7709),   # 中信建投
    ("120.76.152.87", 7709),   # 广发证券
]

MARKET_SH = 1
MARKET_SZ = 0
CATEGORY_DAILY = 4

# 全局锁
save_lock = Lock()


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def get_pending_stocks():
    """获取需要下载的股票"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df_all = pd.read_sql("SELECT symbol, name FROM stock_info", conn)
        df_has = pd.read_sql("SELECT DISTINCT symbol FROM stock_daily_prices", conn)

        if not df_has.empty:
            has_symbols = set(df_has['symbol'])
            df_pending = df_all[~df_all['symbol'].isin(has_symbols)]
        else:
            df_pending = df_all

        return df_pending['symbol'].tolist()
    finally:
        conn.close()


def parse_symbol(symbol: str):
    """解析股票代码"""
    code = str(symbol).zfill(6)
    if code.startswith(('6', '900', '688')):
        return MARKET_SH, code
    else:
        return MARKET_SZ, code


def download_from_server(symbols, server_info, start_date):
    """从指定服务器下载一批股票"""
    host, port = server_info
    api = TdxHq_API()

    if not api.connect(host, port):
        return []

    results = []
    for symbol in symbols:
        try:
            market, code = parse_symbol(symbol)

            all_data = []
            offset = 0

            while True:
                try:
                    data = api.get_security_bars(CATEGORY_DAILY, market, code, offset, 800)
                    if not data:
                        break
                    all_data.extend(data)
                    if len(data) < 800:
                        break
                    offset += 800
                    if offset > 10000:
                        break
                except:
                    break

            if not all_data:
                continue

            df = pd.DataFrame(all_data)
            df = df.rename(columns={
                'datetime': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'vol': 'volume',
                'amount': 'amount',
            })

            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            df['symbol'] = symbol

            if 'close' in df.columns:
                df['pct_chg'] = df['close'].pct_change() * 100

            df = df.sort_values('trade_date').reset_index(drop=True)

            # 过滤日期
            today = pd.Timestamp.now().date()
            df = df[df['trade_date'] <= today].copy()

            if start_date:
                start = pd.to_datetime(start_date).date()
                df = df[df['trade_date'] >= start]

            if not df.empty:
                if 'turnover_rate' not in df.columns:
                    df['turnover_rate'] = None
                results.append(df)

        except Exception:
            continue

    api.disconnect()
    return results


def save_batch(df_list):
    """批量保存"""
    if not df_list:
        return 0

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        saved = 0
        for df in df_list:
            if df is not None and not df.empty:
                try:
                    df.to_sql('stock_daily_prices', conn, if_exists='append', index=False,
                             method='multi', chunksize=1000)
                    saved += 1
                except:
                    pass
        conn.commit()
        return saved
    finally:
        conn.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="pytdx 服务器池下载器")
    parser.add_argument('--threads', type=int, default=5, help='每个服务器的线程数')
    parser.add_argument('--start', default=DEFAULT_START_DATE, help='开始日期')

    args = parser.parse_args()

    print("=" * 70)
    print("  🚀 pytdx 服务器池下载器")
    print("=" * 70)
    print(f"  服务器数: {len(TDX_SERVERS)}")
    print(f"  每服务器线程: {args.threads}")
    print(f"  总并发: {len(TDX_SERVERS) * args.threads}")
    print("=" * 70)
    print()

    init_db()

    pending_stocks = get_pending_stocks()
    print(f"✅ 需要下载 {len(pending_stocks)} 只股票")

    if not pending_stocks:
        print("没有需要下载的股票")
        return

    # 将股票分配给各个服务器
    chunk_size = (len(pending_stocks) + len(TDX_SERVERS) - 1) // len(TDX_SERVERS)
    server_chunks = []
    for i in range(0, len(pending_stocks), chunk_size):
        server_chunks.append(pending_stocks[i:i + chunk_size])

    # 确保每个服务器都有任务
    while len(server_chunks) < len(TDX_SERVERS):
        server_chunks.append([])

    # 为每个服务器分配任务
    tasks = []
    for i, server in enumerate(TDX_SERVERS):
        symbols = server_chunks[i] if i < len(server_chunks) else []
        if symbols:
            tasks.append((symbols, server))

    print(f"📊 已分配 {len(tasks)} 个服务器任务")
    print()

    success_count = 0
    total_count = 0

    # 使用线程池，每个任务使用不同的服务器
    with ThreadPoolExecutor(max_workers=len(TDX_SERVERS)) as executor:
        futures = {
            executor.submit(download_from_server, symbols, server, args.start): (server, len(symbols))
            for symbols, server in tasks
        }

        with tqdm(total=len(pending_stocks), desc="  下载进度", unit="只") as pbar:
            all_results = []
            for future in as_completed(futures):
                server, count = futures[future]
                try:
                    results = future.result()
                    all_results.extend(results)

                    # 每 100 只股票保存一次
                    with save_lock:
                        if len(all_results) >= 100:
                            saved = save_batch(all_results)
                            success_count += saved
                            if saved > 0:
                                pbar.write(f"💾 服务器 {server[0]}: 已保存 {saved} 只")
                            all_results = []

                    pbar.update(count)

                except Exception as e:
                    pbar.write(f"❌ 服务器 {server[0]} 失败: {e}")

        # 保存剩余
        if all_results:
            saved = save_batch(all_results)
            success_count += saved

    print()
    print("=" * 70)
    print("  ✅ 下载完成！")
    print("=" * 70)
    print(f"  成功: {success_count}")
    print(f"  总计: {len(pending_stocks)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
