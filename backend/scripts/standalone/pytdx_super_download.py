"""
pytdx 超级加速下载器
- 多连接池：每个线程独立连接不同服务器
- 高并发：支持 10-20 线程
- 智能重试：自动切换服务器
- 断点续传：支持中断恢复
"""

import sys
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta
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

# 更多可用的通达信服务器
TDX_SERVERS = [
    ("218.75.126.9", 7709),     # 招商证券上海
    ("119.147.212.81", 7709),   # 招商证券深圳
    ("101.71.170.10", 7709),    # 华泰证券
    ("218.108.98.244", 7709),   # 华泰证券
    ("218.108.61.19", 7709),    # 华泰证券
    ("114.80.63.12", 7709),     # 申万宏源
    ("114.80.63.33", 7709),     # 申万宏源
    ("39.106.218.146", 7709),   # 中信建投
    ("60.12.136.250", 7709),    # 中信建投
    ("120.76.152.87", 7709),    # 广发证券
]

MARKET_SH = 1  # 上海
MARKET_SZ = 0  # 深圳

CATEGORY_DAILY = 4  # 日K


def parse_symbol(symbol: str):
    """解析股票代码，返回市场代码和股票代码"""
    code = str(symbol).zfill(6)
    if code.startswith(('6', '900', '688')):
        return MARKET_SH, code
    else:
        return MARKET_SZ, code


def init_table(conn):
    """初始化表"""
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kline_symbol ON stock_daily_prices (symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kline_date ON stock_daily_prices (trade_date)")
    conn.commit()


def get_stock_list():
    """获取股票列表"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT symbol, name FROM stock_info", conn)
        return df.to_dict('records')
    finally:
        conn.close()


def get_last_dates():
    """获取已有数据的最新日期"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            "SELECT symbol, MAX(trade_date) as last_date FROM stock_daily_prices GROUP BY symbol",
            conn
        )
        if df.empty:
            return {}
        return dict(zip(df['symbol'], pd.to_datetime(df['last_date']).dt.date))
    finally:
        conn.close()


def save_kline_batch(df_list, conn):
    """批量保存数据"""
    if not df_list:
        return 0

    try:
        final_df = pd.concat(df_list, ignore_index=True)
        final_df['trade_date'] = pd.to_datetime(final_df['trade_date']).dt.date

        final_df.to_sql('stock_daily_prices', conn, if_exists='append', index=False,
                       method='multi', chunksize=5000)
        conn.commit()
        return len(final_df)
    except Exception as e:
        # 单条插入
        count = 0
        for df in df_list:
            for _, row in df.iterrows():
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO stock_daily_prices
                        (symbol, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row.get('symbol'), row.get('trade_date'),
                        row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                        row.get('volume'), row.get('amount'), row.get('pct_chg'), row.get('turnover_rate')
                    ))
                    count += 1
                except:
                    pass
        conn.commit()
        return count


class TdxConnection:
    """pytdx 连接类，每个线程一个独立连接"""

    def __init__(self, server=None):
        self.api = None
        self.server = server
        self.connect()

    def connect(self):
        """连接到服务器"""
        if self.server:
            servers = [self.server]
        else:
            servers = TDX_SERVERS

        # 随机选择服务器，负载均衡
        for host, port in servers:
            try:
                self.api = TdxHq_API()
                if self.api.connect(host, port):
                    self.server = (host, port)
                    return True
            except:
                continue
        return False

    def is_connected(self):
        """检查是否连接"""
        if not self.api:
            return False
        try:
            self.api.get_security_quotes([(1, '000001')])
            return True
        except:
            return False

    def reconnect(self):
        """重新连接"""
        try:
            if self.api:
                self.api.disconnect()
        except:
            pass
        return self.connect()

    def get_kline(self, symbol, start_date, end_date):
        """获取K线数据"""
        if not self.is_connected():
            if not self.reconnect():
                return None

        try:
            market, code = parse_symbol(symbol)

            all_data = []
            offset = 0
            count = 800

            while True:
                data = self.api.get_security_bars(CATEGORY_DAILY, market, code, offset, count)
                if not data:
                    break
                all_data.extend(data)
                if len(data) < count:
                    break
                offset += count
                if offset > 10000:
                    break

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

            # 日期过滤
            today = pd.Timestamp.now().date()
            df = df[df['trade_date'] <= today].copy()

            if start_date:
                start = pd.to_datetime(start_date).date()
                df = df[df['trade_date'] >= start]
            if end_date:
                end = pd.to_datetime(end_date).date()
                df = df[df['trade_date'] <= end]

            if 'turnover_rate' not in df.columns:
                df['turnover_rate'] = None

            columns = ['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']
            existing_columns = [c for c in columns if c in df.columns]
            df = df[existing_columns]

            return df

        except Exception as e:
            return None

    def close(self):
        """关闭连接"""
        try:
            if self.api:
                self.api.disconnect()
        except:
            pass


