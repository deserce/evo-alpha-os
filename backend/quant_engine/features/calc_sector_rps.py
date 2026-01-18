# backend/quant_engine/features/calc_sector_rps.py

import sys
import os
import time
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import text

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

from app.core.database import get_engine

# ================= 配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SectorRPSCalculator:
    def __init__(self):
        self.engine = get_engine()
        self.source_table = "sector_daily_prices"      # 输入：板块K线
        self.target_table = "quant_feature_sector_rps" # 输出：板块量化因子表
        self.periods = [5, 10, 20, 50, 120, 250]
        
        # ✅ 新增：板块黑名单关键字
        # 凡是板块名称包含这些词的，全部剔除，不计算RPS
        self.blacklist = [
            "昨日", "连板", "涨停", "ST", "AB股", 
            "昨日涨停", "昨日连板", "含一字", "炸板"
        ]

    def _init_table(self):
        """初始化板块因子表结构"""
        with self.engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self.target_table} (
                    sector_name VARCHAR(50),
                    trade_date DATE,
                    -- 涨幅 (Change)
                    chg_5 FLOAT, chg_10 FLOAT, chg_20 FLOAT, 
                    chg_50 FLOAT, chg_120 FLOAT, chg_250 FLOAT,
                    -- 强度 (RPS)
                    rps_5 FLOAT, rps_10 FLOAT, rps_20 FLOAT, 
                    rps_50 FLOAT, rps_120 FLOAT, rps_250 FLOAT,
                    PRIMARY KEY (sector_name, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_sec_rps_date ON {self.target_table} (trade_date);
            """))

    def load_data(self, start_date=None):
        """加载数据 (支持增量窗口)"""
        condition = f"WHERE trade_date >= '{start_date}'" if start_date else ""
        query = f"SELECT trade_date, sector_name, close FROM {self.source_table} {condition} ORDER BY trade_date"
        
        logger.info(f"📥 读取板块数据 (Start: {start_date if start_date else 'All'})...")
        try:
            df = pd.read_sql(query, self.engine)
            if not df.empty:
                df['trade_date'] = pd.to_datetime(df['trade_date'])
            return df
        except Exception as e:
            logger.error(f"❌ 读取失败: {e}")
            return pd.DataFrame()

    def compute_features(self, df):
        """核心计算逻辑"""
        if df.empty: return pd.DataFrame()

        # ✅ 核心修改：在计算前，先把垃圾板块过滤掉
        logger.info(f"🧹 正在过滤干扰板块 (规则: {self.blacklist})...")
        original_count = len(df['sector_name'].unique())
        
        # 使用 str.contains 进行过滤 (正则模式，排除包含关键字的行)
        # join(blacklist) 会生成 "昨日|连板|涨停" 这样的正则
        pattern = "|".join(self.blacklist)
        df = df[~df['sector_name'].str.contains(pattern, regex=True, na=False)]
        
        filtered_count = len(df['sector_name'].unique())
        logger.info(f"   已剔除 {original_count - filtered_count} 个干扰板块，剩余 {filtered_count} 个参与排名。")

        # 1. Pivot 宽表
        df_pivot = df.pivot(index='trade_date', columns='sector_name', values='close')
        df_pivot = df_pivot.fillna(method='ffill')

        features_list = []
        
        for n in self.periods:
            # 涨幅
            df_chg = df_pivot.pct_change(n)
            # RPS (过滤后的板块之间进行排名)
            df_rps = df_chg.rank(axis=1, pct=True) * 100
            
            # Stack
            stack_chg = df_chg.stack().reset_index()
            stack_chg.columns = ['trade_date', 'sector_name', f'chg_{n}']
            stack_chg.set_index(['sector_name', 'trade_date'], inplace=True)
            
            stack_rps = df_rps.stack().reset_index()
            stack_rps.columns = ['trade_date', 'sector_name', f'rps_{n}']
            stack_rps.set_index(['sector_name', 'trade_date'], inplace=True)
            
            features_list.append(stack_chg)
            features_list.append(stack_rps)

        # 合并
        df_final = pd.concat(features_list, axis=1).reset_index()
        
        # 格式化
        float_cols = [c for c in df_final.columns if c not in ['sector_name', 'trade_date']]
        df_final[float_cols] = df_final[float_cols].round(4)
        rps_cols = [c for c in float_cols if 'rps' in c]
        df_final[rps_cols] = df_final[rps_cols].round(2)
        
        return df_final

    def save_to_db(self, df, mode='append'):
        if df.empty: return
        logger.info(f"💾 正在保存 {len(df)} 条数据...")
        
        try:
            # 增量模式下，先删掉当天已有的数据 (幂等性)
            if mode == 'append':
                dates = df['trade_date'].unique()
                date_strs = [pd.to_datetime(d).strftime('%Y-%m-%d') for d in dates]
                if date_strs:
                    date_list_sql = "'" + "','".join(date_strs) + "'"
                    with self.engine.begin() as conn:
                        conn.execute(text(f"DELETE FROM {self.target_table} WHERE trade_date IN ({date_list_sql})"))

            df.to_sql(self.target_table, self.engine, if_exists='append', index=False, method='multi', chunksize=5000)
        except Exception as e:
            logger.error(f"❌ 入库失败: {e}")

    def show_top_sectors(self, df):
        """打印最新战况"""
        if df.empty: return
        latest_date = df['trade_date'].max()
        print(f"\n🏆 [{latest_date.date()}] 市场最强主线 (RPS_20 > 95):")
        
        mask = (df['trade_date'] == latest_date) & (df['rps_20'] > 95)
        top_sectors = df[mask].sort_values(by='rps_20', ascending=False)
        
        print("-" * 65)
        print(f"{'板块名称':<14} {'RPS_5':<8} {'RPS_20':<8} {'RPS_50':<8} {'20日涨幅':<8}")
        print("-" * 65)
        for _, row in top_sectors.head(10).iterrows():
            chg_str = f"{row.get('chg_20', 0)*100:.1f}%"
            print(f"{row['sector_name']:<14} {row.get('rps_5',0):<8} {row.get('rps_20',0):<8} {row.get('rps_50',0):<8} {chg_str:<8}")
        print("-" * 65)

    def run_init(self):
        """全量初始化"""
        logger.info("🚀 [Sector RPS] 启动全量重算 (已启用黑名单过滤)...")
        self._init_table()
        with self.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {self.target_table}"))
            
        df = self.load_data(start_date=None)
        res = self.compute_features(df)
        self.save_to_db(res, mode='replace_fast') # 实际上是append
        self.show_top_sectors(res)
        logger.info("✅ 全量任务完成")

    def run_daily(self):
        """增量更新"""
        logger.info("🚀 [Sector RPS] 启动增量更新 (已启用黑名单过滤)...")
        self._init_table()
        
        # 滑动窗口: 过去 400 天
        cutoff_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        df = self.load_data(start_date=cutoff_date)
        
        if df.empty: return
        
        # 计算
        res_full = self.compute_features(df)
        
        # 截取最近 3 天
        target_date_threshold = (datetime.now() - timedelta(days=3))
        res_daily = res_full[res_full['trade_date'] > target_date_threshold].copy()
        
        if res_daily.empty:
            logger.info("⚠️ 无最新数据")
            return
            
        self.save_to_db(res_daily, mode='append')
        self.show_top_sectors(res_daily)
        logger.info("✅ 增量任务完成")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='daily', help='init or daily')
    args = parser.parse_args()
    
    calculator = SectorRPSCalculator()
    if args.mode == 'init':
        calculator.run_init()
    else:
        calculator.run_daily()