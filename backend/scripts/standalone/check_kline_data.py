"""
个股K线数据完整度分析
"""

from app.core.database import get_engine
from sqlalchemy import text
import pandas as pd
from datetime import datetime, timedelta

engine = get_engine()

print("=" * 80)
print("📊 个股K线数据完整度分析")
print("=" * 80)

# 基础统计
print("\n📈 基础统计:")
with engine.connect() as conn:
    result = conn.execute(text('SELECT COUNT(*) FROM stock_daily_prices'))
    total_records = result.scalar()

    result = conn.execute(text('SELECT COUNT(DISTINCT symbol) FROM stock_daily_prices'))
    total_stocks = result.scalar()

    result = conn.execute(text('SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily_prices'))
    min_date, max_date = result.fetchone()

    result = conn.execute(text('SELECT COUNT(DISTINCT trade_date) FROM stock_daily_prices'))
    trading_days = result.scalar()

    print(f"  总记录数:       {total_records:,} 条")
    print(f"  股票数量:       {total_stocks:,} 只")
    print(f"  日期范围:       {min_date} 至 {max_date}")
    print(f"  交易日数量:     {trading_days} 天")
    print(f"  平均每只:       {total_records / total_stocks:.1f} 条")

# 数据覆盖率
print(f"\n📊 数据覆盖率分析:")

with engine.connect() as conn:
    query = text('''
        SELECT
            symbol,
            COUNT(*) as record_count,
            MIN(trade_date) as first_date,
            MAX(trade_date) as last_date
        FROM stock_daily_prices
        GROUP BY symbol
        ORDER BY record_count DESC
    ''')
    df_stats = pd.read_sql(query, conn)

    ideal_days = trading_days

    data_distribution = {
        "100% 数据": len(df_stats[df_stats['record_count'] >= ideal_days]),
        ">=90% 数据": len(df_stats[df_stats['record_count'] >= ideal_days * 0.9]),
        ">=70% 数据": len(df_stats[df_stats['record_count'] >= ideal_days * 0.7]),
        ">=50% 数据": len(df_stats[df_stats['record_count'] >= ideal_days * 0.5]),
        "< 50% 数据": len(df_stats[df_stats['record_count'] < ideal_days * 0.5]),
    }

    for label, count in data_distribution.items():
        pct = count / len(df_stats) * 100
        print(f"  {label:15s}: {count:6,} 只 ({pct:5.2f}%)")

# 最新数据
print(f"\n📅 最新数据情况:")
df_stats['last_date_dt'] = pd.to_datetime(df_stats['last_date']).dt.date
if isinstance(max_date, str):
    max_date_dt = datetime.strptime(max_date, '%Y-%m-%d').date()
else:
    max_date_dt = max_date

seven_days_ago = max_date_dt - timedelta(days=7)

latest_data_stats = {
    "最新日期": len(df_stats[df_stats['last_date_dt'] == max_date_dt]),
    "3天内": len(df_stats[df_stats['last_date_dt'] >= max_date_dt - timedelta(days=3)]),
    "7天内": len(df_stats[df_stats['last_date_dt'] >= seven_days_ago]),
    "更早": len(df_stats[df_stats['last_date_dt'] < seven_days_ago]),
}

for label, count in latest_data_stats.items():
    pct = count / len(df_stats) * 100
    print(f"  {label:15s}: {count:6,} 只 ({pct:5.2f}%)")

# 数据缺失
print(f"\n❌ 数据缺失情况:")
with engine.connect() as conn:
    all_stocks_df = pd.read_sql(text('SELECT symbol, name FROM stock_info'), conn)
all_stocks = set(all_stocks_df['symbol'].tolist())
stocks_with_kline = set(df_stats['symbol'].tolist())
stocks_no_data = all_stocks - stocks_with_kline
stocks_insufficient = len(df_stats[df_stats['record_count'] < ideal_days * 0.5])

print(f"  完全无数据:     {len(stocks_no_data)} 只")
print(f"  数据不足50%:   {stocks_insufficient} 只")
print(f"  数据良好:      {total_stocks - stocks_insufficient} 只")

# 数据样本
print(f"\n📋 数据量排名 (前10只):")
print(f"  {'排名':<6} {'股票代码':<10} {'数据量':<10} {'最新日期':<12}")

for idx, (_, row) in enumerate(df_stats.head(10).iterrows(), 1):
    print(f"  {idx:<6} {row['symbol']:<10} {int(row['record_count']):<10,} {str(row['last_date']):<12}")

# 总结
print("\n" + "=" * 80)
print("📋 数据完整度总结")
print("=" * 80)

coverage = len(df_stats[df_stats['record_count'] >= ideal_days * 0.9]) / total_stocks * 100
freshness = len(df_stats[df_stats['last_date_dt'] == max_date_dt]) / total_stocks * 100

print(f"✅ 数据整体良好:")
print(f"   - {total_stocks} 只股票中有 {len(df_stats)} 只有K线数据")
print(f"   - 平均每只股票有 {total_records / total_stocks:.0f} 天数据")
print(f"   - 最新数据到 {max_date} ({freshness:.1f}% 股票是最新)")
print(f"   - {coverage:.1f}% 的股票有90%以上数据")

print("=" * 80)
