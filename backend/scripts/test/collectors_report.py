"""
数据采集器全景测试报告
检查所有采集器的可用性和数据状态
"""

import sys
import os
from pathlib import Path

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import logging
from datetime import datetime
import pandas as pd

logging.basicConfig(
    level=logging.WARNING,  # 减少日志输出
    format='%(levelname)s - %(message)s'
)

# 采集器清单
COLLECTORS = {
    # K线数据
    'stock_kline_collector': {
        'name': '股票K线采集器（基础版）',
        'module': 'data_job.collectors.stock_kline_collector',
        'class': 'StockKlineCollector',
        'description': '采集股票日K线数据，使用AKShare',
        'data_type': 'stock_kline',
        'priority': 'LOW'  # 有更好版本
    },

    'stock_kline_collector_optimized': {
        'name': '股票K线采集器（优化版）',
        'module': 'data_job.collectors.stock_kline_collector_optimized',
        'class': 'StockKlineCollectorOptimized',
        'description': '优化版股票K线采集，增加重试和错误处理',
        'data_type': 'stock_kline',
        'priority': 'LOW'  # 有更好版本
    },

    'optimized_kline_collector': {
        'name': '股票K线采集器（极低并发版）',
        'module': 'data_job.collectors.optimized_kline_collector',
        'class': 'OptimizedKlineCollector',
        'description': '针对AKShare不稳定的优化版，极低并发+长延迟',
        'data_type': 'stock_kline',
        'priority': 'MEDIUM'
    },

    'multi_source_kline_collector': {
        'name': '股票K线采集器（多数据源融合）',
        'module': 'data_job.collectors.multi_source_kline_collector',
        'class': 'StockKlineCollectorV2',
        'description': 'pytdx→EFinance→AKShare→BaoStock，自动切换',
        'data_type': 'stock_kline',
        'priority': 'HIGH',
        'recommended': True
    },

    'sector_kline_collector': {
        'name': '板块K线采集器',
        'module': 'data_job.collectors.sector_kline_collector',
        'class': 'SectorKlineCollector',
        'description': '采集申万行业板块K线数据',
        'data_type': 'sector_kline',
        'priority': 'HIGH',
        'recommended': True
    },

    'etf_kline_collector': {
        'name': 'ETF K线采集器',
        'module': 'data_job.collectors.etf_kline_collector',
        'class': 'ETFKlineCollector',
        'description': '采集ETF K线数据',
        'data_type': 'etf_kline',
        'priority': 'HIGH',
        'recommended': True
    },

    'index_data_collector': {
        'name': '指数数据采集器',
        'module': 'data_job.collectors.index_data_collector',
        'class': 'IndexDataCollector',
        'description': '采集主要指数数据',
        'data_type': 'index_data',
        'priority': 'HIGH',
        'recommended': True
    },

    # 基础信息
    'stock_sector_list_collector': {
        'name': '股票-板块映射采集器',
        'module': 'data_job.collectors.stock_sector_list_collector',
        'class': 'StockSectorListCollector',
        'description': '采集股票所属行业板块信息',
        'data_type': 'stock_sector_mapping',
        'priority': 'HIGH',
        'recommended': True
    },

    'etf_info_collector': {
        'name': 'ETF信息采集器',
        'module': 'data_job.collectors.etf_info_collector',
        'class': 'ETFInfoCollector',
        'description': '采集ETF基础信息',
        'data_type': 'etf_info',
        'priority': 'MEDIUM'
    },

    # 财务数据
    'finance_summary_collector': {
        'name': '财务摘要采集器',
        'module': 'data_job.collectors.finance_summary_collector',
        'class': 'FinanceSummaryCollector',
        'description': '采集主要财务指标摘要',
        'data_type': 'finance_summary',
        'priority': 'HIGH',
        'recommended': True
    },

    'fund_holdings_collector': {
        'name': '基金持仓采集器',
        'module': 'data_job.collectors.fund_holdings_collector',
        'class': 'FundHoldingsCollector',
        'description': '采集基金持股数据',
        'data_type': 'fund_holdings',
        'priority': 'HIGH',
        'recommended': True
    },

    'northbound_holdings_collector': {
        'name': '北向资金持股采集器',
        'module': 'data_job.collectors.northbound_holdings_collector',
        'class': 'NorthboundHoldingsCollector',
        'description': '采集沪深股通北向资金持股数据',
        'data_type': 'northbound_holdings',
        'priority': 'MEDIUM'
    },

    'stock_valuation_collector': {
        'name': '估值指标采集器',
        'module': 'data_job.collectors.stock_valuation_collector',
        'class': 'StockValuationCollector',
        'description': '采集PE/PB等估值指标',
        'data_type': 'valuation',
        'priority': 'MEDIUM'
    },

    # 市场数据
    'limit_boards_collector': {
        'name': '涨跌停板采集器',
        'module': 'data_job.collectors.limit_boards_collector',
        'class': 'LimitBoardsCollector',
        'description': '采集每日涨跌停板数据',
        'data_type': 'limit_boards',
        'priority': 'HIGH',
        'recommended': True
    },

    'news_collector': {
        'name': '新闻资讯采集器',
        'module': 'data_job.collectors.news_collector',
        'class': 'NewsCollector',
        'description': '采集股票相关新闻资讯',
        'data_type': 'news',
        'priority': 'MEDIUM'
    },

    'macro_data_collector': {
        'name': '宏观数据采集器',
        'module': 'data_job.collectors.macro_data_collector',
        'class': 'MacroDataCollector',
        'description': '采集宏观经济数据',
        'data_type': 'macro',
        'priority': 'LOW'
    },

    # 工具类
    'clash_proxy_collector': {
        'name': 'Clash代理配置',
        'module': 'data_job.collectors.clash_proxy_collector',
        'class': 'ClashProxyCollector',
        'description': 'Clash代理配置管理（非数据采集）',
        'data_type': 'proxy_config',
        'priority': 'IGNORE'
    },

    # 新创建的
    'qfq_kline_collector': {
        'name': '前复权K线采集器',
        'module': 'data_job.collectors.qfq_kline_collector',
        'class': 'QfqKlineCollector',
        'description': '使用AKShare获取前复权K线数据',
        'data_type': 'stock_kline_qfq',
        'priority': 'EXPERIMENTAL'
    },
}


