# backend/data_job/init_capital_data.py

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, date, timedelta
import time
import random

# ================= 配置区域 =================
DB_IP = "192.168.10.233"
DB_PORT = "5433"
DB_USER = "postgres"
DB_PASS = "123456"
DB_NAME = "evoquant"
DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_IP}:{DB_PORT}/{DB_NAME}"

HISTORY_DAYS_NORTH = 365 * 2  # 北向回溯 2 年
HISTORY_QUARTERS_FUND = 12    # 基金回溯 12 个季度 (3年)
# ===========================================

engine = create_engine(DB_URL)

def retry_fetch(func, retries=3, delay=2, **kwargs):
    """通用重试装饰器"""
    for i in range(retries):
        try:
            return func(**kwargs)
        except Exception as e:
            time.sleep(delay + random.random())
            if i == retries - 1:
                # 某些日期确实没数据（比如周末），不打印报错骚扰，只返回空
                if "keyword argument" in str(e): # 如果是参数错误，必须打印
                    print(f"      ❌ 代码写错了: {e}")
                pass 
    return pd.DataFrame()

def save_to_db_chunked(df, table_name, chunk_size=5000):
    if df.empty: return
    try:
        df.to_sql(table_name, engine, if_exists='append', index=False, method='multi', chunksize=chunk_size)
    except Exception as e:
        print(f"      ❌ DB写入失败: {e}")

