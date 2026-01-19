# backend/data_job/update_stock_sector_list.py

import sys
import os
import time
import random
import logging
import pandas as pd
import akshare as ak
from sqlalchemy import text, inspect
import ssl

# ================= 🚑 网络急救包 =================
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if k in os.environ:
        del os.environ[k]
ssl._create_default_https_context = ssl._create_unverified_context
# ===============================================

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# 💡 引入解耦后的动态引擎获取工具
from app.core.database import get_active_engines
from app.core.config import settings

# ================= 日志配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class StockSectorListManager:
    def __init__(self):
        # 💡 根据 APP_MODE 获取当前所有活跃引擎
        self.active_engines = get_active_engines()

    def _init_tables(self):
        """为所有激活的引擎初始化表结构"""
        for name, engine in self.active_engines:
            inspector = inspect(engine)
            try:
                with engine.begin() as conn:
                    # 1. 股票基础信息表
                    if not inspector.has_table("stock_info"):
                        logger.info(f"🛠️ [{name}] 创建表 stock_info...")
                        conn.execute(text("""
                            CREATE TABLE stock_info (
                                symbol VARCHAR(20) PRIMARY KEY,
                                name VARCHAR(100)
                            );
                        """))
                    
                    # 2. 板块成分映射表
                    if not inspector.has_table("stock_sector_map"):
                        logger.info(f"🛠️ [{name}] 创建表 stock_sector_map...")
                        conn.execute(text("""
                            CREATE TABLE stock_sector_map (
                                symbol VARCHAR(20),
                                name VARCHAR(100),
                                sector_name VARCHAR(100),
                                sector_type VARCHAR(50), 
                                PRIMARY KEY (sector_name, symbol)
                            );
                        """))
                        # SQLite 不需要手动创建索引，但云端建议保留
                        if name == "cloud":
                            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_map_symbol ON stock_sector_map (symbol);"))
            except Exception as e:
                logger.error(f"❌ [{name}] 初始化表结构失败: {e}")

    def _retry_call(self, func, retries=5, delay=3, desc="请求", **kwargs):
        """通用重试机制"""
        for i in range(retries):
            try:
                return func(**kwargs)
            except Exception as e:
                wait_time = delay + random.random() * 2
                logger.warning(f"⚠️ [{desc}] 失败 ({i+1}/{retries})，等待 {wait_time:.1f}s...")
                time.sleep(wait_time)
        return None

    def update_stock_list(self):
        """任务 A: 获取全市场股票列表 (流式写入)"""
        logger.info(f"📋 [1/2] 正在更新股票列表 [模式: {settings.APP_ENV}]")
        try:
            df = self._retry_call(ak.stock_zh_a_spot_em, desc="拉取A股列表")
            if df is None: return
            
            df = df.rename(columns={'代码': 'symbol', '名称': 'name'})
            df['symbol'] = df['symbol'].astype(str).str.zfill(6)
            df = df[['symbol', 'name']].drop_duplicates(subset=['symbol'])

            # 遍历所有激活的引擎写入
            for name, engine in self.active_engines:
                with engine.begin() as conn:
                    if name == "local":
                        # 本地 SQLite 直接覆盖
                        df.to_sql('stock_info', conn, if_exists='replace', index=False)
                    else:
                        # 云端 Postgres 先删后插，使用批量模式优化 RU
                        conn.execute(text("DELETE FROM stock_info"))
                        df.to_sql('stock_info', conn, if_exists='append', index=False, method='multi', chunksize=1000)
                logger.info(f"✅ [{name}] 写入完成。")

        except Exception as e:
            logger.error(f"❌ 更新股票列表失败: {e}")

    def _fetch_and_save_sector_category(self, name_func, cons_func, type_label):
        """内部通用: 抓取并保存板块数据"""
        logger.info(f"🚀 正在处理 [{type_label}] 板块数据...")
        
        try:
            df_list = self._retry_call(name_func, desc=f"获取{type_label}名单")
            if df_list is None: return
            names = df_list['板块名称'].tolist()
            total = len(names)
            
            collected_data = []
            for i, name in enumerate(names):
                if i % 10 == 0: 
                    print(f"   [{i+1}/{total}] 采集进度: {name} ...", end="\r")

                try:
                    cons = cons_func(symbol=name)
                    if cons is None or cons.empty: continue

                    cons = cons.rename(columns={'代码': 'symbol', '名称': 'name'})
                    cons['symbol'] = cons['symbol'].astype(str).str.zfill(6)
                    cons['sector_name'] = name
                    cons['sector_type'] = type_label
                    
                    collected_data.append(cons[['symbol', 'name', 'sector_name', 'sector_type']])
                    
                    # 积攒 50 个板块数据执行一次批量保存
                    if len(collected_data) >= 50:
                        self._bulk_save_active(collected_data)
                        collected_data = []
                    time.sleep(0.05)
                except Exception: continue

            if collected_data:
                self._bulk_save_active(collected_data)
            print() # 换行
            logger.info(f"✅ [{type_label}] 数据处理完毕。")

        except Exception as e:
            logger.error(f"❌ 获取 {type_label} 列表严重失败: {e}")

    def _bulk_save_active(self, df_list):
        """核心保存函数：分发数据到所有激活的引擎"""
        if not df_list: return
        final_df = pd.concat(df_list, ignore_index=True)
        final_df = final_df.drop_duplicates(subset=['sector_name', 'symbol'])
        
        for name, engine in self.active_engines:
            try:
                with engine.begin() as conn:
                    # 本地使用普通写入，云端使用 RU 优化的批量写入
                    method = 'multi' if name == "cloud" else None
                    final_df.to_sql('stock_sector_map', conn, if_exists='append', index=False, method=method, chunksize=1000)
            except Exception as e:
                logger.error(f"❌ [{name}] 批量写入失败: {e}")

    def update_sectors(self):
        """任务 B: 更新板块映射 (清空并重构)"""
        logger.info("🧩 [2/2] 开始更新板块映射...")
        
        # 清空当前激活的库
        for name, engine in self.active_engines:
            try:
                with engine.begin() as conn:
                    # SQLite 不支持 TRUNCATE，统一使用兼容的 DELETE
                    conn.execute(text("DELETE FROM stock_sector_map"))
                    logger.info(f"🧹 [{name}] 历史映射已清理")
            except Exception: pass

        self._fetch_and_save_sector_category(ak.stock_board_industry_name_em, ak.stock_board_industry_cons_em, 'Industry')
        self._fetch_and_save_sector_category(ak.stock_board_concept_name_em, ak.stock_board_concept_cons_em, 'Concept')

    def run(self):
        """统一入口"""
        if not self.active_engines:
            logger.error("🚫 无活跃数据库引擎，请检查 APP_ENV 设置")
            return

        self._init_tables()
        self.update_stock_list()
        self.update_sectors()
        logger.info(f"🎉 任务圆满成功 [模式: {settings.APP_ENV}]")

if __name__ == "__main__":
    manager = StockSectorListManager()
    manager.run()