def test_collector_import(key: str) -> tuple:
    """测试采集器是否能成功导入"""
    config = COLLECTORS[key]

    if config['priority'] == 'IGNORE':
        return True, '工具类，不测试'

    try:
        module = __import__(config['module'], fromlist=[config['class']])
        collector_class = getattr(module, config['class'])
        return True, '导入成功'
    except ImportError as e:
        return False, f'导入失败: {str(e)[:50]}'
    except Exception as e:
        return False, f'错误: {str(e)[:50]}'


def check_database_data(table_name: str) -> dict:
    """检查数据库中是否有数据"""
    try:
        from app.core.database import get_engine
        import pandas as pd

        engine = get_engine("local")

        # 检查表是否存在
        check_table_query = """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name=?
        """

        table_exists = pd.read_sql(check_table_query, engine, params=[table_name])

        if table_exists.empty:
            return {'exists': False, 'rows': 0}

        # 检查数据量
        count_query = f"SELECT COUNT(*) as count FROM {table_name}"
        count_df = pd.read_sql(count_query, engine)
        row_count = count_df.iloc[0]['count']

        # 获取最新数据日期
        if 'trade_date' in table_name or 'kline' in table_name:
            date_query = f"""
            SELECT MAX(trade_date) as latest_date,
                   MIN(trade_date) as earliest_date
            FROM {table_name}
            """
            date_df = pd.read_sql(date_query, engine)
            latest = date_df.iloc[0]['latest_date']
            earliest = date_df.iloc[0]['earliest_date']
        else:
            latest = None
            earliest = None

        return {
            'exists': True,
            'rows': row_count,
            'latest_date': latest,
            'earliest_date': earliest
        }

    except Exception as e:
        return {
            'exists': False,
            'error': str(e)[:100]
        }


