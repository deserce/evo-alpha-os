# backend/data_job/init_sector_data.py

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

def init_sector_data_em():
    print("🚀 启动 [东方财富] 板块数据初始化...")
    
    # 1. 重建数据库表
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS stock_sector_map"))
        conn.execute(text("""
            CREATE TABLE stock_sector_map (
                stock_code TEXT,
                stock_name TEXT,
                sector_name TEXT,
                sector_type TEXT, 
                source TEXT DEFAULT 'EM',
                PRIMARY KEY (stock_code, sector_name)
            )
        """))

    total_saved = 0

    # -------------------------------------------
    # 1. 抓取行业 (Industry)
    # -------------------------------------------
    print("\n🏭 [1/2] 正在下载行业板块...")
    try:
        df_ind = ak.stock_board_industry_name_em()
        names = df_ind['板块名称'].tolist()
        print(f"   发现 {len(names)} 个行业，开始逐个获取成分股...")
        
        for i, name in enumerate(names):
            # 打印进度，不换行
            print(f"   [{i+1}/{len(names)}] 处理: {name} ...", end="\r")
            try:
                # 核心：获取该行业的股票列表
                cons = ak.stock_board_industry_cons_em(symbol=name)
                
                data_list = []
                for _, row in cons.iterrows():
                    data_list.append({
                        'stock_code': str(row['代码']).zfill(6),
                        'stock_name': row['名称'],
                        'sector_name': name,
                        'sector_type': 'Industry',
                        'source': 'EM'
                    })
                
                if data_list:
                    pd.DataFrame(data_list).to_sql('stock_sector_map', engine, if_exists='append', index=False)
                    total_saved += 1
                
                time.sleep(0.05) # 东财很快，稍微歇一下即可
            except:
                continue
    except Exception as e:
        print(f"❌ 行业列表获取失败: {e}")

    # -------------------------------------------
    # 2. 抓取概念 (Concept)
    # -------------------------------------------
    print("\n\n🌈 [2/2] 正在下载概念板块...")
    try:
        df_con = ak.stock_board_concept_name_em()
        names = df_con['板块名称'].tolist()
        print(f"   发现 {len(names)} 个概念，开始逐个获取成分股...")
        
        for i, name in enumerate(names):
            print(f"   [{i+1}/{len(names)}] 处理: {name} ...", end="\r")
            try:
                cons = ak.stock_board_concept_cons_em(symbol=name)
                
                data_list = []
                for _, row in cons.iterrows():
                    data_list.append({
                        'stock_code': str(row['代码']).zfill(6),
                        'stock_name': row['名称'],
                        'sector_name': name,
                        'sector_type': 'Concept',
                        'source': 'EM'
                    })
                
                if data_list:
                    pd.DataFrame(data_list).to_sql('stock_sector_map', engine, if_exists='append', index=False)
                    total_saved += 1
                
                time.sleep(0.05)
            except:
                continue
    except Exception as e:
        print(f"❌ 概念列表获取失败: {e}")

    print(f"\n\n🎉 全部完成！共成功存入 {total_saved} 个板块的数据。")
    
    # 最后验证一下
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM stock_sector_map")).scalar()
        print(f"📊 最终数据库验证：表中共有 {count} 条记录。")

if __name__ == "__main__":
    init_sector_data_em()