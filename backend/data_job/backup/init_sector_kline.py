# backend/data_job/init_sector_kline.py

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import time

# ================= 配置区域 =================
DB_IP = "192.168.10.233"
DB_PORT = "5433"
DB_USER = "postgres"
DB_PASS = "123456"
DB_NAME = "evoquant"
DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_IP}:{DB_PORT}/{DB_NAME}"
# ===========================================

engine = create_engine(DB_URL)

def init_sector_kline_em():
    print("🚀 启动 [东方财富] 板块 K 线下载...")
    
    # 1. 确保表存在
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sector_daily_prices (
                sector_name TEXT, trade_date DATE, open FLOAT, close FLOAT, 
                high FLOAT, low FLOAT, volume FLOAT, PRIMARY KEY (sector_name, trade_date)
            )
        """))

    # 2. 从数据库读取板块名单
    print("📖 正在读取板块名单...")
    try:
        # 直接读取，不需要复杂的 source 判断，全是 EM
        df_sectors = pd.read_sql("SELECT DISTINCT sector_name, sector_type FROM stock_sector_map", engine)
    except Exception as e:
        print(f"❌ 读取失败: {e}")
        return

    if df_sectors.empty:
        print("❌ 数据库是空的！请必须先运行 init_sector_data.py！")
        return

    total = len(df_sectors)
    print(f"🚀 准备下载 {total} 个板块的历史数据...")
    
    success_count = 0
    
    for i, row in df_sectors.iterrows():
        name = row['sector_name']
        s_type = row['sector_type']
        
        print(f"[{i+1}/{total}] 下载 K 线: {name} ...", end="\r")
        
        df = pd.DataFrame()
        try:
            # 简单明了：是行业就调行业接口，是概念就调概念接口
            if s_type == 'Industry':
                df = ak.stock_board_industry_hist_em(symbol=name, adjust="")
            else:
                df = ak.stock_board_concept_hist_em(symbol=name, adjust="")

            if df is None or df.empty:
                continue

            # 统一列名 (东财返回中文)
            cols_map = {
                '日期': 'trade_date',
                '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low',
                '成交量': 'volume'
            }
            df = df.rename(columns=cols_map)
            df['sector_name'] = name
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            
            # 只保留核心列
            save_df = df[['sector_name', 'trade_date', 'open', 'close', 'high', 'low', 'volume']]

            # 入库
            save_df.to_sql('temp_sector_k', engine, if_exists='replace', index=False)
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO sector_daily_prices SELECT * FROM temp_sector_k
                    ON CONFLICT (sector_name, trade_date) DO UPDATE SET 
                    close=EXCLUDED.close, volume=EXCLUDED.volume
                """))
            
            success_count += 1
            time.sleep(0.05) # 极速模式

        except Exception:
            # 某些特殊板块可能没 K 线，跳过即可
            continue

    print(f"\n🎉 任务完成！成功下载 {success_count} / {total} 个板块数据。")

if __name__ == "__main__":
    init_sector_kline_em()