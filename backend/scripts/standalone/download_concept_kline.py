"""
概念板块K线下载器 - 使用 EFinance
下载479个概念板块的历史K线数据
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


def download_concept_kline_efinance():
    """使用 EFinance 下载概念板块K线"""
    print("\n" + "=" * 70)
    print("  下载概念板块 K线数据 (EFinance)")
    print("=" * 70)

    try:
        import efinance as ef
    except ImportError:
        print("⚠️  EFinance 未安装，请运行: pip install efinance")
        return

    conn = init_db()

    # 获取概念板块列表
    try:
        df_sectors = pd.read_sql(
            "SELECT sector_name FROM sector_list WHERE sector_type = 'Concept' ORDER BY sector_name",
            conn
        )
    except Exception as e:
        print(f"⚠️  无法获取板块列表: {e}")
        conn.close()
        return

    if df_sectors.empty:
        print("⚠️  概念板块列表为空")
        conn.close()
        return

    print(f"共 {len(df_sectors)} 个概念板块")

    # 获取已有数据
    try:
        df_has = pd.read_sql("SELECT DISTINCT sector_name FROM sector_daily_prices", conn)
        has_sectors = set(df_has['sector_name'].tolist()) if not df_has.empty else set()
    except:
        has_sectors = set()

    print(f"已下载: {len(has_sectors)} 个板块")

    # 过滤已下载的
    pending = df_sectors[~df_sectors['sector_name'].isin(has_sectors)]

    if pending.empty:
        print("✅ 所有概念板块数据已下载")
        conn.close()
        return

    print(f"需要下载: {len(pending)} 个概念板块\n")

    success = 0
    fail = 0
    errors = []

    for idx, row in tqdm(pending.iterrows(), total=len(pending), desc="下载进度"):
        sector_name = row['sector_name']

        try:
            # 直接用板块名称获取历史数据
            df = ef.stock.get_quote_history(sector_name)

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

                # 只保留最近3年
                three_years_ago = (datetime.now() - pd.DateOffset(years=3)).date()
                df = df[df['trade_date'] >= three_years_ago]

                # 数值转换
                for col in ['open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

                # 选择需要的列
                cols = ['sector_name', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']
                df_final = df[[c for c in cols if c in df.columns]]

                # 去重
                df_final = df_final.drop_duplicates(subset=['sector_name', 'trade_date'], keep='last')

                # 保存数据
                conn.execute("DELETE FROM sector_daily_prices WHERE sector_name = ?", [sector_name])
                df_final.to_sql('sector_daily_prices', conn, if_exists='append', index=False)
                conn.commit()
                success += 1
            else:
                fail += 1
                errors.append(f"{sector_name}: 无数据")

        except Exception as e:
            fail += 1
            errors.append(f"{sector_name}: {str(e)[:40]}")
            time.sleep(1)

        time.sleep(0.12)  # 避免请求过快

    conn.close()

    print(f"\n✅ 概念板块 K线下载完成: 成功 {success}, 失败 {fail}")

    if errors and len(errors) <= 20:
        print(f"\n失败列表:")
        for err in errors:
            print(f"  - {err}")


if __name__ == "__main__":
    download_concept_kline_efinance()
