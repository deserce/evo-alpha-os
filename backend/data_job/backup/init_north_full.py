# backend/data_job/init_north_local.py

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import time
import random

# ================= 配置区域 =================
DB_IP = "192.168.10.233"
DB_PORT = "5433"
DB_USER = "postgres"
DB_PASS = "123456"
DB_NAME = "evoquant"
DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_IP}:{DB_PORT}/{DB_NAME}"
# ===========================================

engine = create_engine(DB_URL)

def init_north_filtered():
    print("🚀 启动 [北向资金] 本地回溯 (智能过滤版)...")
    
    # 1. 确保表结构
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_northbound (
                code TEXT,
                trade_date DATE,
                hold_count FLOAT,
                hold_value FLOAT,
                PRIMARY KEY (code, trade_date)
            )
        """))

    # 2. 读取名单
    print("📋 读取本地股票池...")
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text("SELECT DISTINCT code FROM stock_finance_summary"), conn)
    except:
        print("❌ 无法读取数据库")
        return

    # 3. 🔥 核心修正：只保留沪深 A 股 (00, 30, 60, 68 开头)
    # 过滤掉 83, 43, 87 (新三板) 和 900 (B股)
    original_count = len(df)
    
    # 使用正则筛选：以 00, 30, 60, 68 开头的代码
    df_filtered = df[df['code'].astype(str).str.match(r'^(00|30|60|68)')]
    
    target_codes = df_filtered['code'].tolist()
    filtered_count = len(target_codes)
    
    print(f"✂️ 过滤前: {original_count} 只 -> 过滤后: {filtered_count} 只 (剔除了新三板等)")
    print(f"✅ 锁定真正有北向资格的 {filtered_count} 只股票。")
    
    # 打乱顺序
    random.shuffle(target_codes)

    # 4. 回溯
    total = len(target_codes)
    success_count = 0
    
    for i, code in enumerate(target_codes):
        print(f"[{i+1}/{total}] 同步: {code} ...", end="\r")
        
        # 断点续传：已有数据则跳过
        try:
            with engine.connect() as conn:
                cnt = conn.execute(text(f"SELECT count(*) FROM finance_northbound WHERE code='{code}'")).scalar()
            if cnt > 50: 
                continue
        except: pass

        try:
            df_hist = ak.stock_hsgt_individual_em(stock=code)
            
            if df_hist is None or df_hist.empty:
                continue

            # 清洗
            col_map = {'日期': 'trade_date', '持股数量': 'hold_count', '持股市值': 'hold_value'}
            df_hist = df_hist.rename(columns=col_map)
            df_hist['code'] = code
            df_hist['trade_date'] = pd.to_datetime(df_hist['trade_date']).dt.date
            
            save_df = df_hist[['code', 'trade_date', 'hold_count', 'hold_value']]
            
            # 入库
            save_df.to_sql('finance_northbound', engine, if_exists='append', index=False, method='multi')
            success_count += 1
            
            # 这里的 sleep 可以保持 0.2，因为我们过滤了垃圾请求，效率已经很高了
            time.sleep(0.2) 

        except Exception:
            time.sleep(0.5)

    print(f"\n🎉 任务完成！共回溯 {success_count} 只沪深核心股票。")

if __name__ == "__main__":
    init_north_filtered()