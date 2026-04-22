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

# 1. 基础统计
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

# 2. 数据覆盖率分析
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

# 日期转换
df_stats['last_date_dt'] = pd.to_datetime(df_stats['last_date']).dt.date
if isinstance(max_date, str):
    max_date_dt = datetime.strptime(max_date, '%Y-%m-%d').date()
else:
    max_date_dt = max_date

if isinstance(min_date, str):
    min_date_dt = datetime.strptime(min_date, '%Y-%m-%d').date()
else:
    min_date_dt = min_date

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

# 3. 最新数据情况
print(f"\n📅 最新数据情况 (最新日期: {max_date}):")

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

# 4. 数据缺失情况
print(f"\n❌ 数据缺失情况:")

result = engine.execute(text('SELECT symbol, name FROM stock_info'))
all_stocks_df = pd.read_sql(result, engine)
all_stocks = set(all_stocks_df['symbol'].tolist())

stocks_with_kline = set(df_stats['symbol'].tolist())
stocks_no_data = all_stocks - stocks_with_kline
stocks_insufficient = len(df_stats[df_stats['record_count'] < ideal_days * 0.5])

print(f"  完全无数据:     {len(stocks_no_data)} 只")
print(f"  数据不足50%:   {stocks_insufficient} 只")
print(f"  数据良好:      {total_stocks - stocks_insufficient} 只")

# 5. 显示样本数据
print(f"\n📋 数据量排名 (前15只):")
print(f"  {'排名':<6} {'股票代码':<10} {'数据量':<10} {'开始日期':<12} {'最新日期':<12}")
print(f"  {'-'*6} {'-'*10} {'-'*10} {'-'*12} {'-'*12}")

for idx, (_, row) in enumerate(df_stats.head(15).iterrows(), 1):
    # 查找股票名称
    symbol = row['symbol']
    name = ""
    try:
        result = engine.execute(text(f"SELECT name FROM stock_info WHERE symbol = '{symbol}'"))
        name_result = result.fetchone()
        if name_result:
            name = name_result[0]
    except:
        pass

    print(f"  {idx:<6} {symbol:<10} {int(row['record_count']):<10,} {str(row['first_date']):<12} {str(row['last_date']):<12}")

# 6. 数据质量检查
print(f"\n🔍 数据质量检查:")

with engine.connect() as conn:
    # 检查重复数据
    result = conn.execute(text('''
        SELECT COUNT(*) FROM (
            SELECT symbol, trade_date, COUNT(*) as cnt
            FROM stock_daily_prices
            GROUP BY symbol, trade_date
            HAVING cnt > 1
        )
    '''))
    duplicate_count = result.scalar()

    if duplicate_count > 0:
        print(f"  ⚠️  发现 {duplicate_count:,} 组重复数据")
    else:
        print(f"  ✅ 无重复数据")

    # 检查空值
    result = conn.execute(text('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN open IS NULL THEN 1 ELSE 0 END) as null_open,
            SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) as null_close,
            SUM(CASE WHEN volume IS NULL THEN 1 ELSE 0 END) as null_volume
        FROM stock_daily_prices
    '''))
    total, null_open, null_close, null_volume = result.fetchone()

    print(f"  空值检查:")
    print(f"     open为空:   {null_open:,} 条 ({null_open/total*100:.2f}%)")
    print(f"     close为空:  {null_close:,} 条 ({null_close/total*100:.2f}%)")
    print(f"     volume为空: {null_volume:,} 条 ({null_volume/total*100:.2f}%)")

# 7. 总结
print("\n" + "=" * 80)
print("📋 数据完整度总结")
print("=" * 80)

coverage = len(df_stats[df_stats['record_count'] >= ideal_days * 0.9]) / total_stocks * 100
freshness = len(df_stats[df_stats['last_date_dt'] == max_date_dt]) / total_stocks * 100

print(f"✅ 数据整体良好:")
print(f"   - {total_stocks} 只股票中有 {len(df_stats)} 只有K线数据")
print(f"   - 平均每只股票有 {total_records / total_stocks:.0f} 天数据")
print(f"   - 最新数据到 {max_date} ({freshness:.1f}% 股票是最新)")

if coverage >= 80:
    print(f"⭐ 数据完整度: 优秀 - {coverage:.1f}% 的股票有90%以上数据")
elif coverage >= 60:
    print(f"⭐ 数据完整度: 良好 - {coverage:.1f}% 的股票有90%以上数据")
else:
    print(f"⚠️  数据完整度: 需要补充 - 只有 {coverage:.1f}% 的股票有90%以上数据")

# 8. 建议
print(f"\n💡 建议:")

if freshness < 80:
    print(f"   ⚠️  最新数据不足 ({freshness:.1f}% < 80%)")
    print(f"   → 建议运行: python -m data_job.collectors.stock_kline_collector_optimized --mode normal")

if coverage < 80:
    print(f"   ⚠️  数据完整度不足 ({coverage:.1f}% < 80%)")
    print(f"   → 需要补充历史数据")

print("=" * 80)
