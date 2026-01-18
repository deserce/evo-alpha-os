import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import time

# --- 配置 ---
DB_IP = "192.168.10.233"
DB_PORT = "5433"
DB_USER = "postgres"
DB_PASS = "123456"
DB_NAME = "evoquant"
DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_IP}:{DB_PORT}/{DB_NAME}"

# 定义我们要计算的周期
PERIODS = [3, 5, 10, 20, 50, 120, 250]

def create_db_engine():
    return create_engine(DB_URL)

def calculate_and_save():
    engine = create_db_engine()
    print("⏳ 正在从数据库读取全量日线数据 (这可能需要几秒钟)...")
    
    # 1. 读取数据 (只读需要的列：代码、日期、收盘价)
    # 这一步可能会消耗几百兆内存，对于 Mac 来说很轻松
    query = "SELECT code, trade_date, close FROM daily_prices ORDER BY code, trade_date"
    df = pd.read_sql(query, engine)
    
    print(f"✅ 读取完成，共 {len(df)} 行。开始量化计算...")
    start_time = time.time()

    # 2. 计算 N日涨幅 (纵向计算)
    # GroupBy Code 后，对 Close 列做 pct_change
    df = df.sort_values(['code', 'trade_date']) # 确保按时间排序
    grouped = df.groupby('code')['close']
    
    for p in PERIODS:
        col_name = f'chg_{p}'
        # pct_change(p) 计算的是 (现在 - N天前)/N天前
        df[col_name] = grouped.pct_change(p)
        print(f"   - 已计算 {p} 日涨幅")

    # 3. 计算 RPS (横向计算)
    # 按照日期分组，对当天的所有股票的 涨幅 进行排名
    print("⏳ 正在计算 RPS 排名 (Cross-sectional Rank)...")
    
    # 我们只对有涨幅数据的行计算 RPS (排除掉上市时间不足 N 天的空值)
    for p in PERIODS:
        chg_col = f'chg_{p}'
        rps_col = f'rps_{p}'
        
        # 核心公式: 排名 / 总数 * 100
        # method='min' 表示如果有并列，取最小排名
        # pct=True 会直接生成 0.0-1.0 的百分比，我们乘 100
        df[rps_col] = df.groupby('trade_date')[chg_col].rank(pct=True, method='min') * 100
        
        # 把 RPS 保留2位小数
        df[rps_col] = df[rps_col].round(2)

    cost_time = time.time() - start_time
    print(f"✅ 计算完成！耗时 {cost_time:.2f} 秒。")

    # 4. 数据清洗与入库
    print("⏳ 准备入库 (这可能需要一点时间)...")
    
    # 去除空值 (刚上市前几天无法计算涨幅和 RPS)
    # 只要最大的周期 250 也是空，说明这行数据对于长线 RPS 没用，但也保留短线的
    # 简单策略：保留所有行，数据库里空值存为 NULL
    
    # 整理列名，只保留我们需要的列
    output_cols = ['code', 'trade_date'] + [f'chg_{p}' for p in PERIODS] + [f'rps_{p}' for p in PERIODS]
    result_df = df[output_cols].copy()
    
    # 替换 inf/-inf 为 NaN (防止除以0错误)
    result_df = result_df.replace([np.inf, -np.inf], np.nan)
    
    # 写入数据库
    # 为了速度，我们使用 'append'。
    # 注意：如果表里已经有数据，这里会重复报错。
    # 建议：如果是全量重算，先清空表。
    
    try:
        with engine.connect() as conn:
            print("   正在清空旧指标数据 (TRUNCATE)...")
            conn.execute(text("TRUNCATE TABLE stock_indicators"))
            conn.commit()
            
        print("   正在批量写入数据库 (chunksize=5000)...")
        result_df.to_sql(
            'stock_indicators', 
            engine, 
            if_exists='append', 
            index=False, 
            method='multi', # PostgreSQL 加速写入关键
            chunksize=5000  # 分批写入防止内存溢出
        )
        print("🎉 全部指标计算并入库完成！")
        
    except Exception as e:
        print(f"❌ 入库失败: {e}")

if __name__ == "__main__":
    calculate_and_save()