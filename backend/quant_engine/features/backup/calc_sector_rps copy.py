# backend/app/quant_engine/calc_sector_rps.py

import pandas as pd
import numpy as np
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

def calc_sector_rps_full():
    print("🚀 启动 [板块 RPS] 全量历史计算 (5/10/20/50/120/250)...")
    start_time = time.time()

    # 1. 读取所有板块的 K 线数据
    print("📥 正在读取 sector_daily_prices ...")
    query = "SELECT trade_date, sector_name, close FROM sector_daily_prices ORDER BY trade_date"
    try:
        df = pd.read_sql(query, engine)
    except Exception as e:
        print(f"❌ 读取失败: {e}")
        return
    
    if df.empty:
        print("❌ 数据库无数据，请先运行 K 线下载脚本。")
        return

    # 确保日期格式正确
    df['trade_date'] = pd.to_datetime(df['trade_date'])

    # 2. 数据透视 (Pivot) -> 转换为宽表
    # 行索引 = 日期, 列索引 = 板块名, 值 = 收盘价
    print("🔄 正在进行矩阵变换 (Pivot)...")
    df_pivot = df.pivot(index='trade_date', columns='sector_name', values='close')
    
    # 向前填充（防止停牌导致数据中断）
    df_pivot = df_pivot.fillna(method='ffill')

    # 3. 定义计算周期
    # 5日: 超短爆发
    # 10/20日: 短期/波段趋势
    # 50日: 中期生命线
    # 120/250日: 长期牛熊线
    periods = [5, 10, 20, 50, 120, 250]
    
    rps_results = []

    print(f"🧮 开始向量化计算 RPS...")
    
    for n in periods:
        # A. 计算 N 日涨幅 (Rate of Change)
        # pct_change(n) = (Price_Today - Price_N_ago) / Price_N_ago
        df_roc = df_pivot.pct_change(periods=n)
        
        # B. 横截面排名 (Cross-sectional Rank)
        # axis=1 代表在每一天内部对所有板块进行排名
        # pct=True 输出 0~1，乘以 100 变成 0~100 分
        df_rank = df_roc.rank(axis=1, pct=True) * 100
        
        # C. 堆叠 (Stack) -> 变回长表结构
        series_stacked = df_rank.stack()
        series_stacked.name = f'rps_{n}'
        
        rps_results.append(series_stacked)

    # 4. 合并数据
    print("🔗 正在合并多周期数据...")
    df_final = pd.concat(rps_results, axis=1)
    
    # 重置索引，恢复 trade_date 和 sector_name 列
    df_final = df_final.reset_index()
    
    # 保留2位小数
    numeric_cols = [f'rps_{n}' for n in periods]
    df_final[numeric_cols] = df_final[numeric_cols].round(2)

    # 5. 入库
    print(f"💾 正在保存 {len(df_final)} 条指标数据到 sector_indicators ...")
    
    try:
        # 显式重建表结构，确保包含所有列
        with engine.begin() as conn:
            # 先删表 (为了确保 schema 更新，比如之前没有 rps_5)
            # 如果不想删数据，可以用 ALTER TABLE 添加列，但这里全量重算最快
            conn.execute(text("DROP TABLE IF EXISTS sector_indicators"))
            
            conn.execute(text("""
                CREATE TABLE sector_indicators (
                    trade_date DATE,
                    sector_name TEXT,
                    rps_5 FLOAT,
                    rps_10 FLOAT,
                    rps_20 FLOAT,
                    rps_50 FLOAT,
                    rps_120 FLOAT,
                    rps_250 FLOAT,
                    PRIMARY KEY (trade_date, sector_name)
                )
            """))
        
        # 批量写入
        df_final.to_sql('sector_indicators', engine, if_exists='append', index=False, method='multi', chunksize=5000)
        
        # 补索引
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sec_ind_date ON sector_indicators (trade_date)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sec_ind_name ON sector_indicators (sector_name)"))

        print(f"✅ 成功！耗时 {time.time() - start_time:.2f} 秒。")
        
        # 6. 打印最新一天的战况
        latest_date = df_final['trade_date'].max()
        print(f"\n🏆 [{latest_date.date()}] 市场最强主线 (RPS_20 > 95):")
        
        top_sectors = df_final[
            (df_final['trade_date'] == latest_date) & 
            (df_final['rps_20'] > 95)
        ].sort_values(by='rps_20', ascending=False)
        
        # 打印展示
        print("-" * 50)
        print(f"{'板块名称':<12} {'RPS_5':<8} {'RPS_20':<8} {'RPS_50':<8} {'RPS_250':<8}")
        print("-" * 50)
        for _, row in top_sectors.head(10).iterrows():
            print(f"{row['sector_name']:<12} {row['rps_5']:<8} {row['rps_20']:<8} {row['rps_50']:<8} {row['rps_250']:<8}")
        print("-" * 50)

    except Exception as e:
        print(f"❌ 入库失败: {e}")

if __name__ == "__main__":
    calc_sector_rps_full()