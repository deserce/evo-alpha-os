# backend/data_job/init_valuation_emergency.py

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import time

# ... (数据库配置保持不变，请复制之前的配置) ...
# ================= 配置区域 =================
DB_IP = "192.168.10.233"
DB_PORT = "5433"
DB_USER = "postgres"
DB_PASS = "123456"
DB_NAME = "evoquant"
DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_IP}:{DB_PORT}/{DB_NAME}"
engine = create_engine(DB_URL)
# ===========================================

def fix_valuation():
    print("🚑 启动估值数据紧急修复...")
    
    try:
        # 尝试使用 ak.stock_zh_a_spot_em() 的简化版，有时候不容易超时
        df = ak.stock_zh_a_spot_em()
        
        # 映射
        rename = {
            "代码": "code", "名称": "name", "最新价": "price",
            "总市值": "total_mv", "流通市值": "circ_mv", 
            "市盈率-动态": "pe_ttm", "市净率": "pb"
        }
        df = df.rename(columns=rename)
        
        # 只要核心列
        df = df[["code", "name", "price", "total_mv", "circ_mv", "pe_ttm", "pb"]]
        
        # 清洗
        df['code'] = df['code'].astype(str).str.zfill(6)
        for col in ["price", "total_mv", "circ_mv", "pe_ttm", "pb"]:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        # 入库
        print(f"💾 正在写入 {len(df)} 条数据...")
        with engine.begin() as conn:
            df.to_sql('stock_valuation', conn, if_exists='replace', index=False)
            conn.execute(text("ALTER TABLE stock_valuation ADD PRIMARY KEY (code)"))
            
        print("✅ 估值表修复成功！")
        
    except Exception as e:
        print(f"❌ 依然失败: {e}")
        print("👉 建议：换个时间点（如中午或盘后）再试。")

if __name__ == "__main__":
    fix_valuation()