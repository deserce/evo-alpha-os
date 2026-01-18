# backend/data_job/fix_hot_concepts.py

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import time
import datetime

# ================= 配置区域 =================
DB_IP = "192.168.10.233"
DB_PORT = "5433"
DB_USER = "postgres"
DB_PASS = "123456"
DB_NAME = "evoquant"
DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_IP}:{DB_PORT}/{DB_NAME}"
# ===========================================

engine = create_engine(DB_URL)

def retry_missing_ths_data():
    print("🚑 启动 [同花顺] 数据查漏补缺...")
    
    # 1. 找出谁缺数据
    with engine.connect() as conn:
        # 查全集
        df_all = pd.read_sql("SELECT DISTINCT sector_name, sector_type FROM stock_sector_map", conn)
        # 查已有
        existing_list = pd.read_sql("SELECT DISTINCT sector_name FROM sector_daily_prices", conn)['sector_name'].tolist()
    
    # 筛选出缺失的部分
    df_missing = df_all[~df_all['sector_name'].isin(existing_list)]
    
    if df_missing.empty:
        print("🎉 完美！所有板块数据齐全，无需修复。")
        return

    print(f"📉 发现 {len(df_missing)} 个板块缺失数据，准备重试...")
    
    start_date = "20230101"
    end_date = datetime.datetime.now().strftime("%Y%m%d")
    
    success = 0
    
    for i, row in df_missing.iterrows():
        name = row['sector_name']
        s_type = row['sector_type']
        
        print(f"正在重试: {name} ...", end="\r")
        
        try:
            df = pd.DataFrame()
            if s_type == 'Industry':
                df = ak.stock_board_industry_index_ths(symbol=name, start_date=start_date, end_date=end_date)
            else:
                df = ak.stock_board_concept_index_ths(symbol=name, start_date=start_date, end_date=end_date)
            
            if df is None or df.empty:
                continue
                
            # 清洗
            cols_map = {
                '日期': 'trade_date', '开盘价': 'open', '收盘价': 'close',
                '最高价': 'high', '最低价': 'low', '成交量': 'volume'
            }
            df = df.rename(columns=cols_map)
            df['sector_name'] = name
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            
            valid_cols = ['sector_name', 'trade_date', 'open', 'close', 'high', 'low', 'volume']
            # 补缺列
            for col in valid_cols:
                if col not in df.columns: df[col] = 0
                
            save_df = df[valid_cols]
            
            # 入库
            save_df.to_sql('temp_fix_ths', engine, if_exists='replace', index=False)
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO sector_daily_prices SELECT * FROM temp_fix_ths
                    ON CONFLICT (sector_name, trade_date) DO NOTHING
                """))
            
            success += 1
            print(f"✅ 补回: {name}               ")
            time.sleep(2) # 重试的时候慢一点

        except Exception:
            # print(f"❌ {name} 依然失败")
            pass

    print(f"\n✨ 修复完成，成功补回 {success} 个板块。")

if __name__ == "__main__":
    retry_missing_ths_data()