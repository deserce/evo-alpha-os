-- 补充数据表结构定义
-- 创建日期: 2026-01-20
-- 用途: 支持市场指挥部和个股作战室API

-- ============================================
-- 1. 指数数据表 (新建)
-- ============================================
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
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_index_date ON index_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_index_code ON index_daily(index_code);

-- ============================================
-- 2. 市场统计表 (新建，用于缓存)
-- ============================================
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
    north_money BIGINT DEFAULT 0,  -- 北向资金净流入
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- 3. 自选股表 (新建)
-- ============================================
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(10) NOT NULL UNIQUE,
    name VARCHAR(50),
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON watchlist(symbol);

-- ============================================
-- 4. 扩充 stock_info 表 (已有表，添加字段)
-- ============================================

-- 检查并添加新列（如果不存在）
-- SQLite 不支持 IF NOT EXISTS ALTER TABLE，需要用 Python 脚本处理
-- 这些列将在 stock_info_expander.py 中添加：
-- industry VARCHAR(50)
-- market_cap DECIMAL(20,2)
-- pe DECIMAL(10,2)
-- pb DECIMAL(10,2)
-- high_52w DECIMAL(10,2)
-- low_52w DECIMAL(10,2)
