"""
股票-板块映射采集器 - 使用通达信财务数据
从mootdx获取股票的行业分类信息
"""

import sys
import sqlite3
import pandas as pd
from pathlib import Path
from tqdm import tqdm
import time

# 添加 backend 到路径
backend_dir = Path(__file__).parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

DB_PATH = "data/local_quant.db"


def collect_sector_map_mootdx():
    """使用mootdx获取股票行业分类"""
    print("\n" + "=" * 70)
    print("  采集股票-板块映射 (mootdx行业分类)")
    print("=" * 70)

    from mootdx.quotes import Quotes

    api = Quotes.factory(market='std', server='218.75.126.9:7709', timeout=15)
    if api.client is None:
        print("❌ 无法连接")
        return

    print("✅ 连接成功")

    conn = sqlite3.connect(DB_PATH)

    # 获取股票列表
    df_stocks = pd.read_sql("SELECT symbol, name FROM stock_info WHERE symbol NOT LIKE '9%'", conn)
    stocks = df_stocks.to_dict('records')

    print(f"共 {len(stocks)} 只股票")

    # 清空旧数据
    conn.execute("DELETE FROM stock_sector_map")
    conn.commit()

    collected = []
    success = 0
    fail = 0

    for stock in tqdm(stocks, desc="采集行业"):
        try:
            code = stock['symbol']
            name = stock['name']

            # 获取财务信息（包含行业）
            market = 1 if code.startswith('6') else 0
            finance = api.finance(code, market=market)

            if finance is not None and not finance.empty:
                industry = finance.iloc[0].get('industry', '')
                if pd.notna(industry) and industry:
                    industry = str(industry).strip()
                else:
                    industry = '未分类'

                collected.append({
                    'symbol': code,
                    'name': name,
                    'sector_name': industry,
                    'sector_type': 'Industry'
                })
                success += 1
            else:
                collected.append({
                    'symbol': code,
                    'name': name,
                    'sector_name': '未分类',
                    'sector_type': 'Industry'
                })
                fail += 1

        except Exception as e:
            collected.append({
                'symbol': stock['symbol'],
                'name': stock['name'],
                'sector_name': '未分类',
                'sector_type': 'Industry'
            })
            fail += 1

        time.sleep(0.02)

    # 保存
    if collected:
        df_save = pd.DataFrame(collected)
        df_save = df_save.drop_duplicates(subset=['symbol', 'sector_name'])
        df_save.to_sql('stock_sector_map', conn, if_exists='append', index=False)
        conn.commit()

        print(f"\n✅ 保存完成: {len(df_save)} 条记录 (成功 {success}, 失败 {fail})")

        # 统计
        print("\n行业分布 TOP20:")
        stats = df_save['sector_name'].value_counts().head(20)
        for sector, count in stats.items():
            print(f"  {sector}: {count} 只")

    conn.close()


if __name__ == "__main__":
    collect_sector_map_mootdx()
