"""
EvoAlpha OS - 数据库初始化脚本
创建所有必需的数据表
"""

import sys
import os
from loguru import logger

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
                # 1. 基础数据表
                logger.info("  创建基础数据表...")

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

                # 2. 量化因子表
                logger.info("  创建量化因子表...")

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
                        meta_info JSONB,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (strategy_name, trade_date, symbol)
                    );
                """))

                # 3. 创建索引
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

            logger.success(f"✅ {mode} 数据库初始化完成")

        except Exception as e:
            logger.error(f"❌ {mode} 数据库初始化失败: {e}")
            raise

    logger.success("🎉 所有数据库初始化完成！")


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

            logger.success(f"✅ {mode} 数据库表已删除")

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
