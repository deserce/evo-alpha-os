"""
pytdx 多服务器多线程K线下载器 - 今日增量更新版
- 每个服务器独立线程池
- 自动获取需要更新的股票
- 支持断点续传
- 使用 pytdx (通达信协议)
"""

import sys
import sqlite3
import time
import argparse
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

from pytdx.hq import TdxHq_API

DB_PATH = "data/local_quant.db"
MARKET_SH = 1  # 上海
MARKET_SZ = 0  # 深圳

# 通达信服务器列表（验证过可用的）
TDX_SERVERS = [
    ("218.75.126.9", 7709, "招商证券上海"),
    ("60.12.136.250", 7709, "中信建投"),
    ("119.147.212.81", 7709, "招商证券深圳"),
    ("101.71.170.10", 7709, "华泰证券2"),
    ("218.108.61.19", 7709, "华泰证券3"),
]

# 全局统计
save_lock = Lock()
success_count = 0
fail_count = 0
skip_count = 0


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def get_stocks_need_update(target_date=None):
    """
    获取需要更新的股票列表

    Args:
        target_date: 目标日期，默认为今天
    """
    if target_date is None:
        target_date = datetime.now().date()

    conn = sqlite3.connect(DB_PATH)
    try:
        # 获取所有股票及其最新交易日期
        query = """
            SELECT s.symbol, i.name, MAX(s.trade_date) as last_date
            FROM stock_daily_prices s
            LEFT JOIN stock_info i ON s.symbol = i.symbol
            GROUP BY s.symbol
            HAVING MAX(s.trade_date) < ?
            ORDER BY last_date DESC
        """
        df = pd.read_sql(query, conn, params=[target_date])

        if df.empty:
            return []

        # 计算每只股票的开始日期（上次日期+1）
        df['start_date'] = (pd.to_datetime(df['last_date']) + timedelta(days=1)).dt.date
        df['end_date'] = target_date

        return df.to_dict('records')
    finally:
        conn.close()


def parse_symbol(symbol: str):
    """解析股票代码，返回 (market, code)"""
    code = str(symbol).zfill(6)
    if code.startswith(('6', '900', '688')):
        return MARKET_SH, code
    else:
        return MARKET_SZ, code


def save_to_db(df):
    """保存数据到数据库（去重）"""
    if df is None or df.empty:
        return False

    conn = sqlite3.connect(DB_PATH, timeout=60)
    try:
        # 使用 INSERT OR IGNORE 避免重复
        df.to_sql('stock_daily_prices_tmp', conn, if_exists='replace', index=False)
        conn.execute("""
            INSERT OR IGNORE INTO stock_daily_prices
            SELECT * FROM stock_daily_prices_tmp
        """)
        conn.execute("DROP TABLE IF EXISTS stock_daily_prices_tmp")
        conn.commit()
        return True
    except Exception as e:
        return False
    finally:
        conn.close()


def download_stock(symbol, start_date, end_date, server_info, timeout=10):
    """
    从指定服务器下载单只股票K线数据 (使用 pytdx)

    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        server_info: (host, port, name) 服务器信息
        timeout: 超时时间

    Returns:
        DataFrame or None
    """
    host, port, _ = server_info
    api = TdxHq_API()

    try:
        # 连接服务器
        if not api.connect(host, port):
            return None

        market, code = parse_symbol(symbol)
        all_data = []

        # 循环获取所有数据（每次最多800条）
        offset = 0
        batch_size = 800
        category = 4  # 日K线

        while True:
            try:
                data = api.get_security_bars(category, market, code, offset, batch_size)

                if not data:
                    break

                all_data.extend(data)

                if len(data) < batch_size:
                    break

                offset += batch_size
                if offset > 10000:  # 防止无限循环
                    break
            except Exception:
                break

        api.disconnect()

        if not all_data:
            return None

        # 转换为 DataFrame
        df = pd.DataFrame(all_data)

        # 重命名列
        df = df.rename(columns={'datetime': 'trade_date'})

        # 转换日期
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        df['symbol'] = symbol

        # 重命名其他列
        df = df.rename(columns={
            'vol': 'volume',
        })

        # 计算涨跌幅
        if 'close' in df.columns:
            df['pct_chg'] = df['close'].pct_change() * 100

        # 添加缺失列
        for col in ['turnover_rate']:
            if col not in df.columns:
                df[col] = None

        # 选择需要的列
        columns = ['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']
        existing_columns = [c for c in columns if c in df.columns]
        df = df[existing_columns]

        # 日期过滤
        start = pd.to_datetime(start_date).date()
        end = pd.to_datetime(end_date).date()
        df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()

        if df.empty:
            return None

        return df

    except Exception as e:
        try:
            api.disconnect()
        except:
            pass
        return None


def download_worker(stock_info, server_info):
    """
    工作线程 - 处理单只股票

    Args:
        stock_info: dict with keys: symbol, name, start_date, end_date
        server_info: (host, port, name) 服务器信息
    """
    global success_count, fail_count, skip_count

    symbol = stock_info['symbol']
    start_date = stock_info['start_date']
    end_date = stock_info['end_date']

    # 下载数据
    df = download_stock(symbol, start_date, end_date, server_info, timeout=15)

    if df is not None and not df.empty:
        if save_to_db(df):
            with save_lock:
                success_count += 1
            return {'status': 'success', 'symbol': symbol, 'rows': len(df)}
        else:
            with save_lock:
                fail_count += 1
            return {'status': 'fail', 'symbol': symbol, 'error': 'save_failed'}
    else:
        with save_lock:
            fail_count += 1
        return {'status': 'fail', 'symbol': symbol, 'error': 'no_data'}


