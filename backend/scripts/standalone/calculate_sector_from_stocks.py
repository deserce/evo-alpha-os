"""
板块K线生成器 - 从个股数据计算板块指数
当无法从外部获取板块数据时，可以从成分股计算板块指数
"""

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from tqdm import tqdm

DB_PATH = "data/local_quant.db"


def calculate_sector_kline_from_stocks():
    """从个股数据计算板块K线"""
    print("\n" + "=" * 70)
    print("  计算板块 K线数据 (从个股数据)")
    print("=" * 70)

    conn = sqlite3.connect(DB_PATH)

    # 获取板块列表
    try:
        df_sectors = pd.read_sql("""
            SELECT sector_name, sector_type
            FROM sector_list
            WHERE sector_type = 'Industry'
            ORDER BY sector_name
        """, conn)
    except Exception as e:
        print(f"⚠️  无法获取板块列表: {e}")
        conn.close()
        return

    if df_sectors.empty:
        print("⚠️  板块列表为空")
        conn.close()
        return

    print(f"共 {len(df_sectors)} 个行业板块")

    # 获取股票-板块映射
    try:
        df_map = pd.read_sql("""
            SELECT s.symbol, s.sector_name
            FROM stock_sector_map s
            JOIN stock_daily_prices k ON s.symbol = k.symbol
            GROUP BY s.symbol, s.sector_name
        """, conn)
    except Exception as e:
        print(f"⚠️  无法获取股票-板块映射: {e}")
        conn.close()
        return

    if df_map.empty:
        print("⚠️  股票-板块映射为空")
        conn.close()
        return

    print(f"股票-板块映射: {len(df_map)} 条")

    # 获取所有交易日期
    try:
        df_dates = pd.read_sql("""
            SELECT DISTINCT trade_date
            FROM stock_daily_prices
            ORDER BY trade_date DESC
            LIMIT 800
        """, conn)
    except Exception as e:
        print(f"⚠️  无法获取交易日期: {e}")
        conn.close()
        return

    if df_dates.empty:
        print("⚠️  无交易日期")
        conn.close()
        return

    trade_dates = df_dates['trade_date'].tolist()
    print(f"交易日期: {len(trade_dates)} 个")

    # 为每个板块计算K线
    success = 0
    fail = 0

    for idx, row in tqdm(df_sectors.iterrows(), total=len(df_sectors), desc="计算进度"):
        sector_name = row['sector_name']

        try:
            # 获取板块的成分股
            stocks = df_map[df_map['sector_name'] == sector_name]['symbol'].tolist()

            if not stocks:
                fail += 1
                continue

            # 获取成分股的K线数据
            placeholders = ','.join(['?'] * len(stocks))
            query = f"""
                SELECT trade_date,
                       SUM(open) / SUM(CASE WHEN open > 0 THEN 1 ELSE 0 END) as open,
                       SUM(close) / SUM(CASE WHEN close > 0 THEN 1 ELSE 0 END) as close,
                       SUM(high) / SUM(CASE WHEN high > 0 THEN 1 ELSE 0 END) as high,
                       SUM(low) / SUM(CASE WHEN low > 0 THEN 1 ELSE 0 END) as low,
                       SUM(volume) as volume,
                       SUM(amount) as amount
                FROM stock_daily_prices
                WHERE symbol IN ({placeholders})
                  AND trade_date IN ({','.join(['?'] * len(trade_dates))})
                GROUP BY trade_date
                ORDER BY trade_date
            """

            params = stocks + trade_dates
            df_sector = pd.read_sql(query, conn, params=params)

            if df_sector.empty:
                fail += 1
                continue

            # 计算涨跌幅
            df_sector['pct_chg'] = df_sector['close'].pct_change() * 100

            # 添加板块名称
            df_sector['sector_name'] = sector_name

            # 选择需要的列
            df_final = df_sector[['sector_name', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']]

            # 保存数据
            df_final.to_sql('sector_daily_prices_tmp', conn, if_exists='replace', index=False)
            conn.execute("INSERT OR IGNORE INTO sector_daily_prices SELECT * FROM sector_daily_prices_tmp")
            conn.execute("DROP TABLE sector_daily_prices_tmp")
            conn.commit()
            success += 1

        except Exception as e:
            fail += 1
            continue

    conn.close()

    print(f"\n✅ 板块 K线计算完成: 成功 {success}, 失败 {fail}")


if __name__ == "__main__":
    calculate_sector_kline_from_stocks()
