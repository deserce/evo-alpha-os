"""
板块K线下载器 - 直接使用 AKShare
更简单可靠的实现
"""

import sys
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm
import time

# 添加 backend 到路径
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

DB_PATH = "data/local_quant.db"


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")

    # 创建表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_daily_prices (
            sector_name TEXT,
            trade_date DATE,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            PRIMARY KEY (sector_name, trade_date)
        );
    """)
    conn.commit()
    return conn


def download_sector_kline():
    """下载板块K线数据"""
    print("\n" + "=" * 70)
    print("  下载板块 K线数据 (AKShare)")
    print("=" * 70)

    import akshare as ak

    conn = init_db()

    # 获取板块列表
    try:
        df_sectors = pd.read_sql("SELECT sector_name, sector_type FROM sector_list ORDER BY sector_name", conn)
    except Exception as e:
        print(f"⚠️  无法获取板块列表: {e}")
        conn.close()
        return

    # 只获取行业板块（更可靠）
    df_sectors = df_sectors[df_sectors['sector_type'] == 'Industry']

    if df_sectors.empty:
        print("⚠️  板块列表为空")
        conn.close()
        return

    print(f"共 {len(df_sectors)} 个行业板块")

    # 获取已有数据
    try:
        df_has = pd.read_sql("SELECT DISTINCT sector_name FROM sector_daily_prices", conn)
        has_sectors = set(df_has['sector_name'].tolist()) if not df_has.empty else set()
    except:
        has_sectors = set()

    pending = df_sectors[~df_sectors['sector_name'].isin(has_sectors)]

    if pending.empty:
        print("✅ 所有板块数据已下载")
        conn.close()
        return

    print(f"需要下载 {len(pending)} 个板块\n")

    success = 0
    fail = 0
    errors = []

    # 设置日期范围
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=365*3)).strftime("%Y%m%d")

    for idx, row in tqdm(pending.iterrows(), total=len(pending), desc="下载进度"):
        sector_name = row['sector_name']

        try:
            # 使用 akshare 获取板块数据
            df = ak.stock_board_industry_hist_em(
                symbol=sector_name,
                start_date=start_date,
                end_date=end_date,
                adjust=""
            )

            if df is not None and not df.empty:
                # 字段映射
                df = df.rename(columns={
                    '日期': 'trade_date',
                    '开盘': 'open',
                    '收盘': 'close',
                    '最高': 'high',
                    '最低': 'low',
                    '成交量': 'volume',
                    '成交额': 'amount',
                    '涨跌幅': 'pct_chg'
                })

                df['sector_name'] = sector_name
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

                # 数值转换
                for col in ['open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

                # 选择需要的列
                df_final = df[['sector_name', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']]

                # 保存数据
                df_final.to_sql('sector_daily_prices_tmp', conn, if_exists='replace', index=False)
                conn.execute("INSERT OR IGNORE INTO sector_daily_prices SELECT * FROM sector_daily_prices_tmp")
                conn.execute("DROP TABLE sector_daily_prices_tmp")
                conn.commit()
                success += 1
            else:
                fail += 1
                errors.append(f"{sector_name}: 无数据")

        except Exception as e:
            fail += 1
            errors.append(f"{sector_name}: {str(e)[:40]}")
            # 出错后稍微等待
            time.sleep(1)

        time.sleep(0.3)  # 避免请求过快

    conn.close()

    print(f"\n✅ 板块 K线下载完成: 成功 {success}, 失败 {fail}")

    if errors:
        print(f"\n失败列表 (最多显示10个):")
        for err in errors[:10]:
            print(f"  - {err}")


def download_index_kline():
    """下载指数K线数据"""
    print("\n" + "=" * 70)
    print("  下载指数 K线数据 (AKShare)")
    print("=" * 70)

    import akshare as ak

    conn = init_db()

    # 主要指数列表
    index_list = [
        {"code": "sh000001", "name": "上证指数", "symbol": "000001"},
        {"code": "sz399001", "name": "深证成指", "symbol": "399001"},
        {"code": "sz399006", "name": "创业板指", "symbol": "399006"},
        {"code": "sh000300", "name": "沪深300", "symbol": "000300"},
        {"code": "sh000905", "name": "中证500", "symbol": "000905"},
        {"code": "sh000852", "name": "中证1000", "symbol": "000852"},
    ]

    # 创建表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_daily_prices (
            symbol VARCHAR(20),
            trade_date DATE,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            PRIMARY KEY (symbol, trade_date)
        );
    """)
    conn.commit()

    # 获取已有数据
    try:
        df_has = pd.read_sql("SELECT DISTINCT symbol FROM index_daily_prices", conn)
        has_symbols = set(df_has['symbol'].tolist()) if not df_has.empty else set()
    except:
        has_symbols = set()

    pending = [idx for idx in index_list if idx['symbol'] not in has_symbols]

    if not pending:
        print("✅ 所有指数数据已下载")
        conn.close()
        return

    print(f"需要下载 {len(pending)} 个指数\n")

    success = 0
    fail = 0

    for idx in tqdm(pending, desc="下载进度"):
        code = idx['code']
        symbol = idx['symbol']
        name = idx['name']

        try:
            # 获取指数数据
            df = ak.stock_zh_index_daily(symbol=code)

            if df is not None and not df.empty:
                df = df.reset_index()
                df['symbol'] = symbol
                df['trade_date'] = pd.to_datetime(df['date']).dt.date

                # 计算涨跌幅
                df['pct_chg'] = df['close'].pct_change() * 100

                # 选择需要的列
                df_final = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'pct_chg']]
                df_final['amount'] = df_final['volume'] * df_final['close']

                # 只保留最近3年
                three_years_ago = (datetime.now() - timedelta(days=365*3)).date()
                df_final = df_final[df_final['trade_date'] >= three_years_ago]

                # 保存数据
                df_final.to_sql('index_daily_prices_tmp', conn, if_exists='replace', index=False)
                conn.execute("INSERT OR IGNORE INTO index_daily_prices SELECT * FROM index_daily_prices_tmp")
                conn.execute("DROP TABLE index_daily_prices_tmp")
                conn.commit()
                success += 1
            else:
                fail += 1

        except Exception as e:
            fail += 1
            print(f"\n  ❌ {name} 失败: {e}")

        time.sleep(0.3)

    conn.close()

    print(f"\n✅ 指数 K线下载完成: 成功 {success}, 失败 {fail}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="K线下载器 - AKShare")
    parser.add_argument('--sector', action='store_true', help='下载板块 K线')
    parser.add_argument('--index', action='store_true', help='下载指数 K线')
    parser.add_argument('--all', action='store_true', help='下载所有数据')

    args = parser.parse_args()

    print("=" * 70)
    print("  K线下载器 - AKShare")
    print("=" * 70)

    if args.all or args.sector:
        download_sector_kline()

    if args.all or args.index:
        download_index_kline()

    if not (args.all or args.sector or args.index):
        print("\n请指定要下载的数据类型:")
        print("  --all   下载所有数据")
        print("  --sector 下载板块 K线")
        print("  --index 下载指数 K线")

    print("\n" + "=" * 70)
    print("  下载任务完成！")
    print("=" * 70)
