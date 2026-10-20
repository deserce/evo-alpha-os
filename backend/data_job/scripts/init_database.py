"""
数据库初始化脚本
创建所有必要的数据表
"""
import sys
from sqlalchemy import text

# 路径适配
sys.path.insert(0, '.')

from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

from app.core.database import get_engine

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


def init_database():
    """初始化数据库表结构"""
    logger.info("🚀 开始初始化数据库...")

    engine = get_engine()

    # 表定义
    tables = {
        # 基础数据表
        'stock_info': """
            CREATE TABLE IF NOT EXISTS stock_info (
                symbol VARCHAR(20) PRIMARY KEY,
                name VARCHAR(100)
            );
        """,

        'stock_sector_map': """
            CREATE TABLE IF NOT EXISTS stock_sector_map (
                symbol VARCHAR(20),
                name VARCHAR(100),
                sector_name VARCHAR(100),
                sector_type VARCHAR(50),
                PRIMARY KEY (sector_name, symbol)
            );
        """,

        'etf_info': """
            CREATE TABLE IF NOT EXISTS etf_info (
                symbol VARCHAR(20) PRIMARY KEY,
                name VARCHAR(100),
                fund_type VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """,

        # K线数据表
        'stock_daily_prices': """
            CREATE TABLE IF NOT EXISTS stock_daily_prices (
                symbol VARCHAR(20),
                trade_date DATE,
                open FLOAT,
                close FLOAT,
                high FLOAT,
                low FLOAT,
                volume FLOAT,
                amount FLOAT,
                pct_chg FLOAT,
                turnover_rate FLOAT,
                PRIMARY KEY (symbol, trade_date)
            );
        """,

        'sector_daily_prices': """
            CREATE TABLE IF NOT EXISTS sector_daily_prices (
                sector_name TEXT,
                trade_date DATE,
                open FLOAT,
                close FLOAT,
                high FLOAT,
                low FLOAT,
                volume FLOAT,
                amount FLOAT,
                pct_chg FLOAT,
                PRIMARY KEY (sector_name, trade_date)
            );
        """,

        'etf_daily_prices': """
            CREATE TABLE IF NOT EXISTS etf_daily_prices (
                symbol VARCHAR(20),
                trade_date DATE,
                open FLOAT,
                high FLOAT,
                low FLOAT,
                close FLOAT,
                volume FLOAT,
                amount FLOAT,
                pct_chg FLOAT,
                PRIMARY KEY (symbol, trade_date)
            );
        """,

        # 估值数据表
        'stock_valuation_daily': """
            CREATE TABLE IF NOT EXISTS stock_valuation_daily (
                code VARCHAR(20),
                name VARCHAR(50),
                trade_date DATE,
                price FLOAT,
                pe_ttm FLOAT,
                pb FLOAT,
                total_mv FLOAT,
                circ_mv FLOAT,
                pct_chg FLOAT,
                turnover FLOAT,
                volume_ratio FLOAT,
                PRIMARY KEY (code, trade_date)
            );
        """,

        # 财务数据表
        'stock_finance_summary': """
            CREATE TABLE IF NOT EXISTS stock_finance_summary (
                code VARCHAR(20),
                name VARCHAR(50),
                report_date DATE,
                report_type VARCHAR(20),
                report_date_name VARCHAR(50),
                notice_date DATE,
                update_date DATE,
                source VARCHAR(50),
                fetched_at TIMESTAMP,
                eps FLOAT,
                net_profit_up FLOAT,
                revenue_up FLOAT,
                roe FLOAT,
                total_revenue FLOAT,
                net_profit FLOAT,
                deduct_net_profit FLOAT,
                deduct_net_profit_up FLOAT,
                revenue_qoq FLOAT,
                net_profit_qoq FLOAT,
                deduct_net_profit_qoq FLOAT,
                bvps FLOAT,
                operating_cashflow_per_share FLOAT,
                gross_margin FLOAT,
                roe_diluted FLOAT,
                debt_ratio FLOAT,
                current_ratio FLOAT,
                quick_ratio FLOAT,
                net_margin FLOAT,
                total_revenue_single FLOAT,
                net_profit_single FLOAT,
                deduct_net_profit_single FLOAT,
                eps_single FLOAT,
                roe_single FLOAT,
                operating_cashflow_per_share_single FLOAT,
                gross_margin_single FLOAT,
                net_margin_single FLOAT,
                total_revenue_single_yoy FLOAT,
                net_profit_single_yoy FLOAT,
                deduct_net_profit_single_yoy FLOAT,
                total_revenue_single_qoq FLOAT,
                net_profit_single_qoq FLOAT,
                deduct_net_profit_single_qoq FLOAT,
                PRIMARY KEY (code, report_date)
            );
        """,

        # 北向资金持股表
        'stock_northbound_holdings': """
            CREATE TABLE IF NOT EXISTS stock_northbound_holdings (
                symbol VARCHAR(20),
                name VARCHAR(100),
                hold_date DATE,
                close_price FLOAT,
                pct_chg FLOAT,
                hold_amount FLOAT,
                hold_value FLOAT,
                hold_ratio FLOAT,
                change_amount FLOAT,
                change_value FLOAT,
                change_market_value FLOAT,
                PRIMARY KEY (symbol, hold_date)
            );
        """,

        'finance_fund_holdings': """
            CREATE TABLE IF NOT EXISTS finance_fund_holdings (
                symbol VARCHAR(20),
                report_date DATE,
                fund_count INTEGER,
                hold_count FLOAT,
                hold_value FLOAT,
                hold_change VARCHAR(20),
                change_value FLOAT,
                change_ratio FLOAT,
                PRIMARY KEY (symbol, report_date)
            );
        """,

        # 舆情数据表
        'news_articles': """
            CREATE TABLE IF NOT EXISTS news_articles (
                article_id VARCHAR(50) PRIMARY KEY,
                title VARCHAR(200),
                content TEXT,
                source VARCHAR(50),
                publish_time TIMESTAMP,
                url VARCHAR(500),
                sentiment_type VARCHAR(10),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """,

        'news_stock_relation': """
            CREATE TABLE IF NOT EXISTS news_stock_relation (
                article_id VARCHAR(50),
                symbol VARCHAR(20),
                relevance_score FLOAT,
                sentiment_type VARCHAR(10),
                PRIMARY KEY (article_id, symbol)
            );
        """,

        # 连板数据表
        'limit_board_trading': """
            CREATE TABLE IF NOT EXISTS limit_board_trading (
                trade_date DATE,
                symbol VARCHAR(20),
                name VARCHAR(100),
                pct_chg FLOAT,
                latest_price FLOAT,
                amount FLOAT,
                circ_mv FLOAT,
                total_mv FLOAT,
                turnover_rate FLOAT,
                seal_amount FLOAT,
                first_limit_time VARCHAR(10),
                last_limit_time VARCHAR(10),
                break_count INT,
                limit_stats VARCHAR(50),
                boards INT,
                industry VARCHAR(100),
                PRIMARY KEY (trade_date, symbol)
            );
        """,

        'consecutive_boards_stats': """
            CREATE TABLE IF NOT EXISTS consecutive_boards_stats (
                trade_date DATE,
                boards INT,
                stock_count INT,
                PRIMARY KEY (trade_date, boards)
            );
        """,

        # 宏观数据表
        'macro_indicators': """
            CREATE TABLE IF NOT EXISTS macro_indicators (
                indicator_name VARCHAR(50),
                indicator_code VARCHAR(20),
                period VARCHAR(20),
                value FLOAT,
                forecast_value FLOAT,
                previous_value FLOAT,
                unit VARCHAR(20),
                publish_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (indicator_code, period)
            );
        """,
    }

    # 索引定义
    indexes = [
        # K线数据索引
        "CREATE INDEX IF NOT EXISTS idx_kline_symbol ON stock_daily_prices (symbol);",
        "CREATE INDEX IF NOT EXISTS idx_kline_date ON stock_daily_prices (trade_date);",
        "CREATE INDEX IF NOT EXISTS idx_sector_date ON sector_daily_prices (trade_date);",
        "CREATE INDEX IF NOT EXISTS idx_etf_kline_symbol ON etf_daily_prices (symbol);",
        "CREATE INDEX IF NOT EXISTS idx_etf_kline_date ON etf_daily_prices (trade_date);",

        # 估值数据索引
        "CREATE INDEX IF NOT EXISTS idx_val_code ON stock_valuation_daily (code);",
        "CREATE INDEX IF NOT EXISTS idx_val_date ON stock_valuation_daily (trade_date);",

        # 财务数据索引
        "CREATE INDEX IF NOT EXISTS idx_finance_code ON stock_finance_summary (code);",
        "CREATE INDEX IF NOT EXISTS idx_finance_date ON stock_finance_summary (report_date);",

        # 北向资金持股索引
        "CREATE INDEX IF NOT EXISTS idx_north_holdings_date ON stock_northbound_holdings (hold_date);",
        "CREATE INDEX IF NOT EXISTS idx_north_holdings_symbol ON stock_northbound_holdings (symbol);",
        "CREATE INDEX IF NOT EXISTS idx_fund_date ON finance_fund_holdings (report_date);",

        # 舆情数据索引
        "CREATE INDEX IF NOT EXISTS idx_news_time ON news_articles (publish_time);",
        "CREATE INDEX IF NOT EXISTS idx_news_symbol ON news_stock_relation (symbol);",

        # 连板数据索引
        "CREATE INDEX IF NOT EXISTS idx_boards_date ON limit_board_trading (trade_date);",
        "CREATE INDEX IF NOT EXISTS idx_boards_symbol ON limit_board_trading (symbol);",
        "CREATE INDEX IF NOT EXISTS idx_stats_date ON consecutive_boards_stats (trade_date);",

        # 宏观数据索引
        "CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_indicators (publish_date);",
        "CREATE INDEX IF NOT EXISTS idx_macro_name ON macro_indicators (indicator_name);",

        # 板块映射索引
        "CREATE INDEX IF NOT EXISTS idx_map_symbol ON stock_sector_map (symbol);",
    ]

    try:
        with engine.begin() as conn:
            # 创建表
            logger.info("📊 创建数据表...")
            for table_name, create_sql in tables.items():
                try:
                    conn.execute(text(create_sql))
                    logger.info(f"  ✅ {table_name}")
                except Exception as e:
                    logger.warning(f"  ⚠️  {table_name}: {e}")

            # 创建索引
            logger.info("\n📇 创建索引...")
            for index_sql in indexes:
                try:
                    conn.execute(text(index_sql))
                except Exception:
                    pass  # 索引可能已存在

        logger.info("\n✅ 数据库初始化完成！")
        logger.info(f"✅ 共创建 {len(tables)} 个表")
        logger.info(f"✅ 共创建 {len(indexes)} 个索引")

        return True

    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        return False


if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)
