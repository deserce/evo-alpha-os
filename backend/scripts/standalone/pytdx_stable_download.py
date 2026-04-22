"""
pytdx 稳定高速下载器
- 适中并发：20-30 线程（平衡速度与稳定性）
- 智能重试：失败自动重试
- 连接复用：每个线程保持长连接
- 批量保存：提高写入效率
"""

import sys
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, local
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
    ("218.75.126.9", 7709),
    ("119.147.212.81", 7709),
    ("101.71.170.10", 7709),
    ("218.108.98.244", 7709),
    ("218.108.61.19", 7709),
    ("114.80.63.12", 7709),
    ("114.80.63.33", 7709),
    ("39.106.218.146", 7709),
    ("60.12.136.250", 7709),
    ("120.76.152.87", 7709),
]

MARKET_SH = 1
MARKET_SZ = 0
CATEGORY_DAILY = 4

# 线程本地存储
thread_local = local()
# 数据锁
save_lock = Lock()
pending_save = []


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily_prices (
            symbol VARCHAR(20),
            trade_date DATE,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            turnover_rate FLOAT,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    conn.commit()
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

        return df_pending.to_dict('records')
    finally:
        conn.close()


def parse_symbol(symbol: str):
    """解析股票代码"""
    code = str(symbol).zfill(6)
    if code.startswith(('6', '900', '688')):
        return MARKET_SH, code
    else:
        return MARKET_SZ, code


def get_thread_api():
    """获取当前线程的 API 连接"""
    if not hasattr(thread_local, 'api') or thread_local.api is None:
        api = TdxHq_API()
        servers = TDX_SERVERS.copy()
        random.shuffle(servers)

        for host, port in servers:
            try:
                if api.connect(host, port):
                    thread_local.api = api
                    return api
            except:
                continue
        thread_local.api = None
        return None

    # 测试连接
    try:
        thread_local.api.get_security_quotes([(1, '000001')])
        return thread_local.api
    except:
        thread_local.api = None
        return get_thread_api()


def download_stock(symbol, start_date, retry=3):
    """下载单只股票（带重试）"""
    for attempt in range(retry):
        api = get_thread_api()
        if api is None:
            time.sleep(0.1)
            continue

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

            if df.empty:
                return None

            if 'turnover_rate' not in df.columns:
                df['turnover_rate'] = None

            columns = ['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']
            df = df[[c for c in columns if c in df.columns]]

            return df

        except Exception as e:
            if attempt < retry - 1:
                time.sleep(random.uniform(0.05, 0.15))
                continue

    return None


def save_batch(df_list):
    """批量保存"""
    if not df_list:
        return

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        for df in df_list:
            if df is not None and not df.empty:
                df.to_sql('stock_daily_prices', conn, if_exists='append', index=False,
                         method='multi', chunksize=1000)
        conn.commit()
    except:
        # 单条保存
        for df in df_list:
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO stock_daily_prices
                            (symbol, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            row.get('symbol'), row.get('trade_date'),
                            row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                            row.get('volume'), row.get('amount'), row.get('pct_chg'), row.get('turnover_rate')
                        ))
                    except:
                        pass
                conn.commit()
    finally:
        conn.close()


def download_worker(stock, start_date):
    """工作线程"""
    symbol = stock['symbol']
    df = download_stock(symbol, start_date, retry=2)

    if df is not None and not df.empty:
        return {'status': 'success', 'symbol': symbol, 'data': df}
    else:
        return {'status': 'fail', 'symbol': symbol}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="pytdx 稳定高速下载器")
    parser.add_argument('--workers', type=int, default=25, help='并发线程数')
    parser.add_argument('--start', default=DEFAULT_START_DATE, help='开始日期')
    parser.add_argument('--batch', type=int, default=100, help='批量保存大小')

    args = parser.parse_args()

    print("=" * 70)
    print("  🚀 pytdx 稳定高速下载器")
    print("=" * 70)
    print(f"  并发线程: {args.workers}")
    print(f"  批量大小: {args.batch}")
    print(f"  优化: 连接复用 + 智能重试")
    print("=" * 70)
    print()

    init_db()

    pending_stocks = get_pending_stocks()
    print(f"✅ 需要下载 {len(pending_stocks)} 只股票")
    print()

    success_count = 0
    fail_count = 0
    current_batch = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(download_worker, stock, args.start): stock for stock in pending_stocks}

        with tqdm(total=len(pending_stocks), desc="  下载进度", unit="只") as pbar:
            for future in as_completed(futures):
                try:
                    result = future.result()

                    if result['status'] == 'success':
                        with save_lock:
                            current_batch.append(result['data'])
                            success_count += 1

                            # 批量保存
                            if len(current_batch) >= args.batch:
                                save_batch(current_batch)
                                pbar.write(f"💾 已保存 {len(current_batch)} 只股票 (成功: {success_count})")
                                current_batch = []
                    else:
                        fail_count += 1

                except Exception:
                    fail_count += 1

                pbar.update(1)

    # 保存剩余
    if current_batch:
        save_batch(current_batch)

    print()
    print("=" * 70)
    print("  ✅ 下载完成！")
    print("=" * 70)
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")
    print(f"  总计: {len(pending_stocks)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
