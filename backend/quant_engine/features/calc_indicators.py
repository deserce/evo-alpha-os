# backend/quant_engine/features/calc_indicators.py

import sys
import os
import time
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from sqlalchemy import text

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

from app.core.database import get_engine

# ================= 配置 =================
PERIODS = [3, 5, 10, 20, 50, 120, 250]
TABLE_SOURCE = "stock_daily_prices"
TABLE_TARGET = "quant_feature_rps"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class IndicatorCalculator:
    def __init__(self):
        self.engine = get_engine()

    def load_data(self, start_date=None):
        """加载 K 线数据"""
        # 如果指定了 start_date，只加载那之后的数据（增量模式）
        # 否则加载全量
        condition = f"WHERE trade_date >= '{start_date}'" if start_date else ""
        
        query = f"""
            SELECT symbol, trade_date, close 
            FROM {TABLE_SOURCE} 
            {condition}
            ORDER BY trade_date
        """
        logger.info(f"📥 正在读取数据 (Start: {start_date if start_date else 'All'})...")
        df = pd.read_sql(query, self.engine)
        
        if df.empty: return df
        
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df

    def compute_features(self, df):
        """核心计算逻辑 (向量化)"""
        if df.empty: return pd.DataFrame()

        # 1. Pivot 宽表
        df_pivot = df.pivot(index='trade_date', columns='symbol', values='close')
        df_pivot = df_pivot.fillna(method='ffill') # 填充停牌
        
        # 2. 计算涨幅 & RPS
        feature_dfs = []
        
        for n in PERIODS:
            # 涨幅
            chg = df_pivot.pct_change(n)
            # RPS 排名 (0-100)
            rps = chg.rank(axis=1, pct=True, method='min') * 100
            
            # 堆叠
            chg_stack = chg.stack().reset_index()
            chg_stack.columns = ['trade_date', 'symbol', f'chg_{n}']
            chg_stack.set_index(['symbol', 'trade_date'], inplace=True)
            
            rps_stack = rps.stack().reset_index()
            rps_stack.columns = ['trade_date', 'symbol', f'rps_{n}']
            rps_stack.set_index(['symbol', 'trade_date'], inplace=True)
            
            feature_dfs.append(chg_stack)
            feature_dfs.append(rps_stack)
            
        # 3. 合并
        final_df = pd.concat(feature_dfs, axis=1).reset_index()
        
        # 4. 格式化
        float_cols = [c for c in final_df.columns if c not in ['symbol', 'trade_date']]
        for c in float_cols:
            if 'rps' in c:
                final_df[c] = final_df[c].round(2)
            else:
                final_df[c] = final_df[c].round(4)
                
        return final_df

    def save_to_db(self, df, mode='append'):
        """入库逻辑"""
        if df.empty: return
        
        logger.info(f"💾 正在写入数据库 ({len(df)} 行)...")
        try:
            # 如果是 append (增量)，需要防止主键冲突
            # Pandas 的 to_sql append 遇到主键冲突会报错
            # 所以增量模式下，我们要先删掉当天已有的数据 (幂等性)
            if mode == 'append':
                dates = df['trade_date'].unique()
                # 格式化日期列表
                date_strs = [pd.to_datetime(d).strftime('%Y-%m-%d') for d in dates]
                if date_strs:
                    date_list_sql = "'" + "','".join(date_strs) + "'"
                    with self.engine.begin() as conn:
                        conn.execute(text(f"DELETE FROM {TABLE_TARGET} WHERE trade_date IN ({date_list_sql})"))
            
            # 如果是 replace (全量)，外部需要在调用前 truncate，这里只管 append
            df.to_sql(TABLE_TARGET, self.engine, if_exists='append', index=False, method='multi', chunksize=5000)
            
        except Exception as e:
            logger.error(f"❌ 入库失败: {e}")
            # 如果是死锁或者网络问题，可能需要重试机制，这里暂略

    def run_init(self):
        """【全量模式】重算所有历史"""
        logger.info("🚀 [RPS] 启动全量重算...")
        
        # 1. 清空表
        with self.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {TABLE_TARGET}"))
            
        # 2. 加载全量
        df = self.load_data(start_date=None)
        
        # 3. 计算
        res = self.compute_features(df)
        
        # 4. 保存
        self.save_to_db(res, mode='replace_fast') # 实际上也是append，只是前面清空了
        logger.info("✅ 全量任务完成")

    def run_daily(self):
        """【增量模式】只算最新一天"""
        logger.info("🚀 [RPS] 启动增量更新...")
        
        # 1. 确定数据加载窗口
        # 我们需要计算 250日 RPS，所以至少需要往前推 250个交易日
        # 保险起见，往前推 400 个自然日
        cutoff_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        
        # 2. 加载“滑动窗口”数据
        df = self.load_data(start_date=cutoff_date)
        if df.empty: return

        # 3. 计算 (此时算出来的是过去400天的RPS)
        res_full = self.compute_features(df)
        
        # 4. 截取最新数据
        # 假设今天是 2025-01-01，我们只需要存 2025-01-01 的结果
        # 但考虑到可能补漏，我们取最近 3 天的数据入库
        target_date_threshold = (datetime.now() - timedelta(days=3))
        res_daily = res_full[res_full['trade_date'] > target_date_threshold].copy()
        
        if res_daily.empty:
            logger.info("⚠️ 无最新日期数据需要更新 (可能是假期)")
            return
            
        logger.info(f"📅 捕获更新日期: {res_daily['trade_date'].unique()}")
        
        # 5. 保存
        self.save_to_db(res_daily, mode='append')
        logger.info("✅ 增量任务完成")

if __name__ == "__main__":
    import argparse
    
    # 简单的命令行参数控制
    # python calc_indicators.py --mode=init  (全量)
    # python calc_indicators.py              (默认增量)
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='daily', help='init or daily')
    args = parser.parse_args()
    
    calc = IndicatorCalculator()
    if args.mode == 'init':
        calc.run_init()
    else:
        calc.run_daily()