"""
数据库表数据预览脚本
查询所有正式使用的数据库表，打印最近两个交易日的数据各5条
"""
import sys
import os
from pathlib import Path

# 获取backend目录路径
current_dir = Path(__file__).parent.absolute()
backend_dir = current_dir.parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd
from app.core.database import get_engine
from datetime import datetime
import logging

# 禁用SQLAlchemy的日志输出
logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
logging.getLogger('app').setLevel(logging.WARNING)

engine = get_engine()

print('=' * 100)
print('📊 数据库表清单及最近两个交易日数据预览')
print('=' * 100)
print(f'查询时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
print()

# 定义每个表的配置
table_configs = {
    'stock_daily_prices': {
        'time_col': 'trade_date',
        'cols': ['symbol', 'trade_date', 'close', 'volume', 'amount'],
        'name': '个股K线数据'
    },
    'sector_daily_prices': {
        'time_col': 'trade_date',
        'cols': ['sector_name', 'trade_date', 'close', 'volume', 'amount'],
        'name': '板块K线数据'
    },
    'etf_daily_prices': {
        'time_col': 'trade_date',
        'cols': ['symbol', 'trade_date', 'close', 'volume'],
        'name': 'ETF K线数据'
    },
    'stock_valuation_daily': {
        'time_col': 'trade_date',
        'cols': ['code', 'trade_date', 'price', 'pe_ttm', 'pb', 'total_mv'],
        'name': '股票估值数据'
    },
    'macro_indicators': {
        'time_col': 'publish_date',
        'cols': ['indicator_name', 'period', 'value', 'unit'],
        'name': '宏观指标数据'
    },
    'limit_board_trading': {
        'time_col': 'trade_date',
        'cols': ['trade_date', 'symbol', 'name', 'pct_chg', 'boards'],
        'name': '连板交易数据'
    },
    'consecutive_boards_stats': {
        'time_col': 'trade_date',
        'cols': ['trade_date', 'boards', 'stock_count'],
        'name': '连板统计数据'
    },
    'stock_northbound_holdings': {
        'time_col': 'hold_date',
        'cols': ['symbol', 'hold_date', 'close_price', 'hold_amount', 'hold_value', 'hold_ratio'],
        'name': '北向资金持股数据'
    },
    'finance_fund_holdings': {
        'time_col': 'report_date',
        'cols': ['symbol', 'report_date', 'fund_count', 'hold_value'],
        'name': '基金持仓数据'
    },
    'stock_finance_summary': {
        'time_col': 'report_date',
        'cols': ['code', 'report_date', 'eps', 'roe', 'revenue_up'],
        'name': '财务摘要数据'
    },
    'stock_info': {
        'time_col': None,
        'cols': ['symbol', 'name'],
        'name': '股票基础信息'
    },
    'stock_sector_map': {
        'time_col': None,
        'cols': ['symbol', 'name', 'sector_name', 'sector_type'],
        'name': '股票板块映射'
    },
    'etf_info': {
        'time_col': None,
        'cols': ['symbol', 'name', 'fund_type'],
        'name': 'ETF基础信息'
    },
}

# 查询所有表
all_tables = list(table_configs.keys())

# 查询每个表
for table_name in all_tables:
    if table_name not in table_configs:
        continue

    config = table_configs[table_name]
    time_col = config['time_col']
    cols = config['cols']
    table_display_name = config['name']

    print("\n" + "=" * 100)
    print(f"📋 表名: {table_name} ({table_display_name})")
    print("=" * 100)

    try:
        # 查询总记录数
        count_df = pd.read_sql(f"SELECT COUNT(*) as total FROM {table_name}", engine)
        total = count_df['total'].values[0]
        print(f"📊 总记录数: {total:,} 条")

        if total == 0:
            print("⚠️  表为空，跳过")
            continue

        # 如果没有时间列，直接显示前5条
        if time_col is None:
            print(f"\n📋 前5条样本数据：")
            cols_str = ', '.join(cols)
            query = f"SELECT {cols_str} FROM {table_name} LIMIT 5"
            sample_df = pd.read_sql(query, engine)

            if not sample_df.empty:
                pd.set_option('display.max_columns', None)
                pd.set_option('display.width', 150)
                print(sample_df.to_string(index=False))
            else:
                print("(无数据)")
            continue

        # 获取最近2个日期
        date_df = pd.read_sql(
            f"SELECT DISTINCT {time_col} FROM {table_name} ORDER BY {time_col} DESC LIMIT 2",
            engine
        )

        if date_df.empty:
            print("⚠️  没有时间数据")
            continue

        print(f"\n📅 最近2个交易日: {date_df[time_col].tolist()}")

        # 查询每个日期的5条数据
        for date_val in date_df[time_col]:
            print(f"\n📅 日期: {date_val} (5条样本)")

            # 构建查询（使用字符串格式避免参数问题）
            cols_str = ', '.join(cols)
            date_str = str(date_val) if not isinstance(date_val, str) else date_val
            query = f"SELECT {cols_str} FROM {table_name} WHERE {time_col} = '{date_str}' ORDER BY rowid LIMIT 5"
            sample_df = pd.read_sql(query, engine)

            if not sample_df.empty:
                # 调整显示
                pd.set_option('display.max_columns', None)
                pd.set_option('display.width', 150)
                print(sample_df.to_string(index=False))
            else:
                print("(无数据)")

    except Exception as e:
        print(f"❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "=" * 100)
print("✅ 数据预览完成")
print("=" * 100)