# ==========================================
# 1. 北向资金 (修复版：使用巨潮接口)
# ==========================================
def fetch_northbound_history():
    print(f"\n💰 [任务 1/2] 开始回溯北向资金 (过去 {HISTORY_DAYS_NORTH} 天)...")
    
    # 1. 初始化表结构 (如果不存在)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_northbound (
                code TEXT,
                trade_date DATE,
                hold_count FLOAT, -- 持股数量
                hold_value FLOAT, -- 持股市值
                PRIMARY KEY (code, trade_date)
            )
        """))

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=HISTORY_DAYS_NORTH)
    
    # 2. 检查已存在的日期
    try:
        with engine.connect() as conn:
            existing_dates = pd.read_sql(text(f"SELECT DISTINCT trade_date FROM finance_northbound WHERE trade_date >= '{start_date}'"), conn)
        existing_set = set(existing_dates['trade_date'].astype(str).tolist()) if not existing_dates.empty else set()
    except:
        existing_set = set()
    
    current_date = end_date
    while current_date >= start_date:
        date_str = current_date.strftime("%Y%m%d")
        iso_date_str = current_date.strftime("%Y-%m-%d")
        
        if iso_date_str in existing_set:
            print(f"   ⏭️ {iso_date_str} 已存在，跳过。")
            current_date -= timedelta(days=1)
            continue
            
        print(f"   📥 正在抓取: {iso_date_str} ...", end="\r")
        
        try:
            # 🔥 核心修改：切换到 ak.stock_hsgt_hold_stock_cninfo
            # 这个接口支持 historical date
            df = retry_fetch(ak.stock_hsgt_hold_stock_cninfo, date=date_str)
            
            if not df.empty:
                # 巨潮接口返回列名通常是：['代码', '简称', '持股数量', '持股占比', '收盘价', '当日涨幅', '持股市值', '日期']
                # 需要做映射
                col_map = {
                    '代码': 'code', 
                    '持股数量': 'hold_count', 
                    '持股市值': 'hold_value',
                    '日期': 'trade_date'
                }
                df = df.rename(columns=col_map)
                
                # 数据清洗
                if 'code' in df.columns and 'hold_count' in df.columns:
                    # 如果没有 trade_date 列，手动补上
                    if 'trade_date' not in df.columns:
                        df['trade_date'] = current_date
                    
                    # 只要需要的列
                    df = df[['code', 'trade_date', 'hold_count', 'hold_value']].copy()
                    
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                    df['code'] = df['code'].astype(str).str.zfill(6)
                    
                    # 巨潮的数据里持股市值单位可能是元，有些接口是万元，这里不做特殊处理，保持原样
                    # 通常巨潮返回的是“元”
                    
                    save_to_db_chunked(df, 'finance_northbound')
                    print(f"   ✅ {iso_date_str} 入库成功 ({len(df)} 条)            ")
                else:
                    print(f"   ⚠️ {iso_date_str} 格式不对，列名: {df.columns}")
            else:
                # 周末没有数据是正常的
                # print(f"   💤 {iso_date_str} 无数据 (可能是周末)")
                pass
                
        except Exception as e:
            if "expected string" not in str(e): # 忽略解析错误的噪音
                print(f"   ❌ {iso_date_str} 异常: {e}")
            
        current_date -= timedelta(days=1)
        time.sleep(0.8) # 巨潮稍微慢一点，多睡会

    print("✅ 北向资金回溯完成。")

# ==========================================
# 2. 基金持仓 (逻辑保持不变)
# ==========================================
def fetch_fund_history():
    print(f"\n📊 [任务 2/2] 开始回溯基金持仓 (过去 {HISTORY_QUARTERS_FUND} 个季度)...")
    
    # 1. 初始化表结构
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_fund_holdings (
                code TEXT,
                report_date DATE,
                fund_ratio FLOAT, -- 基金持仓比例
                PRIMARY KEY (code, report_date)
            )
        """))

    quarters = []
    curr_year = date.today().year
    for y in range(curr_year, curr_year - 4, -1):
        for md in ["1231", "0930", "0630", "0331"]:
            q_date_str = f"{y}{md}"
            q_date_obj = datetime.strptime(q_date_str, "%Y%m%d").date()
            if q_date_obj <= date.today():
                quarters.append(q_date_str)
                
    target_quarters = quarters[:HISTORY_QUARTERS_FUND]
    
    print("   ⏳ 正在拉取最新流通股本基准...")
    try:
        # 这里用东财的实时行情拿流通股本
        df_spot = retry_fetch(ak.stock_zh_a_spot_em)
        spot_map = {} 
        if not df_spot.empty:
            rename_dict = {'代码':'code', '流通股本':'float_share', '最新价':'price', '流通市值':'mcap'}
            df_spot = df_spot.rename(columns=rename_dict)
            
            # 兼容处理：有些时候返回的是流通市值，没有流通股本
            if 'float_share' not in df_spot.columns and 'mcap' in df_spot.columns:
                 df_spot['float_share'] = pd.to_numeric(df_spot['mcap'], errors='coerce') / pd.to_numeric(df_spot['price'], errors='coerce')
            
            df_spot['code'] = df_spot['code'].astype(str).str.zfill(6)
            df_spot['float_share'] = pd.to_numeric(df_spot['float_share'], errors='coerce')
            spot_map = df_spot.set_index('code')['float_share'].to_dict()
    except Exception as e:
        print(f"   ❌ 无法获取行情数据: {e}")
        return

    for q_date in target_quarters:
        print(f"   📥 正在处理季度: {q_date} ...")
        
        check_date = f"{q_date[:4]}-{q_date[4:6]}-{q_date[6:]}"
        try:
            with engine.connect() as conn:
                cnt = conn.execute(text(f"SELECT COUNT(*) FROM finance_fund_holdings WHERE report_date = '{check_date}'")).scalar()
            if cnt > 100:
                print(f"      ⏭️ {q_date} 已存在，跳过。")
                continue
        except: pass

        # 抓取基金持仓
        df_fund = retry_fetch(ak.stock_report_fund_hold, date=q_date)
        
        if df_fund.empty:
            continue
            
        col_map = {}
        for c in df_fund.columns:
            if c in ['代码', '股票代码', 'code']: col_map[c] = 'code'
            if c in ['持股总数', '基金持股总数', '持股数']: col_map[c] = 'hold_count'
            
        df_fund.rename(columns=col_map, inplace=True)
        
        if 'code' not in df_fund.columns or 'hold_count' not in df_fund.columns:
            continue
            
        df_fund['code'] = df_fund['code'].astype(str).str.zfill(6)
        df_fund['hold_count'] = pd.to_numeric(df_fund['hold_count'], errors='coerce')
        
        result_rows = []
        for _, row in df_fund.iterrows():
            code = row['code']
            hold = row['hold_count']
            if pd.isna(hold): continue
            
            float_share = spot_map.get(code)
            ratio = 0
            if float_share and float_share > 0:
                ratio = (hold / float_share) * 100
                
            if ratio > 0:
                result_rows.append({
                    'code': code,
                    'report_date': check_date,
                    'fund_ratio': round(ratio, 4)
                })
                
        if result_rows:
            df_save = pd.DataFrame(result_rows)
            with engine.begin() as conn:
                conn.execute(text(f"DELETE FROM finance_fund_holdings WHERE report_date = '{check_date}'"))
            save_to_db_chunked(df_save, 'finance_fund_holdings')
            print(f"      ✅ {q_date} 入库成功 ({len(df_save)} 条)")
            
        time.sleep(2)

    print("✅ 基金持仓回溯完成。")

if __name__ == "__main__":
    print(f"🚀 全量资金数据回溯启动...")
    try:
        fetch_northbound_history()
        fetch_fund_history()
    except KeyboardInterrupt:
        print("\n🛑 用户手动停止")
    print("\n🎉 任务结束。")