class SuperTdxDownloader:
    """超级加速下载器"""

    def __init__(self, max_workers=15, batch_size=500):
        self.max_workers = max_workers
        self.batch_size = batch_size
        self.data_lock = Lock()
        self.collected_data = []
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0

    def _collect_worker(self, stock, existing_records, start_date, end_date, thread_id):
        """工作线程"""
        code = stock['symbol']
        name = stock.get('name', '')

        # 每个线程使用不同的服务器
        server_idx = thread_id % len(TDX_SERVERS)
        conn = TdxConnection(TDX_SERVERS[server_idx])

        try:
            # 检查是否需要更新
            last_date = existing_records.get(code)
            if last_date:
                today = datetime.now().date()
                if last_date >= today:
                    conn.close()
                    return {'status': 'skip', 'code': code}
                download_start = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                download_start = start_date

            if download_start > end_date:
                conn.close()
                return {'status': 'skip', 'code': code}

            # 重试机制
            for retry in range(3):
                df = conn.get_kline(code, download_start, end_date)
                if df is not None and not df.empty:
                    conn.close()
                    return {'status': 'success', 'code': code, 'data': df}

                # 重试前换个服务器
                if retry < 2:
                    conn.reconnect()
                    time.sleep(random.uniform(0.05, 0.15))

            conn.close()
            return {'status': 'fail', 'code': code, 'error': '无数据'}

        except Exception as e:
            conn.close()
            return {'status': 'fail', 'code': code, 'error': str(e)}

    def run(self, start_date=None, end_date=None):
        """执行下载"""
        if start_date is None:
            start_date = DEFAULT_START_DATE
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')

        print("=" * 70)
        print("  🚀 pytdx 超级加速下载器")
        print("=" * 70)
        print(f"  日期范围: {start_date} ~ {end_date}")
        print(f"  并发线程: {self.max_workers}")
        print(f"  批次大小: {self.batch_size}")
        print(f"  服务器数: {len(TDX_SERVERS)}")
        print("=" * 70)
        print()

        # 获取股票列表
        print("获取股票列表...")
        stock_list = get_stock_list()
        print(f"✅ 共 {len(stock_list)} 只股票")

        print("获取已有数据...")
        existing_records = get_last_dates()
        print(f"✅ 已有 {len(existing_records)} 只股票的数据")

        conn = sqlite3.connect(DB_PATH)
        init_table(conn)

        total = len(stock_list)
        pending = total - len([s for s in stock_list if s['symbol'] in existing_records])

        print(f"\n📊 需要下载: {pending} 只股票")
        print()

        # 使用线程池
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for i, stock in enumerate(stock_list):
                future = executor.submit(
                    self._collect_worker,
                    stock, existing_records, start_date, end_date, i
                )
                futures[future] = stock

            with tqdm(total=total, desc="  下载进度", unit="只") as pbar:
                for future in as_completed(futures):
                    try:
                        result = future.result()

                        if result['status'] == 'success':
                            with self.data_lock:
                                self.collected_data.append(result['data'])
                                self.success_count += 1

                                # 批量保存
                                if len(self.collected_data) >= self.batch_size:
                                    count = save_kline_batch(self.collected_data, conn)
                                    pbar.write(f"💾 已保存 {count:,} 条记录 (进度: {self.success_count}/{total})")
                                    self.collected_data = []

                        elif result['status'] == 'skip':
                            self.skip_count += 1

                        else:
                            self.fail_count += 1

                    except Exception as e:
                        self.fail_count += 1

                    pbar.update(1)

        # 保存剩余数据
        if self.collected_data:
            count = save_kline_batch(self.collected_data, conn)
            print(f"\n💾 最终保存 {count:,} 条记录")

        conn.close()

        print()
        print("=" * 70)
        print("  ✅ 下载完成！")
        print("=" * 70)
        print(f"  成功: {self.success_count}")
        print(f"  跳过: {self.skip_count}")
        print(f"  失败: {self.fail_count}")
        print(f"  总计: {total}")
        print("=" * 70)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="pytdx 超级加速下载")
    parser.add_argument('--workers', type=int, default=15, help='并发线程数 (默认15)')
    parser.add_argument('--start', default='2023-01-01', help='开始日期')
    parser.add_argument('--end', default=None, help='结束日期')
    parser.add_argument('--batch', type=int, default=500, help='批次大小')

    args = parser.parse_args()

    end_date = args.end
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')

    downloader = SuperTdxDownloader(
        max_workers=args.workers,
        batch_size=args.batch
    )
    downloader.run(args.start, end_date)


if __name__ == "__main__":
    main()
