"""
板块指数K线下载器 - 使用 mootdx (修复版)
下载通达信行业指数作为板块数据
"""

import sys
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime
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


def download_sector_indices():
    """下载行业指数K线数据"""
    print("\n" + "=" * 70)
    print("  下载行业指数 K线数据 (mootdx)")
    print("=" * 70)

    from mootdx.quotes import Quotes

    # 创建API
    api = Quotes.factory(
        market='std',
        server='119.147.212.81:7709',
        timeout=15
    )

    if api.client is None:
        print("⚠️  mootdx 连接失败")
        return

    print("✅ mootdx 已连接")

    # 获取行业指数列表
    try:
        stocks = api.stocks(market=1)
        # 过滤行业指数
        sector_indices = stocks[
            stocks['code'].str.match(r'^\d{6}$') &  # 6位代码
            stocks['name'].str.contains('指数', na=False) &
            ~stocks['name'].str.contains('上证|深证|创业板|中证|ETF', na=False)
        ].copy()
    except Exception as e:
        print(f"⚠️  获取指数列表失败: {e}")
        return

    if sector_indices.empty:
        print("⚠️  无行业指数")
        return

    print(f"共 {len(sector_indices)} 个行业指数")

    # 创建指数名称映射
    index_map = {}
    for _, row in sector_indices.iterrows():
        code = row['code']
        name = row['name'].replace('指数', '')
        index_map[code] = name

    conn = init_db()

    # 获取已有的数据
    try:
        df_has = pd.read_sql("SELECT DISTINCT sector_name FROM sector_daily_prices", conn)
        has_sectors = set(df_has['sector_name'].tolist()) if not df_has.empty else set()
    except:
        has_sectors = set()

    # 过滤已下载的
    pending = {k: v for k, v in index_map.items() if v not in has_sectors}

    if not pending:
        print("✅ 所有行业指数数据已下载")
        conn.close()
        return

    print(f"已下载: {len(has_sectors)} 个")
    print(f"需要下载: {len(pending)} 个行业指数\n")

    success = 0
    fail = 0

    for code, name in tqdm(pending.items(), desc="下载进度"):
        try:
            # 5开头的是上海ETF，用bars(market=0)
            # 其他的用index_bars
            all_dfs = []
            offset = 800

            for i in range(15):
                if code.startswith('5'):
                    df = api.bars(
                        symbol=code,
                        frequency=9,
                        start=0,
                        offset=offset,
                        count=800,
                        market=0  # 上海市场
                    )
                else:
                    df = api.index_bars(
                        symbol=code,
                        frequency=9,
                        start=0,
                        offset=offset,
                        count=800
                    )

                if df is None or df.empty:
                    break

                all_dfs.append(df)

                if len(df) < 800:
                    break

                offset += 800

            if not all_dfs:
                fail += 1
                continue

            # 合并所有数据
            df_combined = pd.concat(all_dfs, ignore_index=True)

            # 标准化列名
            # 删除重复列
            df_combined = df_combined.loc[:, ~df_combined.columns.duplicated(keep='first')]

            # 重命名列 - 需要处理 vol -> volume，并避免与现有 volume 冲突
            column_map = {}
            if 'vol' in df_combined.columns and 'volume' not in df_combined.columns:
                column_map['vol'] = 'volume'
            if 'datetime' in df_combined.columns:
                column_map['datetime'] = 'trade_date'

            if column_map:
                df_combined = df_combined.rename(columns=column_map)

            # 如果还有重复列（来自重命名冲突），删除第一次出现的
            if df_combined.columns.duplicated().any():
                df_combined = df_combined.loc[:, ~df_combined.columns.duplicated(keep='last')]

            # 确保有必要的列
            required_cols = ['trade_date', 'open', 'close', 'high', 'low']
            if not all(c in df_combined.columns for c in required_cols):
                fail += 1
                continue

            # 转换日期
            if 'trade_date' in df_combined.columns:
                df_combined['trade_date'] = pd.to_datetime(df_combined['trade_date']).dt.date

            # 添加板块名称
            df_combined['sector_name'] = name

            # 计算涨跌幅
            if 'close' in df_combined.columns:
                df_combined['pct_chg'] = df_combined['close'].pct_change() * 100

            # 处理 amount 列
            if 'amount' not in df_combined.columns and 'volume' in df_combined.columns and 'close' in df_combined.columns:
                df_combined['amount'] = df_combined['volume'] * df_combined['close']

            # 选择需要的列
            cols = ['sector_name', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
            df_final = df_combined[[c for c in cols if c in df_combined.columns]]

            # 去重（同一sector_name + trade_date只保留一条）
            df_final = df_final.drop_duplicates(subset=['sector_name', 'trade_date'], keep='last')

            # 先删除该板块的旧数据
            conn.execute("DELETE FROM sector_daily_prices WHERE sector_name = ?", [name])

            # 保存数据
            df_final.to_sql('sector_daily_prices', conn, if_exists='append', index=False)
            conn.commit()
            success += 1

        except Exception as e:
            fail += 1
            print(f"\n❌ {name} ({code}): {e}")
            continue

        time.sleep(0.05)

    conn.close()

    print(f"\n✅ 行业指数 K线下载完成: 成功 {success}, 失败 {fail}")


if __name__ == "__main__":
    download_sector_indices()
