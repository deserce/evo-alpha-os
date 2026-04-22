"""
pytdx 多线程K线数据下载器
使用通达信协议 + 多线程，快速下载全部A股历史数据

性能：
- 5线程: ~30-40分钟完成 5800只股票
- 10线程: ~20分钟完成
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

# 添加 backend 到路径
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from data_job.core.tdx_adapter import TdxAdapter
import akshare as ak

DB_PATH = "data/local_quant.db"
DEFAULT_START_DATE = "2023-01-01"


def init_table(conn):
    """初始化 stock_daily_prices 表"""
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
    """从数据库获取股票列表"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT symbol, name FROM stock_info", conn)
        return df.to_dict('records')
    finally:
        conn.close()


def get_last_dates():
    """获取每只股票的最新日期"""
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
    """批量保存K线数据"""
    if not df_list:
        return 0

    try:
        final_df = pd.concat(df_list, ignore_index=True)

        # 确保日期格式正确
        final_df['trade_date'] = pd.to_datetime(final_df['trade_date']).dt.date

        final_df.to_sql('stock_daily_prices', conn, if_exists='append', index=False,
                       method='multi', chunksize=5000)
        conn.commit()
        return len(final_df)
    except Exception as e:
        # 批量失败，尝试单条插入
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


def download_single_stock(tdx, symbol, start_date, end_date):
    """下载单只股票K线"""
    try:
        df = tdx.get_stock_kline(symbol, start_date, end_date)
        if df is None or df.empty:
            return None

        # 过滤未来日期
        today = pd.Timestamp.now().date()
        df = df[df['trade_date'] <= today].copy()

        if df.empty:
            return None

        # 确保列名正确
        required_cols = ['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']
        for col in required_cols:
            if col not in df.columns:
                if col == 'symbol':
                    df[col] = symbol
                elif col == 'pct_chg' and 'close' in df.columns:
                    df[col] = df['close'].pct_change() * 100
                else:
                    df[col] = None

        if 'turnover_rate' not in df.columns:
            df['turnover_rate'] = None

        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        return df[required_cols + ['turnover_rate']]

    except Exception as e:
        return None


class TdxParallelDownloader:
    """pytdx 多线程下载器"""

    def __init__(self, max_workers=5, batch_size=500):
        self.max_workers = max_workers
        self.batch_size = batch_size
        self.data_lock = Lock()
        self.collected_data = []
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0

    def _init_tdx(self):
        """初始化 pytdx 连接"""
        tdx = TdxAdapter()
        if not tdx.is_available():
            raise Exception("pytdx 连接失败")
        return tdx

    def _collect_worker(self, tdx, stock, existing_records, start_date, end_date):
        """工作线程函数"""
        code = stock['symbol']
        name = stock.get('name', '')

        try:
            # 检查是否需要更新
            last_date = existing_records.get(code)
            if last_date:
                today = datetime.now().date()
                if last_date >= today:
                    return {'status': 'skip', 'code': code}
                download_start = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                download_start = start_date

            if download_start > end_date:
                return {'status': 'skip', 'code': code}

            # 下载数据
            df = download_single_stock(tdx, code, download_start, end_date)

            if df is not None and not df.empty:
                return {'status': 'success', 'code': code, 'data': df}
            else:
                return {'status': 'fail', 'code': code, 'error': '无数据'}

        except Exception as e:
            return {'status': 'fail', 'code': code, 'error': str(e)}

    def run(self, start_date=None, end_date=None):
        """执行下载"""
        if start_date is None:
            start_date = DEFAULT_START_DATE
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')

        print("=" * 70)
        print("  🚀 pytdx 多线程K线下载器")
        print("=" * 70)
        print(f"  日期范围: {start_date} ~ {end_date}")
        print(f"  并发线程: {self.max_workers}")
        print(f"  批次大小: {self.batch_size}")
        print("=" * 70)
        print()

        # 初始化
        print("初始化 pytdx...")
        tdx = self._init_tdx()
        print(f"✅ pytdx 已连接: {tdx.current_server}")

        print("获取股票列表...")
        stock_list = get_stock_list()
        print(f"✅ 共 {len(stock_list)} 只股票")

        print("获取已有数据...")
        existing_records = get_last_dates()
        print(f"✅ 已有 {len(existing_records)} 只股票的数据")

        conn = sqlite3.connect(DB_PATH)
        init_table(conn)

        total = len(stock_list)
        pending = total - len(existing_records)

        print(f"\n📊 需要下载: {pending} 只股票")
        print()

        # 使用线程池
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._collect_worker, tdx, stock, existing_records, start_date, end_date): stock
                for stock in stock_list
            }

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
                                    print(f"\n💾 已保存 {count} 条记录")
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
            print(f"\n💾 最终保存 {count} 条记录")

        conn.close()
        tdx.disconnect()

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

    parser = argparse.ArgumentParser(description="pytdx 多线程K线下载")
    parser.add_argument('--workers', type=int, default=5, help='并发线程数 (默认5)')
    parser.add_argument('--start', default='2023-01-01', help='开始日期')
    parser.add_argument('--end', default=None, help='结束日期')
    parser.add_argument('--batch', type=int, default=500, help='批次大小')

    args = parser.parse_args()

    end_date = args.end
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')

    downloader = TdxParallelDownloader(
        max_workers=args.workers,
        batch_size=args.batch
    )
    downloader.run(args.start, end_date)


if __name__ == "__main__":
    main()
