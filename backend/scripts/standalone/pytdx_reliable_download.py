"""
pytdx 可靠下载器
- WAL 模式：避免数据库锁定
- 立即保存：每只股票下载后立即写入
- 进度可见：实时显示下载状态
- 断点续传：支持中断后继续
"""

import sys
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def init_db_wal():
    """初始化数据库，启用 WAL 模式"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-10000")  # 10MB cache
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
    """获取需要下载的股票列表"""
    conn = sqlite3.connect(DB_PATH)
    try:
        # 获取所有股票
        df_all = pd.read_sql("SELECT symbol, name FROM stock_info", conn)

        # 获取已有数据的股票
        df_has = pd.read_sql("""
            SELECT DISTINCT symbol FROM stock_daily_prices
        """, conn)

        # 筛选缺少数据的股票
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


def download_stock_kline(symbol, start_date):
    """下载单只股票K线"""
    api = TdxHq_API()

    # 随机选择服务器
    random.shuffle(TDX_SERVERS)
    connected = False
    for host, port in TDX_SERVERS:
        try:
            if api.connect(host, port):
                connected = True
                break
        except:
            continue

    if not connected:
        api.disconnect()
        return None

    try:
        market, code = parse_symbol(symbol)

        all_data = []
        offset = 0
        count = 800

        while True:
            data = api.get_security_bars(CATEGORY_DAILY, market, code, offset, count)
            if not data:
                break
            all_data.extend(data)
            if len(data) < count:
                break
            offset += count
            if offset > 10000:
                break

        api.disconnect()

        if not all_data:
            return None

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

        if 'turnover_rate' not in df.columns:
            df['turnover_rate'] = None

        columns = ['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']
        existing_columns = [c for c in columns if c in df.columns]
        df = df[existing_columns]

        return df

    except Exception as e:
        try:
            api.disconnect()
        except:
            pass
        return None


def save_to_db(df):
    """立即保存到数据库"""
    if df is None or df.empty:
        return False

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    try:
        df.to_sql('stock_daily_prices', conn, if_exists='append', index=False,
                 method='multi', chunksize=1000)
        conn.commit()
        return True
    except Exception as e:
        # 尝试单条插入
        try:
            for _, row in df.iterrows():
                conn.execute("""
                    INSERT OR IGNORE INTO stock_daily_prices
                    (symbol, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row.get('symbol'), row.get('trade_date'),
                    row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                    row.get('volume'), row.get('amount'), row.get('pct_chg'), row.get('turnover_rate')
                ))
            conn.commit()
            return True
        except:
            return False
    finally:
        conn.close()


def download_worker(stock, start_date):
    """工作线程"""
    symbol = stock['symbol']
    name = stock.get('name', '')

    try:
        df = download_stock_kline(symbol, start_date)
        if df is not None and not df.empty:
            # 立即保存
            if save_to_db(df):
                return {'status': 'success', 'symbol': symbol, 'count': len(df)}
            else:
                return {'status': 'fail', 'symbol': symbol, 'error': '保存失败'}
        else:
            return {'status': 'fail', 'symbol': symbol, 'error': '无数据'}
    except Exception as e:
        return {'status': 'fail', 'symbol': symbol, 'error': str(e)}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="pytdx 可靠下载器")
    parser.add_argument('--workers', type=int, default=10, help='并发线程数')
    parser.add_argument('--start', default=DEFAULT_START_DATE, help='开始日期')
    parser.add_argument('--limit', type=int, default=None, help='限制下载数量（测试用）')

    args = parser.parse_args()

    print("=" * 70)
    print("  🚀 pytdx 可靠下载器")
    print("=" * 70)
    print(f"  并发线程: {args.workers}")
    print(f"  开始日期: {args.start}")
    print("=" * 70)
    print()

    # 初始化数据库
    print("初始化数据库...")
    init_db_wal()

    # 获取待下载股票
    print("获取待下载股票列表...")
    pending_stocks = get_pending_stocks()
    print(f"✅ 需要下载 {len(pending_stocks)} 只股票")

    if args.limit:
        pending_stocks = pending_stocks[:args.limit]
        print(f"⚠️  测试模式：仅下载 {args.limit} 只")

    print()

    # 统计
    success_count = 0
    fail_count = 0

    # 使用线程池
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(download_worker, stock, args.start): stock for stock in pending_stocks}

        with tqdm(total=len(pending_stocks), desc="  下载进度", unit="只") as pbar:
            for future in as_completed(futures):
                try:
                    result = future.result()

                    if result['status'] == 'success':
                        success_count += 1
                        pbar.write(f"✅ {result['symbol']}: {result['count']} 条记录 (成功: {success_count})")
                    else:
                        fail_count += 1

                except Exception as e:
                    fail_count += 1

                pbar.update(1)

    print()
    print("=" * 70)
    print("  ✅ 下载完成！")
    print("=" * 70)
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")
    print(f"  总计: {len(pending_stocks)}")
    print("=" * 70)

    # 最终统计
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT symbol) FROM stock_daily_prices")
    total_stocks = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM stock_daily_prices")
    total_records = cursor.fetchone()[0]
    conn.close()

    print()
    print(f"📊 数据库状态: {total_stocks} 只股票, {total_records:,} 条记录")


if __name__ == "__main__":
    main()
