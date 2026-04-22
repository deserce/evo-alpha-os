"""
概念板块K线下载器 - 使用 mootdx
下载600+个概念板块的历史K线数据
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
    return conn


def download_concept_kline_mootdx():
    """使用 mootdx 下载概念板块K线"""
    print("\n" + "=" * 70)
    print("  下载概念板块 K线数据 (mootdx)")
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

    # 获取深圳市场的板块列表（880开头）
    try:
        stocks = api.stocks(market=1)
        # 过滤880开头的板块（排除已下载的行业指数）
        sector_indices = stocks[
            stocks['code'].str.match(r'^880\d{3}$')
        ].copy()
    except Exception as e:
        print(f"⚠️  获取板块列表失败: {e}")
        return

    if sector_indices.empty:
        print("⚠️  无板块数据")
        return

    print(f"共 {len(sector_indices)} 个板块")

    # 创建板块名称映射（去除"指数"、"概念"等后缀）
    index_map = {}
    for _, row in sector_indices.iterrows():
        code = row['code']
        name = row['name'].replace('指数', '').replace('概念', '').replace('板块', '').strip()
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
        print("✅ 所有板块数据已下载")
        conn.close()
        return

    print(f"已下载: {len(has_sectors)} 个板块")
    print(f"需要下载: {len(pending)} 个板块\n")

    success = 0
    fail = 0
    errors = []

    for code, name in tqdm(pending.items(), desc="下载进度"):
        try:
            # 获取板块K线数据
            all_dfs = []
            offset = 800

            for i in range(15):
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
                errors.append(f"{name} ({code}): 无数据")
                continue

            # 合并所有数据
            df_combined = pd.concat(all_dfs, ignore_index=True)

            # 处理列名
            df_combined = df_combined.loc[:, ~df_combined.columns.duplicated(keep='first')]

            column_map = {}
            if 'vol' in df_combined.columns and 'volume' not in df_combined.columns:
                column_map['vol'] = 'volume'
            if 'datetime' in df_combined.columns:
                column_map['datetime'] = 'trade_date'

            if column_map:
                df_combined = df_combined.rename(columns=column_map)

            if df_combined.columns.duplicated().any():
                df_combined = df_combined.loc[:, ~df_combined.columns.duplicated(keep='last')]

            # 确保有必要的列
            required_cols = ['trade_date', 'open', 'close', 'high', 'low']
            if not all(c in df_combined.columns for c in required_cols):
                fail += 1
                errors.append(f"{name} ({code}): 缺少必要列")
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

            # 去重
            df_final = df_final.drop_duplicates(subset=['sector_name', 'trade_date'], keep='last')

            # 先删除旧数据，再保存
            conn.execute("DELETE FROM sector_daily_prices WHERE sector_name = ?", [name])
            df_final.to_sql('sector_daily_prices', conn, if_exists='append', index=False)
            conn.commit()
            success += 1

        except Exception as e:
            fail += 1
            errors.append(f"{name} ({code}): {str(e)[:30]}")
            continue

        time.sleep(0.05)

    conn.close()

    print(f"\n✅ 板块 K线下载完成: 成功 {success}, 失败 {fail}")

    if errors and len(errors) <= 20:
        print(f"\n失败列表:")
        for err in errors:
            print(f"  - {err}")


if __name__ == "__main__":
    download_concept_kline_mootdx()
