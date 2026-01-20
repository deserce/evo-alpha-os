# backend/quant_engine/pool/maintain_pool.py

import sys
import os
import traceback
import pandas as pd
from datetime import datetime, date
from sqlalchemy import text

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

from app.core.database import get_engine

# ================= 配置 =================
# 筛选逻辑配置
# 只要符合以下任一条件即可入选核心股票池
FUND_RATIO_THRESHOLD = 5.0   # 基金持仓: 基金持股数/总股本 > 5%（最近3季度任意满足）
NORTH_MIN_VALUE = 100000000  # 北向持仓: 持股市值 > 1亿元 (单位: 元)

class StockPoolMaintainer:
    def __init__(self):
        self.engine = get_engine()
        self.target_table = "quant_stock_pool"  # 标准化后的表名

    def refresh_pool(self):
        print("🏊‍♂️ 开始清洗 [核心股票池] (基本面筛选)...")
        print(f"   💡 筛选标准（符合任一即可）:")
        print(f"      1. 基金持股比例 ≥ {FUND_RATIO_THRESHOLD}%（最近3季度任意满足）")
        print(f"      2. 北向资金持仓 ≥ 1亿元")

        # 使用 SQLite 兼容的语法
        # 基金持股比例 = 基金持股数 / 总股本 × 100%
        # 总股本 = 总市值 / 收盘价
        # 北向持仓：直接使用hold_value（单位：元）

        # 获取最近日期作为基准
        max_fund_date = pd.read_sql(
            "SELECT MAX(report_date) as max_date FROM finance_fund_holdings",
            self.engine
        ).iloc[0]['max_date']

        # 计算9个月前的日期（最近3个季度）
        max_date_obj = pd.to_datetime(max_fund_date)
        cutoff_date = (max_date_obj - pd.DateOffset(months=9)).strftime('%Y-%m-%d')

        print(f"   📅 基金数据范围: {cutoff_date} 至 {max_fund_date}（最近3季度）")

        sql_filter = text(f"""
        WITH LatestValuation AS (
            SELECT code, total_mv, price
            FROM stock_valuation_daily v1
            WHERE trade_date = (SELECT MAX(trade_date) FROM stock_valuation_daily)
        ),
        FundLast3Quarters AS (
            SELECT DISTINCT
                symbol,
                report_date,
                hold_count
            FROM finance_fund_holdings
            WHERE report_date >= '{cutoff_date}'
        ),
        FundRatio AS (
            SELECT
                f.symbol,
                MAX(CAST(f.hold_count AS REAL) / (v.total_mv / v.price) * 100.0) as max_fund_ratio
            FROM FundLast3Quarters f
            JOIN LatestValuation v ON f.symbol = v.code
            GROUP BY f.symbol
        ),
        LatestNorth AS (
            SELECT symbol, hold_value
            FROM stock_northbound_holdings n1
            WHERE hold_date = (SELECT MAX(hold_date) FROM stock_northbound_holdings)
        ),
        BasicInfo AS (
            SELECT symbol, name FROM stock_info
        )
        SELECT
            b.symbol,
            b.name,
            'core_pool' as pool_name,
            CASE
                WHEN COALESCE(fr.max_fund_ratio, 0) >= {FUND_RATIO_THRESHOLD}
                     AND COALESCE(n.hold_value, 0) >= {NORTH_MIN_VALUE} THEN '基金+北向双重符合'
                WHEN COALESCE(fr.max_fund_ratio, 0) >= {FUND_RATIO_THRESHOLD} THEN '基金重仓'
                WHEN COALESCE(n.hold_value, 0) >= {NORTH_MIN_VALUE} THEN '北向重仓'
            END as reason
        FROM BasicInfo b
        LEFT JOIN FundRatio fr ON b.symbol = fr.symbol
        LEFT JOIN LatestNorth n ON b.symbol = n.symbol
        WHERE
            COALESCE(fr.max_fund_ratio, 0) >= {FUND_RATIO_THRESHOLD}
            OR COALESCE(n.hold_value, 0) >= {NORTH_MIN_VALUE}
        """)
        
        try:
            print("   ⏳ 正在执行数据库比对...")
            df = pd.read_sql(sql_filter, self.engine)
            
            if df.empty:
                print("⚠️ 筛选结果为空！请检查以下表是否有数据:")
                print("   - finance_fund_holdings (基金持仓)")
                print("   - stock_northbound_holdings (北向持仓)")
                print("   - stock_info (股票基本信息)")
                return

            print(f"✅ 成功筛选出 {len(df)} 只优质股票！")
            
            # 2. 数据标准化
            df['add_date'] = date.today()
            df['is_active'] = True
            
            # 3. 入库 (适配新的 quant_stock_pool 表结构)
            # 你的逻辑是 Drop Table，但在生产环境中，我们通常是清空特定 pool_name 的数据
            # 这样不会误删其他策略（比如人工精选）的池子
            
            with self.engine.begin() as conn:
                # 先初始化表（如果不存在）
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS {self.target_table} (
                        pool_name VARCHAR(50),
                        symbol VARCHAR(20),
                        name VARCHAR(50),
                        add_date DATE,
                        reason TEXT,
                        is_active BOOLEAN DEFAULT TRUE,
                        PRIMARY KEY (pool_name, symbol, add_date)
                    )
                """))
                
                # 清除旧的 'core_pool' 数据
                conn.execute(text(f"DELETE FROM {self.target_table} WHERE pool_name = 'core_pool'"))
                
                # 写入新数据
                df.to_sql(self.target_table, conn, if_exists='append', index=False)
                
            print("🎉 核心股票池已重建完成。")
            if not df.empty:
                row = df.iloc[0]
                print(f"   示例: {row['symbol']} {row['name']} -> {row['reason']}")

        except Exception:
            print("❌ 发生错误:")
            traceback.print_exc()

    def run(self):
        """统一调用入口"""
        self.refresh_pool()

if __name__ == "__main__":
    StockPoolMaintainer().run()