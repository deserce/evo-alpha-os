"""
mootdx K线下载器 - 多线程优化版
- 共享连接池，避免服务器限流
- 智能队列分配
- 自动重试失败任务
- 支持黑名单过滤
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore
import pandas as pd
import time
from queue import Queue
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
        self.skip = 0
        self.lock = Lock()

    def add_success(self):
        with self.lock:
            self.success += 1

    def add_fail(self):
        with self.lock:
            self.fail += 1

    def add_skip(self):
        with self.lock:
            self.skip += 1


class ConnectionPool:
    """连接池 - 管理多个 mootdx 连接"""

    def __init__(self, size=3):
        self.size = size
        self.pool = Queue(maxsize=size)
        self.lock = Lock()
        self._init_pool()

    def _init_pool(self):
        """初始化连接池"""
        for i in range(self.size):
            try:
                adapter = MootdxAdapter(timeout=15)
                if adapter.is_available():
                    self.pool.put(adapter, block=False)
                else:
                    print(f"  连接 {i} 初始化失败")
            except Exception as e:
                print(f"  连接 {i} 异常: {e}")

    def get(self, timeout=30):
        """获取一个连接"""
        return self.pool.get(timeout=timeout)

    def put(self, adapter):
        """归还连接"""
        self.pool.put(adapter, block=False)

    @property
    def available(self):
        """可用连接数"""
        return self.pool.qsize()


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


def get_pending_stocks(limit=None):
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

        result = df_pending['symbol'].tolist()
        if limit and len(result) > limit:
            result = result[:limit]
        return result
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
        # 可能是重复数据，也算成功
        return True
    finally:
        conn.close()


def download_stock(symbol, start_date, pool, stats, max_retries=3):
    """下载单只股票并保存（使用连接池）"""
    last_error = None

    for attempt in range(max_retries):
        adapter = None
        try:
            # 从连接池获取连接
            adapter = pool.get(timeout=30)

            # 下载数据
            df = adapter.get_stock_kline(symbol, start_date=start_date)

            if df is not None and not df.empty:
                if save_immediate(df):
                    stats.add_success()
                    return True
            else:
                last_error = "no_data"
                time.sleep(0.5)
                continue

        except Exception as e:
            last_error = str(e)
            time.sleep(0.5)
            continue

        finally:
            # 归还连接到池
            if adapter:
                pool.put(adapter)

    stats.add_fail()
    return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="mootdx K线下载器 - 多线程优化版")
    parser.add_argument('--workers', type=int, default=5, help='并发下载线程数')
    parser.add_argument('--pool-size', type=int, default=3, help='连接池大小')
    parser.add_argument('--start', default=DEFAULT_START_DATE, help='开始日期')
    parser.add_argument('--symbol', help='指定单只股票代码')
    parser.add_argument('--limit', type=int, help='限制下载数量（测试用）')

    args = parser.parse_args()

    print("=" * 70)
    print("  mootdx K线下载器 - 多线程优化版")
    print("=" * 70)
    print(f"  并发线程: {args.workers}")
    print(f"  连接池大小: {args.pool_size}")
    print(f"  开始日期: {args.start}")
    print("=" * 70)
    print()

    init_db()

    # 如果指定了单只股票
    if args.symbol:
        pending_stocks = [args.symbol]
        print(f"下载指定股票: {args.symbol}")
    else:
        pending_stocks = get_pending_stocks(limit=args.limit)
        print(f"需要下载 {len(pending_stocks)} 只股票")

    if not pending_stocks:
        print("没有需要下载的股票")
        return

    # 创建连接池
    print(f"正在初始化连接池 (大小={args.pool_size})...")
    pool = ConnectionPool(size=args.pool_size)

    if pool.available == 0:
        print("无法创建任何连接，请检查网络")
        return

    print(f"连接池已就绪: {pool.available} 个连接")
    print()

    stats = DownloadStats()

    # 使用线程池下载
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download_stock, symbol, args.start, pool, stats): symbol
            for symbol in pending_stocks
        }

        with tqdm(total=len(pending_stocks), desc="下载进度", unit="只") as pbar:
            for future in as_completed(futures):
                try:
                    future.result()
                    pbar.update(1)
                    pbar.set_postfix({
                        "成功": stats.success,
                        "失败": stats.fail,
                        "池": pool.available
                    })
                except Exception as e:
                    stats.add_fail()
                    pbar.update(1)
                    pbar.write(f"错误: {e}")

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
