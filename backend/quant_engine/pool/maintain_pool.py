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
# 你的筛选逻辑配置
FUND_THRESHOLD = 5         # 基金持仓 > 5%
NORTH_THRESHOLD = 10000    # 北向持仓 > 1亿元 (假设你的库里单位是万元)
                           # 如果库里单位是元，这里需要改为 100000000

class StockPoolMaintainer:
    def __init__(self):
        self.engine = get_engine()
        self.target_table = "quant_stock_pool"  # 标准化后的表名

    def refresh_pool(self):
        print("🏊‍♂️ 开始清洗 [核心股票池] (基本面筛选)...")
        print(f"   💡 筛选标准: 基金持股>{FUND_THRESHOLD}% 或 北向持仓>{NORTH_THRESHOLD}万元")

        # 1. 构造 SQL (基于你的原始SQL进行字段适配)
        # 变化点：
        # - stock_list -> stock_info
        # - code -> symbol (为了统一标准)
        # - 输出增加 pool_name 字段，方便区分不同策略的池子
        
        sql_filter = text(f"""
        WITH LatestFund AS (
            SELECT DISTINCT ON (code) code, fund_ratio 
            FROM finance_fund_holdings 
            ORDER BY code, report_date DESC
        ),
        LatestNorth AS (
            SELECT DISTINCT ON (code) code, hold_value 
            FROM finance_northbound 
            ORDER BY code, trade_date DESC
        ),
        BasicInfo AS (
            SELECT symbol, name FROM stock_info  -- 适配: 表名变了
        )
        SELECT 
            b.symbol, 
            b.name,
            'core_pool' as pool_name,  -- 新增: 池子名称
            CASE 
                WHEN f.fund_ratio > {FUND_THRESHOLD} AND n.hold_value > {NORTH_THRESHOLD} THEN '机构+北向双重仓'
                WHEN f.fund_ratio > {FUND_THRESHOLD} THEN '基金重仓(>{FUND_THRESHOLD}%)' 
                WHEN n.hold_value > {NORTH_THRESHOLD} THEN '北向重仓(>1亿)'
            END as reason
        FROM BasicInfo b
        LEFT JOIN LatestFund f ON b.symbol::text = f.code::text
        LEFT JOIN LatestNorth n ON b.symbol::text = n.code::text
        WHERE 
            f.fund_ratio > {FUND_THRESHOLD} 
            OR n.hold_value > {NORTH_THRESHOLD}
        """)
        
        try:
            print("   ⏳ 正在执行数据库比对...")
            df = pd.read_sql(sql_filter, self.engine)
            
            if df.empty:
                print("⚠️ 筛选结果为空！请检查 finance_fund_holdings 或 finance_northbound 是否有数据。")
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