"""
检查数据库数据完整性
查看所有表的数据量、日期范围等统计信息
"""

import sys
from pathlib import Path
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import sqlite3
from datetime import datetime, timedelta
import pandas as pd

def check_database():
    """检查数据库完整性"""

    db_path = "data/local_quant.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("=" * 80)
    print("数据库数据完整性检查")
    print("=" * 80)

    # 获取所有表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row[0] for row in cursor.fetchall()]

    print(f"\n数据库中共有 {len(tables)} 张表\n")

    # 检查每张表的数据情况
    table_stats = {}

    for table in tables:
        try:
            # 获取总行数
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            total_rows = cursor.fetchone()[0]

            # 获取表结构
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]

            # 检查是否有日期相关字段
            date_column = None
            for col in ['trade_date', 'date', 'timestamp', 'time', 'datetime']:
                if col in columns:
                    date_column = col
                    break

            date_range = "N/A"
            if date_column:
                try:
                    cursor.execute(f"SELECT MIN({date_column}), MAX({date_column}) FROM {table}")
                    min_date, max_date = cursor.fetchone()
                    if min_date and max_date:
                        date_range = f"{min_date} ~ {max_date}"
                except:
                    pass

            table_stats[table] = {
                'rows': total_rows,
                'columns': len(columns),
                'date_column': date_column,
                'date_range': date_range
            }

            # 打印表信息
            print(f"📊 表: {table}")
            print(f"   行数: {total_rows:,}")
            print(f"   列数: {len(columns)}")
            if date_column:
                print(f"   日期字段: {date_column}")
                print(f"   日期范围: {date_range}")
            print()

        except Exception as e:
            print(f"❌ 表 {table} 检查失败: {e}\n")

    # 详细检查核心表
    print("=" * 80)
    print("核心数据表详细检查")
    print("=" * 80)

    core_tables = {
        'stock_kline_daily': '股票日线数据',
        'sector_kline_daily': '板块日线数据',
        'etf_kline_daily': 'ETF日线数据',
        'stock_valuation': '股票估值数据',
        'stock_sector_list': '股票板块列表',
        'index_data': '指数数据',
    }

    for table_name, table_desc in core_tables.items():
        if table_name not in table_stats:
            print(f"\n⚠️  {table_desc} ({table_name}) - 表不存在")
            continue

        stats = table_stats[table_name]
        print(f"\n📈 {table_desc} ({table_name})")

        if stats['rows'] == 0:
            print("   ❌ 无数据")
            continue

        # 检查最近数据日期
        if stats['date_column']:
            try:
                date_col = stats['date_column']
                cursor.execute(f"""
                    SELECT {date_col}, COUNT(*) as cnt
                    FROM {table_name}
                    GROUP BY {date_col}
                    ORDER BY {date_col} DESC
                    LIMIT 1
                """)
                latest_date, count = cursor.fetchone()
                print(f"   最新日期: {latest_date} (记录数: {count})")

                # 检查是否应该有今天的数据
                today = datetime.now().strftime('%Y-%m-%d')
                yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

                if latest_date < yesterday:
                    print(f"   ⚠️  数据可能过期 (最新: {latest_date}, 昨天: {yesterday})")
            except Exception as e:
                print(f"   检查日期失败: {e}")

    # 检查量化因子表
    print("\n" + "=" * 80)
    print("量化因子表检查")
    print("=" * 80)

    factor_tables = [t for t in tables if t.startswith('quant_feature_')]
    if factor_tables:
        for table in factor_tables:
            stats = table_stats.get(table, {})
            print(f"\n📊 {table}")
            print(f"   行数: {stats.get('rows', 0):,}")
            if stats.get('date_range'):
                print(f"   日期范围: {stats['date_range']}")
    else:
        print("\n⚠️  没有量化因子表")

    # 检查股票池
    print("\n" + "=" * 80)
    print("股票池检查")
    print("=" * 80)

    if 'stock_pool' in tables:
        cursor.execute("SELECT COUNT(*) FROM stock_pool WHERE is_active=1")
        active_count = cursor.fetchone()[0]
        print(f"\n活跃股票数: {active_count:,}")
    else:
        print("\n⚠️  stock_pool表不存在")

    # 总结
    print("\n" + "=" * 80)
    print("数据完整性总结")
    print("=" * 80)

    # 检查关键表是否有数据
    critical_tables = ['stock_kline_daily', 'sector_kline_daily']
    missing_data = []

    for table in critical_tables:
        if table not in table_stats or table_stats[table]['rows'] == 0:
            missing_data.append(table)

    if missing_data:
        print(f"\n⚠️  关键表缺失数据: {', '.join(missing_data)}")
        print("\n建议操作:")
        print("1. 运行K线数据采集")
        print("2. 运行估值数据采集")
        print("3. 运行其他基础数据采集")
    else:
        print("\n✅ 关键表都有数据")

        # 检查数据是否新鲜
        fresh = True
        for table in critical_tables:
            stats = table_stats[table]
            if stats['date_column']:
                try:
                    date_col = stats['date_column']
                    cursor.execute(f"SELECT MAX({date_col}) FROM {table}")
                    max_date = cursor.fetchone()[0]
                    if max_date:
                        latest = pd.to_datetime(max_date)
                        if latest < pd.Timestamp.now() - pd.Timedelta(days=7):
                            print(f"⚠️  {table} 数据可能过旧 (最新: {max_date})")
                            fresh = False
                except:
                    pass

        if fresh:
            print("✅ 数据较新")

    # 检查量化因子
    if not factor_tables:
        print("\n⚠️  量化因子未计算")
        print("建议操作: 运行RPS等因子计算")
    else:
        print("\n✅ 量化因子已计算")

    conn.close()

if __name__ == "__main__":
    check_database()
