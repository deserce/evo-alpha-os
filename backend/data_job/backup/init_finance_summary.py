# backend/data_job/init_finance_summary.py

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text, inspect
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

def init_db_table():
    """初始化数据库表结构（只在表不存在时创建）"""
    inspector = inspect(engine)
    if not inspector.has_table("stock_finance_summary"):
        print("🛠️ 表不存在，正在创建 stock_finance_summary...")
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE stock_finance_summary (
                    code TEXT,
                    name TEXT,
                    report_date DATE,
                    eps FLOAT,               -- 每股收益
                    net_profit_up FLOAT,     -- 净利润同比增长
                    revenue_up FLOAT,        -- 营收同比增长
                    roe FLOAT,               -- 净资产收益率
                    net_margin FLOAT,        -- 销售净利率
                    PRIMARY KEY (code, report_date)
                )
            """))
    else:
        print("✅ 表 stock_finance_summary 已存在，准备断点续传...")

def check_date_exists(report_date):
    """检查某个季度的数据是否已经入库"""
    try:
        # 注意：这里日期需要转成标准格式 'YYYY-MM-DD' 进行查询
        formatted_date = pd.to_datetime(report_date).strftime('%Y-%m-%d')
        query = text(f"SELECT 1 FROM stock_finance_summary WHERE report_date = '{formatted_date}' LIMIT 1")
        with engine.connect() as conn:
            result = conn.execute(query).fetchone()
            return result is not None
    except Exception:
        return False

def fetch_master_finance():
    print("📈 启动 [2025 稳健版] 财务采集 (支持断点续传)...")
    
    # 1. 初始化表结构（不再删除旧表）
    init_db_table()

    # 2. 构造日期列表 (2020-2025)
    years = [2025, 2024, 2023, 2022, 2021, 2020]
    quarters = ["1231", "0930", "0630", "0331"] # 倒序抓取，优先看最近的
    date_tasks = [f"{y}{q}" for y in years for q in quarters if f"{y}{q}" <= "20251231"] # 注意当前时间限制

    # 3. 循环采集
    total = len(date_tasks)
    
    for i, target_date in enumerate(date_tasks):
        # --- [断点续传检测] ---
        if check_date_exists(target_date):
            print(f"[{i+1}/{total}] ⏩ {target_date} 数据库已有，跳过...")
            continue
        # --------------------

        # --- [失败重试机制] ---
        max_retries = 3
        success = False
        
        for attempt in range(max_retries):
            try:
                print(f"[{i+1}/{total}] ⏳ 正在抓取 {target_date} (第 {attempt+1} 次尝试)...", end="\r")
                
                # 获取东财业绩报表
                df = ak.stock_yjbb_em(date=target_date)
                
                if df is None or df.empty:
                    print(f"\n⚠️ {target_date} 无数据，跳过")
                    success = True # 标记为成功以免死循环，虽然是空的
                    break

                # 统一列名映射
                rename_map = {
                    '股票代码': 'code', '股票简称': 'name',
                    '每股收益': 'eps', '净利润-同比增长': 'net_profit_up',
                    '营业收入-同比增长': 'revenue_up', '净资产收益率': 'roe',
                    '销售净利率': 'net_margin'
                }
                # 防止部分季度缺字段报错，只rename存在的列
                df = df.rename(columns=rename_map)
                
                # 确保关键列都存在，不存在的补 0
                required_cols = ['code', 'name', 'eps', 'net_profit_up', 'revenue_up', 'roe', 'net_margin']
                for col in required_cols:
                    if col not in df.columns:
                        df[col] = 0
                
                # 提取数据并清洗
                df_save = df[required_cols].copy()
                df_save['report_date'] = pd.to_datetime(target_date).date()
                df_save['code'] = df_save['code'].astype(str).str.zfill(6)
                df_save = df_save.replace('-', 0)
                
                # 强制数字类型转换
                num_cols = ['eps', 'net_profit_up', 'revenue_up', 'roe', 'net_margin']
                for col in num_cols:
                    df_save[col] = pd.to_numeric(df_save[col], errors='coerce').fillna(0)

                # 写入数据库
                df_save.to_sql('stock_finance_summary', engine, if_exists='append', index=False)
                
                print(f"[{i+1}/{total}] ✅ {target_date} 入库成功 ({len(df_save)}条)          ")
                success = True
                
                # 成功后休息久一点，防封
                time.sleep(random.uniform(3, 5)) 
                break # 成功了就跳出重试循环

            except Exception as e:
                wait_time = 10 * (attempt + 1)
                print(f"\n❌ {target_date} 第 {attempt+1} 次失败: {str(e)[:50]}... 等待 {wait_time}秒")
                time.sleep(wait_time) # 失败了休息久一点

        if not success:
            print(f"\n💀 {target_date} 最终失败，程序继续执行下一个日期...")

    print("\n🎉 财务数据采集任务结束！")

if __name__ == "__main__":
    fetch_master_finance()