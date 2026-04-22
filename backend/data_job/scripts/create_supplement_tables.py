"""
创建补充数据表
执行 SQL 创建 index_daily, market_daily_stats, watchlist
并扩充 stock_info 表字段
"""
import sys
from pathlib import Path
from sqlalchemy import text

# 添加项目路径
current_dir = Path(__file__).parent
backend_dir = current_dir.parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.database import get_local_engine
import logging
logger = logging.getLogger(__name__)

def create_tables():
    """创建新数据表"""
    engine = get_local_engine()

    try:
        with engine.begin() as conn:
            # 1. 创建 index_daily 表
            logger.info("创建 index_daily 表...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS index_daily (
                    index_code VARCHAR(10) NOT NULL,
                    index_name VARCHAR(20) NOT NULL,
                    trade_date DATE NOT NULL,
                    open DECIMAL(10,2),
                    high DECIMAL(10,2),
                    low DECIMAL(10,2),
                    close DECIMAL(10,2),
                    volume BIGINT,
                    amount BIGINT,
                    pct_chg DECIMAL(10,2),
                    PRIMARY KEY (index_code, trade_date)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_index_date ON index_daily(trade_date)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_index_code ON index_daily(index_code)"))
            logger.info("✅ index_daily 表创建成功")

            # 2. 创建 market_daily_stats 表
            logger.info("创建 market_daily_stats 表...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS market_daily_stats (
                    trade_date DATE PRIMARY KEY,
                    up_count INTEGER NOT NULL DEFAULT 0,
                    down_count INTEGER NOT NULL DEFAULT 0,
                    flat_count INTEGER NOT NULL DEFAULT 0,
                    limit_up_count INTEGER NOT NULL DEFAULT 0,
                    limit_down_count INTEGER NOT NULL DEFAULT 0,
                    avg_chg DECIMAL(10,2),
                    total_volume BIGINT NOT NULL DEFAULT 0,
                    total_amount BIGINT NOT NULL DEFAULT 0,
                    north_money BIGINT DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            logger.info("✅ market_daily_stats 表创建成功")

            # 3. 创建 watchlist 表
            logger.info("创建 watchlist 表...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol VARCHAR(10) NOT NULL UNIQUE,
                    name VARCHAR(50),
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON watchlist(symbol)"))
            logger.info("✅ watchlist 表创建成功")

            # 4. 扩充 stock_info 表字段
            logger.info("扩充 stock_info 表字段...")

            # 添加 industry 字段
            try:
                conn.execute(text("ALTER TABLE stock_info ADD COLUMN industry VARCHAR(50)"))
                logger.info("  ✅ 添加 industry 字段")
            except Exception as e:
                if "duplicate column" in str(e).lower():
                    logger.info("  ⏭️  industry 字段已存在，跳过")
                else:
                    raise

            # 添加 market_cap 字段
            try:
                conn.execute(text("ALTER TABLE stock_info ADD COLUMN market_cap DECIMAL(20,2)"))
                logger.info("  ✅ 添加 market_cap 字段")
            except Exception as e:
                if "duplicate column" in str(e).lower():
                    logger.info("  ⏭️  market_cap 字段已存在，跳过")
                else:
                    raise

            # 添加 pe 字段
            try:
                conn.execute(text("ALTER TABLE stock_info ADD COLUMN pe DECIMAL(10,2)"))
                logger.info("  ✅ 添加 pe 字段")
            except Exception as e:
                if "duplicate column" in str(e).lower():
                    logger.info("  ⏭️  pe 字段已存在，跳过")
                else:
                    raise

            # 添加 pb 字段
            try:
                conn.execute(text("ALTER TABLE stock_info ADD COLUMN pb DECIMAL(10,2)"))
                logger.info("  ✅ 添加 pb 字段")
            except Exception as e:
                if "duplicate column" in str(e).lower():
                    logger.info("  ⏭️  pb 字段已存在，跳过")
                else:
                    raise

            # 添加 high_52w 字段
            try:
                conn.execute(text("ALTER TABLE stock_info ADD COLUMN high_52w DECIMAL(10,2)"))
                logger.info("  ✅ 添加 high_52w 字段")
            except Exception as e:
                if "duplicate column" in str(e).lower():
                    logger.info("  ⏭️  high_52w 字段已存在，跳过")
                else:
                    raise

            # 添加 low_52w 字段
            try:
                conn.execute(text("ALTER TABLE stock_info ADD COLUMN low_52w DECIMAL(10,2)"))
                logger.info("  ✅ 添加 low_52w 字段")
            except Exception as e:
                if "duplicate column" in str(e).lower():
                    logger.info("  ⏭️  low_52w 字段已存在，跳过")
                else:
                    raise

            logger.info("✅ stock_info 表字段扩充完成")

    except Exception as e:
        logger.error(f"❌ 创建表失败: {e}")
        raise

    logger.info("🎉 所有数据表创建完成！")

    # 验证表是否创建成功
    logger.info("验证表创建情况...")
    verify_tables(engine)


def verify_tables(engine):
    """验证表是否创建成功"""
    with engine.begin() as conn:
        # 检查新建的表
        tables_to_check = [
            'index_daily',
            'market_daily_stats',
            'watchlist'
        ]

        for table in tables_to_check:
            cursor = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = cursor.fetchone()[0]
            logger.info(f"  📊 {table}: {count} 条记录")

        # 检查 stock_info 表结构
        cursor = conn.execute(text("PRAGMA table_info(stock_info)"))
        columns = cursor.fetchall()
        logger.info(f"  📋 stock_info 表字段: {[col[1] for col in columns]}")


if __name__ == "__main__":
    logger.info("🚀 开始创建补充数据表...")
    create_tables()
    logger.info("✅ 完成！")
