"""
EvoAlpha OS - 数据库初始化脚本（完整版）
创建所有必需的数据表
"""

import sys
import os
import logging

logger = logging.getLogger(__name__)

# 添加项目根目录到 Python 路径
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.database import get_active_engines
from sqlalchemy import text


def init_database():
    """初始化数据库表"""
    logger.info("🚀 开始初始化数据库...")

    # 获取所有活跃引擎
    engines = get_active_engines()

    for mode, engine in engines:
        logger.info(f"📊 正在初始化 {mode} 数据库...")

        try:
            with engine.begin() as conn:
                # ========== 1. 基础数据表 ==========
                logger.info("  [1/8] 创建基础数据表...")

                # 股票基本信息
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS stock_info (
                        symbol VARCHAR(20) PRIMARY KEY,
                        name VARCHAR(100),
                        industry VARCHAR(100),
                        list_date DATE,
                        market VARCHAR(10)
                    );
                """))

                # 个股日线
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS stock_daily_prices (
                        symbol VARCHAR(20),
                        trade_date DATE,
                        open FLOAT,
                        high FLOAT,
                        low FLOAT,
                        close FLOAT,
                        volume FLOAT,
                        amount FLOAT,
                        pct_chg FLOAT,
                        turnover_rate FLOAT,
                        PRIMARY KEY (symbol, trade_date)
                    );
                """))

                # 板块日线
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS sector_daily_prices (
                        sector_name VARCHAR(50),
                        trade_date DATE,
                        open FLOAT,
                        high FLOAT,
                        low FLOAT,
                        close FLOAT,
                        volume FLOAT,
                        amount FLOAT,
                        pct_chg FLOAT,
                        PRIMARY KEY (sector_name, trade_date)
                    );
                """))

                # 股票-板块映射
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS stock_sector_map (
                        symbol VARCHAR(20),
                        sector_name VARCHAR(50),
                        weight FLOAT,
                        PRIMARY KEY (symbol, sector_name)
                    );
                """))

                # ========== 2. 量化因子表 ==========
                logger.info("  [2/8] 创建量化因子表...")

                # 个股 RPS
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS quant_feature_rps (
                        symbol VARCHAR(20),
                        trade_date DATE,
                        rps_5 FLOAT,
                        rps_10 FLOAT,
                        rps_20 FLOAT,
                        rps_50 FLOAT,
                        rps_120 FLOAT,
                        rps_250 FLOAT,
                        ma_20 FLOAT,
                        ma_50 FLOAT,
                        ma_250 FLOAT,
                        PRIMARY KEY (symbol, trade_date)
                    );
                """))

                # 板块 RPS
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS quant_feature_sector_rps (
                        sector_name VARCHAR(50),
                        trade_date DATE,
                        rps_20 FLOAT,
                        rps_50 FLOAT,
                        rps_250 FLOAT,
                        PRIMARY KEY (sector_name, trade_date)
                    );
                """))

                # 股票池
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS quant_stock_pool (
                        pool_name VARCHAR(50),
                        symbol VARCHAR(20),
                        reason TEXT,
                        add_date DATE,
                        PRIMARY KEY (pool_name, symbol)
                    );
                """))

                # 策略结果
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS quant_strategy_results (
                        strategy_name VARCHAR(50),
                        trade_date DATE,
                        symbol VARCHAR(20),
                        signal_type VARCHAR(10),
                        meta_info TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (strategy_name, trade_date, symbol)
                    );
                """))

                # ========== 3. ETF 相关表 ==========
                logger.info("  [3/8] 创建 ETF 相关表...")

                # ETF 基础信息
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS etf_info (
                        symbol VARCHAR(20) PRIMARY KEY,
                        name VARCHAR(100),
                        fund_type VARCHAR(50),
                        underlying_index VARCHAR(100),
                        launch_date DATE,
                        expense_ratio FLOAT,
                        fund_company VARCHAR(100),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """))

                # ETF 行情
                conn.execute(text("""
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
                """))

                # ETF RPS
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS etf_feature_rps (
                        symbol VARCHAR(20),
                        trade_date DATE,
                        rps_20 FLOAT,
                        rps_50 FLOAT,
                        rps_250 FLOAT,
                        PRIMARY KEY (symbol, trade_date)
                    );
                """))

                # 全天候配置记录
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS all_weather_allocations (
                        allocation_date DATE PRIMARY KEY,
                        tech_ratio FLOAT,
                        dividend_ratio FLOAT,
                        nasdaq_ratio FLOAT,
                        gold_ratio FLOAT,
                        soybean_ratio FLOAT,
                        total_value FLOAT,
                        rebalance_reason TEXT
                    );
                """))

                # ========== 4. 新闻舆情表 ==========
                logger.info("  [4/8] 创建新闻舆情表...")

                # 新闻文章
                conn.execute(text("""
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
                """))

                # 新闻-股票关联
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS news_stock_relation (
                        article_id VARCHAR(50),
                        symbol VARCHAR(20),
                        relevance_score FLOAT,
                        sentiment_type VARCHAR(10),
                        PRIMARY KEY (article_id, symbol)
                    );
                """))

                # ========== 5. 连板数据表 ==========
                logger.info("  [5/8] 创建连板数据表...")

                # 涨停板交易
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS limit_board_trading (
                        trade_date DATE,
                        symbol VARCHAR(20),
                        name VARCHAR(100),
                        limit_time TIME,
                        limit_price FLOAT,
                        turnover_rate FLOAT,
                        amount FLOAT,
                        is_new_high BOOLEAN,
                        boards INT,
                        PRIMARY KEY (trade_date, symbol)
                    );
                """))

                # 连板统计
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS consecutive_boards_stats (
                        trade_date DATE,
                        boards INT,
                        stock_count INT,
                        PRIMARY KEY (trade_date, boards)
                    );
                """))

                # ========== 6. 宏观数据表 ==========
                logger.info("  [6/8] 创建宏观数据表...")

                # 宏观指标
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS macro_indicators (
                        indicator_name VARCHAR(50),
                        indicator_code VARCHAR(20),
                        period VARCHAR(20),
                        value FLOAT,
                        unit VARCHAR(20),
                        publish_date DATE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (indicator_code, period)
                    );
                """))

                # ========== 7. Alpha 机会表 ==========
                logger.info("  [7/8] 创建 Alpha 机会表...")

                # Alpha 机会列表
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS alpha_opportunities (
                        opportunity_id VARCHAR(50) PRIMARY KEY,
                        trade_date DATE,
                        symbol VARCHAR(20),
                        opportunity_type VARCHAR(50),
                        confidence_score FLOAT,
                        reason TEXT,
                        status VARCHAR(20),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """))

                # 机会跟踪
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS opportunity_tracking (
                        opportunity_id VARCHAR(50),
                        update_date DATE,
                        current_price FLOAT,
                        return_pct FLOAT,
                        status VARCHAR(20),
                        PRIMARY KEY (opportunity_id, update_date)
                    );
                """))

                # ========== 8. AI 分析表 ==========
                logger.info("  [8/8] 创建 AI 分析表...")

                # AI 分析缓存
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS ai_analysis_cache (
                        analysis_type VARCHAR(50),
                        target_id VARCHAR(50),
                        analysis_result TEXT,
                        model_version VARCHAR(20),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expire_at TIMESTAMP,
                        PRIMARY KEY (analysis_type, target_id, created_at)
                    );
                """))

                # ========== 创建索引 ==========
                logger.info("  创建索引...")

                # 个股日线索引
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_kline_symbol ON stock_daily_prices (symbol);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_kline_date ON stock_daily_prices (trade_date);"))

                # 板块日线索引
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sector_symbol ON sector_daily_prices (sector_name);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sector_date ON sector_daily_prices (trade_date);"))

                # RPS 索引
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_rps_symbol ON quant_feature_rps (symbol);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_rps_date ON quant_feature_rps (trade_date);"))

                # 策略结果索引
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_strategy_date ON quant_strategy_results (trade_date);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_strategy_symbol ON quant_strategy_results (symbol);"))

                # ETF 行情索引
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_etf_kline_symbol ON etf_daily_prices (symbol);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_etf_kline_date ON etf_daily_prices (trade_date);"))

                # 新闻索引
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_news_time ON news_articles (publish_time);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_news_symbol ON news_stock_relation (symbol);"))

                # 连板索引
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_boards_date ON limit_board_trading (trade_date);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_boards_symbol ON limit_board_trading (symbol);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stats_date ON consecutive_boards_stats (trade_date);"))

            logger.info(f"✅ {mode} 数据库初始化完成")

        except Exception as e:
            logger.error(f"❌ {mode} 数据库初始化失败: {e}")
            raise

    logger.info("🎉 所有数据库初始化完成！")