def main():
    print("=" * 100)
    print("EvoAlpha OS 数据采集器全景报告")
    print("=" * 100)
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 1. 测试采集器导入
    print("📦 第一部分：采集器可用性测试")
    print("-" * 100)

    import_results = {}
    for key in sorted(COLLECTORS.keys()):
        config = COLLECTORS[key]

        if config['priority'] == 'IGNORE':
            continue

        success, message = test_collector_import(key)
        import_results[key] = {'success': success, 'message': message}

        status = "✅" if success else "❌"
        priority_mark = {
            'HIGH': '🔴',
            'MEDIUM': '🟡',
            'LOW': '⚪',
            'EXPERIMENTAL': '🔬'
        }.get(config['priority'], '')

        recommended = " ⭐推荐" if config.get('recommended') else ""

        print(f"{status} {priority_mark} {config['name']:<30} {message:<40}{recommended}")

    # 2. 检查数据库数据
    print("\n" + "=" * 100)
    print("💾 第二部分：数据库数据状态")
    print("-" * 100)

    # 数据表映射
    TABLE_MAPPING = {
        'stock_kline': 'stock_kline',
        'sector_kline': 'sector_kline',
        'etf_kline': 'etf_kline',
        'index_data': 'index_data',
        'stock_sector_mapping': 'stock_sector',
        'etf_info': 'etf_info',
        'finance_summary': 'finance_summary',
        'fund_holdings': 'fund_holdings',
        'northbound_holdings': 'northbound_holdings',
        'valuation': 'stock_valuation',
        'limit_boards': 'limit_boards',
        'news': 'stock_news',
        'macro': 'macro_data'
    }

    checked_tables = set()
    for key, config in COLLECTORS.items():
        data_type = config['data_type']
        if data_type in TABLE_MAPPING and data_type not in checked_tables:
            table_name = TABLE_MAPPING[data_type]
            checked_tables.add(data_type)

            result = check_database_data(table_name)

            if result.get('exists'):
                rows = result['rows']
                latest = result.get('latest_date', 'N/A')
                earliest = result.get('earliest_date', 'N/A')

                print(f"✅ {table_name:<25} 行数: {rows:>8,}   "
                      f"日期范围: {earliest} ~ {latest}")
            else:
                error = result.get('error', '表不存在')
                print(f"❌ {table_name:<25} {error}")

    # 3. 汇总统计
    print("\n" + "=" * 100)
    print("📊 第三部分：汇总统计")
    print("-" * 100)

    total = len([k for k, c in COLLECTORS.items() if c['priority'] != 'IGNORE'])
    available = sum(1 for r in import_results.values() if r['success'])

    print(f"采集器总数: {total}")
    print(f"可用数量:   {available} ({available/total*100:.1f}%)")
    print(f"不可用数量: {total - available}")

    # 按优先级统计
    print("\n按优先级分类:")
    for priority in ['HIGH', 'MEDIUM', 'LOW', 'EXPERIMENTAL']:
        collectors = [k for k, c in COLLECTORS.items()
                     if c['priority'] == priority]
        available_count = sum(1 for k in collectors
                            if import_results.get(k, {}).get('success'))

        if collectors:
            print(f"  {priority:>12}: {available_count}/{len(collectors)} 可用")

    # 推荐使用的采集器
    print("\n⭐ 推荐使用的数据采集器:")
    recommended = [k for k, c in COLLECTORS.items() if c.get('recommended')]
    for key in recommended:
        config = COLLECTORS[key]
        status = "✅" if import_results.get(key, {}).get('success') else "❌"
        print(f"  {status} {config['name']}")

    print("\n" + "=" * 100)
    print("报告结束")
    print("=" * 100)


if __name__ == "__main__":
    main()
