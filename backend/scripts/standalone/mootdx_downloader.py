"""
mootdx K线下载器 - 稳定高速版
- 使用 mootdx 自动选择最快服务器
- 支持断点续传
- 立即保存防止数据丢失
- 支持黑名单过滤
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import pandas as pd
import json

# 添加 backend 到路径
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from data_job.core.mootdx_adapter import MootdxAdapter

DB_PATH = "data/local_quant.db"
DEFAULT_START_DATE = "2023-01-01"
BLACKLIST_FILE = "data/blacklist.json"


class DownloadStats:
    """下载统计"""
    def __init__(self):
        self.success = 0
        self.fail = 0
        self.lock = Lock()

    def add_success(self):
        with self.lock:
            self.success += 1

    def add_fail(self):
        with self.lock:
            self.fail += 1


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def get_blacklist():
    """获取黑名单"""
    try:
        with open(BLACKLIST_FILE, 'r') as f:
            return set(json.load(f))
    except:
        return set()


def get_pending_stocks():
    """获取需要下载的股票（过滤不支持的代码和黑名单）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df_all = pd.read_sql("SELECT symbol, name FROM stock_info", conn)
        df_has = pd.read_sql("SELECT DISTINCT symbol FROM stock_daily_prices", conn)

        if not df_has.empty:
            has_symbols = set(df_has['symbol'])
            df_pending = df_all[~df_all['symbol'].isin(has_symbols)]
        else:
            df_pending = df_all

        # 过滤：只支持 0、2、3、6 开头的股票
        def is_supported(symbol):
            return symbol.startswith(('0', '2', '3', '6'))

        df_pending = df_pending[df_pending['symbol'].apply(is_supported)]

        # 过滤黑名单
        blacklist = get_blacklist()
        df_pending = df_pending[~df_pending['symbol'].isin(blacklist)]

        return df_pending['symbol'].tolist()
    finally:
        conn.close()


def save_immediate(df):
    """立即保存单只股票数据（去重）"""
    if df is None or df.empty:
        return False

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        # 使用 INSERT OR IGNORE 避免重复
        df.to_sql('stock_daily_prices_tmp', conn, if_exists='replace', index=False)
        conn.execute("""
            INSERT OR IGNORE INTO stock_daily_prices
            SELECT * FROM stock_daily_prices_tmp
        """)
        conn.execute("DROP TABLE stock_daily_prices_tmp")
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def download_stock(symbol, start_date, adapter, stats):
    """下载单只股票并保存"""
    try:
        df = adapter.get_stock_kline(symbol, start_date=start_date)

        if df is not None and not df.empty:
            if save_immediate(df):
                stats.add_success()
                return True
            else:
                stats.add_fail()
                return False
        else:
            stats.add_fail()
            return False
    except Exception:
        stats.add_fail()
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="mootdx K线下载器 - 稳定高速版")
    parser.add_argument('--workers', type=int, default=1, help='并发下载线程数')
    parser.add_argument('--start', default=DEFAULT_START_DATE, help='开始日期')
    parser.add_argument('--symbol', help='指定单只股票代码')

    args = parser.parse_args()

    print("=" * 70)
    print("  mootdx K线下载器 - 稳定高速版")
    print("=" * 70)
    print(f"  并发线程: {args.workers}")
    print(f"  开始日期: {args.start}")
    print("=" * 70)
    print()

    init_db()

    # 如果指定了单只股票
    if args.symbol:
        pending_stocks = [args.symbol]
        print(f"下载指定股票: {args.symbol}")
    else:
        pending_stocks = get_pending_stocks()
        print(f"需要下载 {len(pending_stocks)} 只股票")

    if not pending_stocks:
        print("没有需要下载的股票")
        return

    # 创建适配器
    adapter = MootdxAdapter(timeout=15)
    if not adapter.is_available():
        print("mootdx 连接失败，请检查网络")
        return

    print(f"已连接到服务器")
    print()

    stats = DownloadStats()

    # 单线程下载
    if args.workers == 1:
        with tqdm(total=len(pending_stocks), desc="下载进度", unit="只") as pbar:
            for symbol in pending_stocks:
                download_stock(symbol, args.start, adapter, stats)
                pbar.set_postfix({"成功": stats.success})
                pbar.update(1)

    # 多线程下载
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(download_stock, s, args.start, adapter, stats): s
                for s in pending_stocks
            }

            with tqdm(total=len(pending_stocks), desc="下载进度", unit="只") as pbar:
                for future in as_completed(futures):
                    try:
                        future.result()
                        pbar.update(1)
                        pbar.set_postfix({"成功": stats.success, "失败": stats.fail})
                    except Exception as e:
                        pbar.write(f"错误: {e}")
                        pbar.update(1)

    adapter.disconnect()

    print()
    print("=" * 70)
    print("  下载完成！")
    print("=" * 70)
    print(f"  成功: {stats.success}")
    print(f"  失败: {stats.fail}")

    total = stats.success + stats.fail
    if total > 0:
        print(f"  成功率: {stats.success/total*100:.1f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
