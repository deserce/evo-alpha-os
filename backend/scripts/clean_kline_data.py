"""
K线数据清洗脚本
清理数据库中的错误数据：
1. 删除日期格式错误的记录
2. 删除未来日期的记录
3. 验证数据合理性
"""

import sys
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime

# 添加 backend 到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

DB_PATH = "data/local_quant.db"


def clean_stock_daily_prices(db_path, dry_run=False):
    """清洗 stock_daily_prices 表"""
    print("\n" + "="*60)
    print("清洗 stock_daily_prices 表")
    print("="*60)

    conn = sqlite3.connect(db_path)

    # 总记录数
    total = conn.execute("SELECT COUNT(*) FROM stock_daily_prices").fetchone()[0]
    print(f"\n总记录数: {total:,}")

    # 检查日期格式错误的记录
    bad_format = conn.execute("""
        SELECT COUNT(*) FROM stock_daily_prices
        WHERE trade_date NOT GLOB '####-##-##'
           OR length(trade_date) != 10
    """).fetchone()[0]

    if bad_format > 0:
        print(f"⚠️  日期格式错误的记录: {bad_format:,}")

        # 查看示例
        samples = conn.execute("""
            SELECT DISTINCT trade_date, COUNT(*) as cnt
            FROM stock_daily_prices
            WHERE trade_date NOT GLOB '####-##-##'
               OR length(trade_date) != 10
            GROUP BY trade_date
            LIMIT 10
        """).fetchall()
        print("   示例错误日期:")
        for date, cnt in samples:
            print(f"     {date}: {cnt} 条")

        if not dry_run:
            conn.execute("""
                DELETE FROM stock_daily_prices
                WHERE trade_date NOT GLOB '####-##-##'
                   OR length(trade_date) != 10
            """)
            conn.commit()
            print(f"   ✅ 已删除 {bad_format:,} 条格式错误记录")
    else:
        print("✅ 日期格式检查通过")

    # 检查未来日期
    future_dates = conn.execute("""
        SELECT COUNT(*) FROM stock_daily_prices
        WHERE trade_date > date('now')
    """).fetchone()[0]

    if future_dates > 0:
        print(f"⚠️  未来日期记录: {future_dates:,}")

        # 查看示例
        samples = conn.execute("""
            SELECT trade_date, COUNT(*) as cnt
            FROM stock_daily_prices
            WHERE trade_date > date('now')
            GROUP BY trade_date
            ORDER BY trade_date DESC
        """).fetchall()
        print("   未来日期示例:")
        for date, cnt in samples[:10]:
            print(f"     {date}: {cnt} 条")

        if not dry_run:
            conn.execute("""
                DELETE FROM stock_daily_prices
                WHERE trade_date > date('now')
            """)
            conn.commit()
            print(f"   ✅ 已删除 {future_dates:,} 条未来日期记录")
    else:
        print("✅ 未来日期检查通过")

    # 检查价格数据异常
    df_check = pd.read_sql_query("""
        SELECT symbol, trade_date, open, high, low, close, volume
        FROM stock_daily_prices
        WHERE high < low
           OR close > high
           OR close < low
           OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
        LIMIT 100
    """, conn)

    if not df_check.empty:
        print(f"⚠️  发现 {len(df_check)} 条价格异常记录")
        print(df_check.head(10).to_string())

        if not dry_run:
            conn.execute("""
                DELETE FROM stock_daily_prices
                WHERE high < low
                   OR close > high
                   OR close < low
                   OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
            """)
            conn.commit()
            print(f"   ✅ 已删除异常记录")
    else:
        print("✅ 价格数据检查通过")

    # 统计清洗后的数据
    cleaned_total = conn.execute("SELECT COUNT(*) FROM stock_daily_prices").fetchone()[0]
    print(f"\n清洗后记录数: {cleaned_total:,}")
    print(f"删除记录数: {total - cleaned_total:,}")

    # 数据分布
    stats = conn.execute("""
        SELECT
            substr(trade_date, 1, 4) as year,
            COUNT(DISTINCT symbol) as symbols,
            COUNT(*) as records,
            MIN(trade_date) as min_date,
            MAX(trade_date) as max_date
        FROM stock_daily_prices
        GROUP BY substr(trade_date, 1, 4)
        ORDER BY year
    """).fetchall()

    print("\n数据分布:")
    print(f"{'年份':<6} {'股票数':<8} {'记录数':<10} {'日期范围'}")
    print("-" * 60)
    for year, symbols, records, min_date, max_date in stats:
        print(f"{year:<6} {symbols:<8} {records:<10,} {min_date} ~ {max_date}")

    conn.close()


