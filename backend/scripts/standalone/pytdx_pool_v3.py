"""
pytdx 服务器池下载器 v3 - 超高速版
- 每个服务器 10 线程
- 总并发：18 服务器 × 10 线程 = 180 并发
- 立即保存 + 断点续传
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

# 添加 backend 到路径
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from pytdx.hq import TdxHq_API

DB_PATH = "data/local_quant.db"
DEFAULT_START_DATE = "2023-01-01"

# 服务器列表
TDX_SERVERS = [
    ("218.75.126.9", 7709),     # 招商证券上海 ✅
    ("119.147.212.81", 7709),   # 招商证券深圳
    ("101.71.170.10", 7709),    # 华泰证券
    ("218.108.98.244", 7709),   # 华泰证券
    ("218.108.61.19", 7709),    # 华泰证券
    ("114.80.63.12", 7709),     # 申万宏源
    ("114.80.63.33", 7709),     # 申万宏源
    ("39.106.218.146", 7709),   # 中信建投
    ("60.12.136.250", 7709),    # 中信建投 ✅
    ("120.76.152.87", 7709),    # 广发证券
    ("61.49.50.180", 7709),     # 国信证券
    ("61.49.50.181", 7709),     # 国信证券
    ("59.173.18.69", 7709),     # 银河证券
    ("59.173.18.140", 7709),    # 银河证券
    ("47.103.86.229", 7709),    # 中信证券
    ("47.103.84.187", 7709),    # 中信证券
    ("61.144.194.9", 7709),     # 平安证券
    ("61.144.194.8", 7709),     # 平安证券
]

MARKET_SH = 1
MARKET_SZ = 0
CATEGORY_DAILY = 4

# 全局锁
save_lock = Lock()
success_count = 0


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


def save_immediate(df):
    """立即保存单只股票数据"""
    if df is None or df.empty:
        return False

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        df.to_sql('stock_daily_prices', conn, if_exists='append', index=False)
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def download_stock(symbol, start_date, server_info):
    """从指定服务器下载单只股票"""
    host, port = server_info
    api = TdxHq_API()

    if not api.connect(host, port):
        return None

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

        api.disconnect()

        if not all_data:
            return None

        df = pd.DataFrame(all_data)
        df = df.rename(columns={
            'datetime': 'trade_date',
            'open': 'open', 'close': 'close',
            'high': 'high', 'low': 'low',
            'vol': 'volume', 'amount': 'amount',
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

        if df.empty:
            return None

        if 'turnover_rate' not in df.columns:
            df['turnover_rate'] = None

        columns = ['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']
        df = df[[c for c in columns if c in df.columns]]

        return df

    except Exception:
        try:
            api.disconnect()
        except:
            pass
        return None


def download_worker(symbols_chunk, server_info, start_date):
    """工作线程 - 处理一批股票"""
    for symbol in symbols_chunk:
        df = download_stock(symbol, start_date, server_info)

        if df is not None and not df.empty:
            if save_immediate(df):
                with save_lock:
                    global success_count
                    success_count += 1

    return len(symbols_chunk)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="pytdx 服务器池下载器 v3 - 超高速版")
    parser.add_argument('--threads_per_server', type=int, default=10, help='每个服务器的线程数')
    parser.add_argument('--start', default=DEFAULT_START_DATE, help='开始日期')

    args = parser.parse_args()

    total_threads = len(TDX_SERVERS) * args.threads_per_server

    print("=" * 70)
    print("  🚀 pytdx 服务器池下载器 v3 - 超高速版")
    print("=" * 70)
    print(f"  服务器数: {len(TDX_SERVERS)}")
    print(f"  每服务器线程: {args.threads_per_server}")
    print(f"  总并发: {total_threads}")
    print("=" * 70)
    print()

    init_db()

    pending_stocks = get_pending_stocks()
    print(f"✅ 需要下载 {len(pending_stocks)} 只股票")

    if not pending_stocks:
        print("没有需要下载的股票")
        return

    # 将股票分配给各个服务器的各个线程
    chunk_size = max(1, len(pending_stocks) // (len(TDX_SERVERS) * args.threads_per_server))
    server_tasks = []

    for server_idx, server in enumerate(TDX_SERVERS):
        for thread_idx in range(args.threads_per_server):
            start_idx = (server_idx * args.threads_per_server + thread_idx) * chunk_size
            if start_idx >= len(pending_stocks):
                break
            end_idx = min(start_idx + chunk_size, len(pending_stocks))
            chunk = pending_stocks[start_idx:end_idx]
            if chunk:
                server_tasks.append((chunk, server))

    print(f"📊 已分配 {len(server_tasks)} 个任务")
    print()

    # 使用线程池
    with ThreadPoolExecutor(max_workers=total_threads) as executor:
        futures = {}
        for symbols, server in server_tasks:
            future = executor.submit(download_worker, symbols, server, args.start)
            futures[future] = (len(symbols), server)

        with tqdm(total=len(pending_stocks), desc="  下载进度", unit="只") as pbar:
            completed = 0
            for future in as_completed(futures):
                try:
                    chunk_size, server = futures[future]
                    result = future.result()
                    completed += result
                    pbar.update(result)
                except Exception as e:
                    pbar.write(f"❌ {server[0]}: {e}")
                    chunk_size, _ = futures[future]
                    pbar.update(chunk_size)

    print()
    print("=" * 70)
    print("  ✅ 下载完成！")
    print("=" * 70)
    print(f"  成功: {success_count}")
    print("=" * 70)


if __name__ == "__main__":
    main()