def drop_database():
    """删除所有表（谨慎使用！）"""
    logger.warning("⚠️  即将删除所有数据库表...")
    response = input("确认删除？(yes/no): ")

    if response.lower() != "yes":
        logger.info("已取消")
        return

    engines = get_active_engines()

    for mode, engine in engines:
        logger.info(f"🗑️  正在删除 {mode} 数据库表...")

        try:
            with engine.begin() as conn:
                # 删除所有表
                tables = [
                    "ai_analysis_cache",
                    "opportunity_tracking",
                    "alpha_opportunities",
                    "consecutive_boards_stats",
                    "limit_board_trading",
                    "macro_indicators",
                    "news_stock_relation",
                    "news_articles",
                    "all_weather_allocations",
                    "etf_feature_rps",
                    "etf_daily_prices",
                    "etf_info",
                    "quant_strategy_results",
                    "quant_stock_pool",
                    "quant_feature_sector_rps",
                    "quant_feature_rps",
                    "stock_sector_map",
                    "sector_daily_prices",
                    "stock_daily_prices",
                    "stock_info",
                ]

                for table in tables:
                    conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE;"))

            logger.info(f"✅ {mode} 数据库表已删除")

        except Exception as e:
            logger.error(f"❌ {mode} 数据库删除失败: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="数据库管理工具")
    parser.add_argument("--drop", action="store_true", help="删除所有表")
    args = parser.parse_args()

    if args.drop:
        drop_database()
    else:
        init_database()