def clean_sector_daily_prices(db_path, dry_run=False):
    """清洗 sector_daily_prices 表"""
    print("\n" + "="*60)
    print("清洗 sector_daily_prices 表")
    print("="*60)

    conn = sqlite3.connect(db_path)

    total = conn.execute("SELECT COUNT(*) FROM sector_daily_prices").fetchone()[0]
    print(f"\n总记录数: {total:,}")

    # 删除未来日期
    future = conn.execute("""
        SELECT COUNT(*) FROM sector_daily_prices
        WHERE trade_date > date('now')
    """).fetchone()[0]

    if future > 0:
        print(f"⚠️  未来日期记录: {future:,}")
        if not dry_run:
            conn.execute("""
                DELETE FROM sector_daily_prices
                WHERE trade_date > date('now')
            """)
            conn.commit()
            print(f"   ✅ 已删除")
    else:
        print("✅ 未来日期检查通过")

    cleaned_total = conn.execute("SELECT COUNT(*) FROM sector_daily_prices").fetchone()[0]
    print(f"\n清洗后记录数: {cleaned_total:,}")

    conn.close()


def clean_etf_daily_prices(db_path, dry_run=False):
    """清洗 etf_daily_prices 表"""
    print("\n" + "="*60)
    print("清洗 etf_daily_prices 表")
    print("="*60)

    conn = sqlite3.connect(db_path)

    total = conn.execute("SELECT COUNT(*) FROM etf_daily_prices").fetchone()[0]
    print(f"\n总记录数: {total:,}")

    # 删除未来日期
    future = conn.execute("""
        SELECT COUNT(*) FROM etf_daily_prices
        WHERE trade_date > date('now')
    """).fetchone()[0]

    if future > 0:
        print(f"⚠️  未来日期记录: {future:,}")
        if not dry_run:
            conn.execute("""
                DELETE FROM etf_daily_prices
                WHERE trade_date > date('now')
            """)
            conn.commit()
            print(f"   ✅ 已删除")
    else:
        print("✅ 未来日期检查通过")

    # 检查日期格式
    bad_format = conn.execute("""
        SELECT COUNT(*) FROM etf_daily_prices
        WHERE trade_date NOT GLOB '####-##-##'
    """).fetchone()[0]

    if bad_format > 0:
        print(f"⚠️  日期格式错误: {bad_format:,}")
        if not dry_run:
            conn.execute("""
                DELETE FROM etf_daily_prices
                WHERE trade_date NOT GLOB '####-##-##'
            """)
            conn.commit()
            print(f"   ✅ 已删除")

    cleaned_total = conn.execute("SELECT COUNT(*) FROM etf_daily_prices").fetchone()[0]
    print(f"\n清洗后记录数: {cleaned_total:,}")

    conn.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="清洗K线数据")
    parser.add_argument('--dry-run', action='store_true', help='只检查不修改')
    parser.add_argument('--table', choices=['all', 'stock', 'sector', 'etf'],
                       default='all', help='指定清洗的表')
    parser.add_argument('--db', default=DB_PATH, help='数据库路径')

    args = parser.parse_args()

    print("="*60)
    print("K线数据清洗工具")
    print("="*60)
    print(f"数据库: {args.db}")

    if args.dry_run:
        print("\n⚠️  DRY RUN 模式 - 只检查不修改")

    if args.table in ['all', 'stock']:
        clean_stock_daily_prices(args.db, args.dry_run)

    if args.table in ['all', 'sector']:
        clean_sector_daily_prices(args.db, args.dry_run)

    if args.table in ['all', 'etf']:
        clean_etf_daily_prices(args.db, args.dry_run)

    print("\n" + "="*60)
    print("清洗完成！")
    print("="*60)

    if args.dry_run:
        print("\n去掉 --dry-run 参数执行实际清洗")


if __name__ == "__main__":
    main()