def server_worker(server_info, stocks_chunk):
    """
    服务器工作线程 - 处理分配给该服务器的一批股票

    Args:
        server_info: (host, port, name) 服务器信息
        stocks_chunk: 分配给该服务器的股票列表
    """
    host, port, name = server_info
    results = []

    for stock_info in stocks_chunk:
        result = download_worker(stock_info, server_info)
        results.append(result)
        time.sleep(0.05)  # 小延迟避免过载

    return results, server_info


def main():
    parser = argparse.ArgumentParser(description="mootdx 多服务器K线下载器 - 今日更新")
    parser.add_argument('--threads-per-server', type=int, default=3, help='每个服务器的线程数')
    parser.add_argument('--date', help='目标日期 (YYYY-MM-DD)，默认今天')
    parser.add_argument('--symbol', help='指定单只股票代码')
    parser.add_argument('--limit', type=int, help='限制处理数量（测试用）')
    parser.add_argument('--force-all', action='store_true', help='强制更新所有股票（忽略已有数据）')

    args = parser.parse_args()

    # 确定目标日期
    if args.date:
        target_date = pd.to_datetime(args.date).date()
    else:
        target_date = datetime.now().date()

    print("=" * 80)
    print("  🚀 pytdx 多服务器K线下载器 - 今日更新")
    print("=" * 80)
    print(f"  目标日期: {target_date}")
    print(f"  服务器数: {len(TDX_SERVERS)}")
    print(f"  每服务器线程: {args.threads_per_server}")
    print(f"  总并发: {len(TDX_SERVERS) * args.threads_per_server}")
    print("=" * 80)
    print()

    init_db()

    # 获取需要更新的股票
    if args.symbol:
        # 单只股票模式
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql("SELECT name FROM stock_info WHERE symbol = ?", conn, params=[args.symbol])
        conn.close()

        if not df.empty:
            stock_info = {
                'symbol': args.symbol,
                'name': df.iloc[0]['name'],
                'start_date': (target_date - timedelta(days=5)).strftime('%Y-%m-%d'),
                'end_date': target_date
            }
            pending_stocks = [stock_info]
            print(f"📊 更新指定股票: {args.symbol}")
        else:
            print(f"❌ 股票 {args.symbol} 不存在")
            return
    elif args.force_all:
        # 强制更新所有股票
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql("SELECT symbol, name FROM stock_info", conn)
        conn.close()

        pending_stocks = []
        for _, row in df.iterrows():
            pending_stocks.append({
                'symbol': row['symbol'],
                'name': row['name'],
                'start_date': (target_date - timedelta(days=7)).strftime('%Y-%m-%d'),
                'end_date': target_date
            })
        print(f"📊 强制更新所有股票: {len(pending_stocks)} 只")
    else:
        # 增量更新模式
        pending_stocks = get_stocks_need_update(target_date)
        print(f"📊 需要更新 {len(pending_stocks)} 只股票")

    if args.limit and len(pending_stocks) > args.limit:
        pending_stocks = pending_stocks[:args.limit]
        print(f"📊 限制处理数量: {args.limit}")

    if not pending_stocks:
        print("✅ 没有需要更新的股票")
        return

    print()

    # 将股票分配给各个服务器的各个线程
    total_threads = len(TDX_SERVERS) * args.threads_per_server
    chunk_size = max(1, len(pending_stocks) // total_threads)

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

    print(f"📊 已分配 {len(server_tasks)} 个任务块")
    print(f"📊 开始下载...")
    print()

    # 使用线程池执行
    with ThreadPoolExecutor(max_workers=total_threads) as executor:
        futures = {
            executor.submit(server_worker, server, stocks): (len(stocks), server)
            for stocks, server in server_tasks
        }

        with tqdm(total=len(pending_stocks), desc="  下载进度", unit="只") as pbar:
            completed = 0
            for future in as_completed(futures):
                try:
                    chunk_size, server = futures[future]
                    results, server_info = future.result()
                    completed += chunk_size
                    pbar.update(chunk_size)

                    # 统计
                    success = sum(1 for r in results if r['status'] == 'success')
                    fail = sum(1 for r in results if r['status'] == 'fail')

                    pbar.set_postfix({
                        "服务器": server_info[2][:6],
                        "成功": success_count,
                        "失败": fail_count
                    })
                except Exception as e:
                    chunk_size, server = futures[future]
                    pbar.update(chunk_size)
                    pbar.write(f"❌ {server[2]}: {e}")

    print()
    print("=" * 80)
    print("  ✅ 下载完成！")
    print("=" * 80)
    print(f"  成功: {success_count} 只")
    print(f"  失败: {fail_count} 只")
    print(f"  跳过: {skip_count} 只")
    total = success_count + fail_count + skip_count
    if total > 0:
        print(f"  成功率: {success_count/total*100:.1f}%")
    print("=" * 80)


if __name__ == "__main__":
    main()
