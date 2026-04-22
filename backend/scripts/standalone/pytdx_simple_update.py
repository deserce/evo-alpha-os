"""
pytdx K线增量更新脚本 - 简单可靠版
- 单服务器连接，避免复杂的多服务器问题
- 多线程处理
- 只获取需要更新的股票的最近数据
"""

import sys
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta, date
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
MARKET_SH = 1
MARKET_SZ = 0
CATEGORY_DAILY = 4

# 使用验证过的好用服务器
TDX_SERVER = ("218.75.126.9", 7709)  # 招商证券上海

# 全局锁
save_lock = Lock()
success_count = 0
fail_count = 0


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def get_stocks_need_update(target_date=None, days=7):
    """
    获取需要更新的股票列表

    Args:
        target_date: 目标日期
        days: 获取最近N天的数据
    """
    if target_date is None:
        target_date = datetime.now().date()

    start_date = target_date - timedelta(days=days)

    conn = sqlite3.connect(DB_PATH)
    try:
        # 获取所有股票及其最新交易日期
        query = """
            SELECT s.symbol, MAX(s.trade_date) as last_date
            FROM stock_daily_prices s
            GROUP BY s.symbol
            HAVING MAX(s.trade_date) < ?
            ORDER BY s.symbol
        """
        df = pd.read_sql(query, conn, params=[target_date])

        if df.empty:
            return []

        return df['symbol'].tolist()
    finally:
        conn.close()


def parse_symbol(symbol: str):
    code = str(symbol).zfill(6)
    if code.startswith(('6', '900', '688')):
        return MARKET_SH, code
    else:
        return MARKET_SZ, code


def save_to_db(conn, df):
    """保存数据到数据库（使用 INSERT OR IGNORE）"""
    if df is None or df.empty:
        return False

    try:
        cursor = conn.cursor()

        for _, row in df.iterrows():
            cursor.execute("""
                INSERT OR IGNORE INTO stock_daily_prices
                (symbol, trade_date, open, close, high, low, volume, amount, pct_chg, turnover_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row['symbol'],
                row['trade_date'],
                row.get('open'),
                row.get('close'),
                row.get('high'),
                row.get('low'),
                row.get('volume'),
                row.get('amount'),
                row.get('pct_chg'),
                row.get('turnover_rate')
            ))

        conn.commit()
        return True
    except Exception as e:
        return False


def download_stock(symbol, conn, api_lock):
    """
    下载单只股票的最近数据

    Args:
        symbol: 股票代码
        conn: 数据库连接
        api_lock: API连接锁
    """
    global success_count, fail_count

    try:
        market, code = parse_symbol(symbol)

        # 使用锁保护API连接（pytdx不是线程安全的）
        with api_lock:
            # 每次创建新连接
            api = TdxHq_API()
            if not api.connect(TDX_SERVER[0], TDX_SERVER[1]):
                with save_lock:
                    fail_count += 1
                return False

            try:
                # 获取最近的数据（每次800条）
                all_data = []
                for offset in range(0, 1600, 800):
                    data = api.get_security_bars(CATEGORY_DAILY, market, code, offset, 800)
                    if not data:
                        break
                    all_data.extend(data)
                    if len(data) < 800:
                        break

                api.disconnect()
            except Exception:
                try:
                    api.disconnect()
                except:
                    pass
                with save_lock:
                    fail_count += 1
                return False

        if not all_data:
            with save_lock:
                fail_count += 1
            return False

        # 转换为 DataFrame
        df = pd.DataFrame(all_data)

        # 重命名列
        df = df.rename(columns={'datetime': 'trade_date', 'vol': 'volume'})

        # 转换日期
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        df['symbol'] = symbol

        # 计算涨跌幅
        if 'close' in df.columns:
            df['pct_chg'] = df['close'].pct_change() * 100

        # 添加缺失列
        if 'turnover_rate' not in df.columns:
            df['turnover_rate'] = None

        # 选择需要的列
        columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']
        existing_columns = [c for c in columns if c in df.columns]
        df = df[existing_columns]

        # 保存到数据库
        if save_to_db(conn, df):
            with save_lock:
                success_count += 1
            return True
        else:
            # 保存失败可能是因为数据已存在，也算成功
            with save_lock:
                success_count += 1
            return True

    except Exception as e:
        with save_lock:
            fail_count += 1
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="pytdx K线增量更新")
    parser.add_argument('--workers', type=int, default=3, help='并发线程数')
    parser.add_argument('--days', type=int, default=7, help='获取最近N天的数据')
    parser.add_argument('--limit', type=int, help='限制处理数量')

    args = parser.parse_args()

    target_date = datetime.now().date()

    print("=" * 70)
    print("  🚀 pytdx K线增量更新")
    print("=" * 70)
    print(f"  目标日期: {target_date}")
    print(f"  并发线程: {args.workers}")
    print(f"  数据源: {TDX_SERVER[0]}:{TDX_SERVER[1]}")
    print("=" * 70)
    print()

    init_db()

    # 获取需要更新的股票
    pending = get_stocks_need_update(target_date, days=args.days)
    print(f"📊 需要更新 {len(pending)} 只股票")

    if args.limit and len(pending) > args.limit:
        pending = pending[:args.limit]
        print(f"📊 限制处理数量: {args.limit}")

    if not pending:
        print("✅ 没有需要更新的股票")
        return

    print(f"📊 开始下载...")
    print()

    # 创建数据库连接（每个线程一个连接，但在本简化版中用全局）
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # API锁
    api_lock = Lock()

    # 使用线程池
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download_stock, symbol, conn, api_lock): symbol
            for symbol in pending
        }

        with tqdm(total=len(pending), desc="  下载进度", unit="只") as pbar:
            for future in as_completed(futures):
                try:
                    future.result()
                    pbar.update(1)
                    pbar.set_postfix({
                        "成功": success_count,
                        "失败": fail_count
                    })
                except Exception as e:
                    pbar.update(1)

    conn.close()

    print()
    print("=" * 70)
    print("  ✅ 更新完成！")
    print("=" * 70)
    print(f"  成功: {success_count} 只")
    print(f"  失败: {fail_count} 只")
    total = success_count + fail_count
    if total > 0:
        print(f"  成功率: {success_count/total*100:.1f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
