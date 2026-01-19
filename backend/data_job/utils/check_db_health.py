"""
EvoAlpha OS - 数据库健康检查脚本
快速检查所有数据表的健康状况
"""

import os
import sys

# 环境路径适配
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# 禁用SQLAlchemy日志
os.environ['SQLALCHEMY_SILENCE_UBER_WARNING'] = '1'
import logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.ERROR)
logging.getLogger('sqlalchemy').setLevel(logging.ERROR)

import pandas as pd
from datetime import datetime
from app.core.database import get_engine
from sqlalchemy import text


def check_table_health(table_name, engine, key_fields=None):
    """检查单个表的健康状况"""
    try:
        # 总记录数
        df_total = pd.read_sql(f"SELECT COUNT(*) as total FROM {table_name}", engine)
        total = df_total['total'].values[0]

        result = {
            'table': table_name,
            'total': total,
            'status': '✅',
            'issues': []
        }

        if total == 0:
            result['status'] = '⚠️'
            result['issues'].append('表为空')
            return result

        # 检查关键字段完整性
        if key_fields:
            for field in key_fields:
                try:
                    df_null = pd.read_sql(
                        f"SELECT SUM(CASE WHEN {field} IS NULL THEN 1 ELSE 0 END) as null_count FROM {table_name}",
                        engine
                    )
                    null_count = df_null['null_count'].values[0]
                    complete_pct = ((total - null_count) / total * 100)

                    if complete_pct < 90:
                        result['status'] = '❌'
                        result['issues'].append(f'{field}: {complete_pct:.1f}% 完整')
                    elif complete_pct < 99:
                        if result['status'] == '✅':
                            result['status'] = '⚠️'
                        result['issues'].append(f'{field}: {complete_pct:.1f}% 完整')
                except:
                    pass

        # 检查时间范围
        date_fields = ['trade_date', 'date', 'created_at', 'updated_at', 'publish_date']
        for field in date_fields:
            try:
                df_range = pd.read_sql(
                    f"SELECT MIN({field}) as min_dt, MAX({field}) as max_dt FROM {table_name} WHERE {field} IS NOT NULL",
                    engine
                )
                if not df_range.empty and df_range['max_dt'].values[0]:
                    result['min_date'] = str(df_range['min_dt'].values[0])
                    result['max_date'] = str(df_range['max_dt'].values[0])
                    break
            except:
                pass

        return result

    except Exception as e:
        return {
            'table': table_name,
            'total': 0,
            'status': '❌',
            'issues': [f'查询失败: {str(e)[:50]}']
        }


