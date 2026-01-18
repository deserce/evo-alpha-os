import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
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

def get_all_stocks():
    print("📋 获取全市场股票列表...")
    try:
        df = ak.stock_zh_a_spot_em()
        return df['代码'].tolist()
    except:
        return []

def worker_northbound_safe(code):
    try:
        # 抓取
        df = ak.stock_hsgt_individual_detail_em(symbol=code)
        
        # 判空防御
        if df is None or df.empty:
            return None # 标记为无数据

        # 清洗
        rename_map = {'日期': 'trade_date', '持股数量': 'hold_count', '持股市值': 'hold_value'}
        df.rename(columns=rename_map, inplace=True)
        
        if 'trade_date' not in df.columns or 'hold_value' not in df.columns:
            return None

        # 整理
        df = df[['trade_date', 'hold_count', 'hold_value']].copy()
        df['code'] = code
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        
        # 入库
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM finance_northbound WHERE code = '{code}'"))
            df.to_sql('finance_northbound', conn, if_exists='append', index=False)
            
        return len(df)
        
    except Exception:
        return None

def run_safe_mode():
    print("🚀 启动【龟速隐身】初始化模式...")
    codes = get_all_stocks()
    # 过滤出 A 股
    valid_codes = [c for c in codes if c.startswith(('60', '00', '30', '68'))]
    
    # 检查断点续传
    with engine.connect() as conn:
        existing = pd.read_sql("SELECT DISTINCT code FROM finance_northbound", conn)
        done_set = set(existing['code'].tolist())
    
    tasks = [c for c in valid_codes if c not in done_set]
    total = len(tasks)
    print(f"📋 总任务: {len(valid_codes)}, 已完成: {len(done_set)}, 剩余: {total}")

    success_count = 0
    
    for i, code in enumerate(tasks):
        # 执行任务
        res = worker_northbound_safe(code)
        
        # 进度条逻辑
        status = "✅" if res else "⚪"
        msg = f"{res}条" if res else "无数据/非标的"
        if res: success_count += 1
            
        print(f"[{i+1}/{total}] {code} {status} {msg}", end="\r")
        
        # 🔥 核心防封逻辑：随机休眠 🔥
        # 每次请求后，随机休息 0.5 ~ 1.5 秒
        # 这会让爬虫看起来像真人在点击
        sleep_time = random.uniform(0.5, 1.5)
        time.sleep(sleep_time)
        
        # 每抓 100 个，额外休息 5 秒
        if (i + 1) % 100 == 0:
            print(f"\n☕️ 也就是跑了 100 个，休息 5 秒喝口水...")
            time.sleep(5)

    print(f"\n🎉 任务结束！本次成功入库: {success_count} 只股票")

if __name__ == "__main__":
    # 只有在你换了 IP 之后再运行这个！！！
    run_safe_mode()