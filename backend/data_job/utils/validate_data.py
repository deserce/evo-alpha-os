"""
数据验证工具 - 检查采集数据的质量和完整性
"""
import sys
import logging
from datetime import datetime, timedelta

# 路径适配
sys.path.insert(0, '.')

from data_job.common import setup_backend_path, setup_logger
from app.core.database import get_engine

# 路径初始化
setup_backend_path()

# Logger配置
logger = setup_logger(__name__)


class DataValidator:
    """数据验证器"""

    def __init__(self):
        self.engine = get_engine()
        self.validation_results = {}

    def validate_table(self, table_name, expected_columns=None,
                      date_column=None, min_rows=0):
        """
        验证表数据

        Args:
            table_name: 表名
            expected_columns: 期望的列名列表
            date_column: 日期列名（用于检查最新数据）
            min_rows: 最小行数

        Returns:
            dict: 验证结果
        """
        result = {
            'table_name': table_name,
            'exists': False,
            'row_count': 0,
            'latest_date': None,
            'is_valid': False,
            'issues': []
        }

        try:
            with self.engine.connect() as conn:
                # 检查表是否存在
                check_query = f"""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='{table_name}'
                """
                table_exists = conn.execute(check_query).fetchone()

                if not table_exists:
                    result['issues'].append('表不存在')
                    return result

                result['exists'] = True

                # 获取行数
                count_query = f"SELECT COUNT(*) FROM {table_name}"
                row_count = conn.execute(count_query).scalar()
                result['row_count'] = row_count

                if row_count == 0:
                    result['issues'].append('表为空')
                    return result

                if row_count < min_rows:
                    result['issues'].append(f'行数({row_count})少于最小要求({min_rows})')

                # 检查最新数据
                if date_column:
                    date_query = f"SELECT MAX({date_column}) FROM {table_name}"
                    latest_date = conn.execute(date_query).scalar()
                    result['latest_date'] = latest_date

                    if latest_date:
                        days_old = (datetime.now() - latest_date).days
                        if days_old > 7:
                            result['issues'].append(f'数据过旧: 最新数据是{days_old}天前')

                # 检查列是否存在
                if expected_columns:
                    columns_query = f"PRAGMA table_info({table_name})"
                    columns_info = conn.execute(columns_query).fetchall()
                    actual_columns = {row[1] for row in columns_info}

                    missing_columns = set(expected_columns) - actual_columns
                    if missing_columns:
                        result['issues'].append(f'缺失列: {missing_columns}')

                # 判断是否有效
                result['is_valid'] = len(result['issues']) == 0

        except Exception as e:
            result['issues'].append(f'验证异常: {e}')

        return result

    def validate_all_tables(self):
        """验证所有数据表"""
        logger.info("🔍 开始数据验证...")

        tables_to_validate = [
            # 基础数据
            {'name': 'stock_info', 'columns': ['symbol', 'name'], 'min_rows': 4000},
            {'name': 'stock_sector_map', 'columns': ['symbol', 'sector_name', 'sector_type'], 'min_rows': 10000},
            {'name': 'etf_info', 'columns': ['symbol', 'name', 'fund_type'], 'min_rows': 50},

            # K线数据
            {'name': 'stock_daily_prices', 'columns': ['symbol', 'trade_date', 'close'],
             'date_column': 'trade_date', 'min_rows': 100000},
            {'name': 'sector_daily_prices', 'columns': ['sector_name', 'trade_date', 'close'],
             'date_column': 'trade_date', 'min_rows': 10000},
            {'name': 'etf_daily_prices', 'columns': ['symbol', 'trade_date', 'close'],
             'date_column': 'trade_date', 'min_rows': 10000},

            # 估值数据
            {'name': 'stock_valuation_daily', 'columns': ['code', 'trade_date', 'pe_ttm'],
             'date_column': 'trade_date', 'min_rows': 4000},

            # 财务数据
            {'name': 'stock_finance_summary', 'columns': ['code', 'report_date', 'eps'],
             'date_column': 'report_date', 'min_rows': 1000},

            # 资金流向
            {'name': 'stock_northbound_holdings', 'columns': ['symbol', 'hold_date', 'hold_amount'],
             'date_column': 'hold_date', 'min_rows': 1000},
            {'name': 'finance_fund_holdings', 'columns': ['symbol', 'report_date', 'fund_count'],
             'date_column': 'report_date', 'min_rows': 1000},

            # 舆情数据
            {'name': 'news_articles', 'columns': ['article_id', 'title', 'publish_time'],
             'date_column': 'publish_time', 'min_rows': 0},
            {'name': 'limit_board_trading', 'columns': ['trade_date', 'symbol'],
             'date_column': 'trade_date', 'min_rows': 0},

            # 宏观数据
            {'name': 'macro_indicators', 'columns': ['indicator_code', 'period', 'value'],
             'date_column': 'publish_date', 'min_rows': 10},
        ]

        all_valid = True
        for table_config in tables_to_validate:
            result = self.validate_table(
                table_name=table_config['name'],
                expected_columns=table_config['columns'],
                date_column=table_config.get('date_column'),
                min_rows=table_config['min_rows']
            )

            self.validation_results[table_config['name']] = result

            if result['is_valid']:
                logger.info(f"✅ {table_config['name']}: {result['row_count']} 行")
            else:
                logger.warning(f"⚠️  {table_config['name']}: {', '.join(result['issues'])}")
                all_valid = False

        return all_valid

    def generate_report(self):
        """生成验证报告"""
        logger.info("\n" + "=" * 80)
        logger.info("📊 数据验证报告")
        logger.info("=" * 80)

        total_tables = len(self.validation_results)
        valid_tables = sum(1 for r in self.validation_results.values() if r['is_valid'])
        invalid_tables = total_tables - valid_tables

        logger.info(f"总表数: {total_tables}")
        logger.info(f"✅ 有效: {valid_tables}")
        logger.info(f"⚠️  无效: {invalid_tables}")

        if invalid_tables > 0:
            logger.info("\n❌ 需要修复的表:")
            for table_name, result in self.validation_results.items():
                if not result['is_valid']:
                    logger.info(f"  - {table_name}: {', '.join(result['issues'])}")

        logger.info("=" * 80)

        return valid_tables == total_tables


def main():
    """主函数"""
    validator = DataValidator()
    is_valid = validator.validate_all_tables()
    validator.generate_report()

    return 0 if is_valid else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