def main():
    print("\n" + "=" * 100)
    print("EvoAlpha OS - 数据库健康检查报告".center(100))
    print("=" * 100)
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    engine = get_engine()

    # 获取所有表
    with engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        all_tables = [row[0] for row in result.fetchall()]

    data_tables = [t for t in all_tables if not t.startswith('sqlite_')]

    print(f"📋 数据库中共有 {len(data_tables)} 个数据表\n")

    # 核心表配置
    core_tables_config = {
        'stock_daily_prices': {
            'name': '个股K线',
            'key_fields': ['symbol', 'trade_date', 'close', 'amount']
        },
        'sector_daily_prices': {
            'name': '板块K线',
            'key_fields': ['sector_name', 'trade_date', 'close', 'amount', 'pct_chg']
        },
        'etf_daily_prices': {
            'name': 'ETF K线',
            'key_fields': ['symbol', 'trade_date', 'close', 'amount', 'pct_chg']
        },
        'stock_info': {
            'name': '股票信息',
            'key_fields': ['symbol', 'name']
        },
        'etf_info': {
            'name': 'ETF信息',
            'key_fields': ['symbol', 'name']
        },
        'stock_sector_map': {
            'name': '板块映射',
            'key_fields': ['symbol', 'sector_name']
        }
    }

    print("=" * 100)
    print("🎯 核心数据表健康状态")
    print("=" * 100 + "\n")

    # 检查核心表
    core_ok = 0
    for table, config in core_tables_config.items():
        if table not in data_tables:
            continue

        health = check_table_health(table, engine, config['key_fields'])

        print(f"{health['status']} 【{config['name']}】{table}")
        print(f"   记录数: {health['total']:,}")

        if 'min_date' in health:
            print(f"   时间范围: {health['min_date']} ~ {health['max_date']}")

        if health['issues']:
            print(f"   问题:")
            for issue in health['issues']:
                print(f"      ⚠️  {issue}")
        else:
            print(f"   ✅ 数据健康")
            core_ok += 1

        print()

    print("=" * 100)
    print("📊 其他数据表统计")
    print("=" * 100 + "\n")

    # 其他表
    other_tables = [t for t in data_tables if t not in core_tables_config]
    other_ok = 0
    other_empty = 0

    for table in sorted(other_tables):
        health = check_table_health(table, engine)
        print(f"{health['status']} {table:35s}: {health['total']:>10,} 条")

        if health['status'] == '✅':
            other_ok += 1
        if health['total'] == 0:
            other_empty += 1

    # 总体统计
    print("\n" + "=" * 100)
    print("📈 健康统计")
    print("=" * 100)
    print(f"核心表健康率: {core_ok}/{len(core_tables_config)} ({core_ok/len(core_tables_config)*100:.1f}%)")
    print(f"其他表健康率: {other_ok}/{len(other_tables)} ({other_ok/len(other_tables)*100:.1f}%)")
    print(f"空表数量: {other_empty}")

    # 特别问题检测
    print("\n" + "=" * 100)
    print("🔍 特殊问题检测")
    print("=" * 100 + "\n")

    # 1. 检查 sector_daily_prices 的零值问题
    if 'sector_daily_prices' in data_tables:
        df = pd.read_sql("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN amount = 0 THEN 1 ELSE 0 END) as zero_amount,
                SUM(CASE WHEN pct_chg IS NULL THEN 1 ELSE 0 END) as null_pct_chg
            FROM sector_daily_prices
        """, engine)
        print("1. 板块K线数据 (sector_daily_prices):")
        print(f"   零值成交额: {df['zero_amount'].values[0]:,} ({df['zero_amount'].values[0]/df['total'].values[0]*100:.2f}%)")
        print(f"   NULL涨跌幅: {df['null_pct_chg'].values[0]:,} ({df['null_pct_chg'].values[0]/df['total'].values[0]*100:.2f}%)")

    # 2. 检查 etf_daily_prices
    if 'etf_daily_prices' in data_tables:
        df = pd.read_sql("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN amount = 0 THEN 1 ELSE 0 END) as zero_amount,
                SUM(CASE WHEN pct_chg IS NULL THEN 1 ELSE 0 END) as null_pct_chg
            FROM etf_daily_prices
        """, engine)
        if df['total'].values[0] > 0:
            print("\n2. ETF K线数据 (etf_daily_prices):")
            print(f"   零值成交额: {df['zero_amount'].values[0]:,} ({df['zero_amount'].values[0]/df['total'].values[0]*100:.2f}%)")
            print(f"   NULL涨跌幅: {df['null_pct_chg'].values[0]:,} ({df['null_pct_chg'].values[0]/df['total'].values[0]*100:.2f}%)")

    # 3. 数据新鲜度
    print("\n3. 数据新鲜度检查:")
    for table, date_col, desc in [
        ('stock_daily_prices', 'trade_date', '个股K线'),
        ('sector_daily_prices', 'trade_date', '板块K线'),
        ('etf_daily_prices', 'trade_date', 'ETF K线'),
    ]:
        try:
            df = pd.read_sql(f"SELECT MAX({date_col}) as max_date FROM {table}", engine)
            if not df.empty and df['max_date'].values[0]:
                max_date = pd.to_datetime(df['max_date'].values[0])
                days_old = (datetime.now() - max_date).days
                status = "✅" if days_old <= 2 else "⚠️" if days_old <= 7 else "❌"
                print(f"   {status} {desc:10s}: {days_old} 天前")
        except:
            pass

    print("\n" + "=" * 100)
    print("✅ 健康检查完成！")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    main()
