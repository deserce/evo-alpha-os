"""
EvoAlpha OS - 股票板块映射数据采集器
采集股票列表和股票-板块映射关系
"""

import time
import random
import pandas as pd
import akshare as ak
from sqlalchemy import text, inspect

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

from app.core.database import get_active_engines
from app.core.config import settings

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class StockSectorListCollector(BaseCollector):
    """股票板块映射数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="stock_sector_list",
            request_timeout=30,
            request_delay=0.05,
            max_retries=5
        )
        self.active_engines = get_active_engines()

    def _init_tables(self):
        """为所有激活的引擎初始化表结构"""
        for name, engine in self.active_engines:
            inspector = inspect(engine)
            try:
                with engine.begin() as conn:
                    if not inspector.has_table("stock_info"):
                        logger.info(f"🛠️ [{name}] 创建表 stock_info...")
                        conn.execute(text("""
                            CREATE TABLE stock_info (
                                symbol VARCHAR(20) PRIMARY KEY,
                                name VARCHAR(100)
                            );
                        """))

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
                        if name == "cloud":
                            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_map_symbol ON stock_sector_map (symbol);"))
            except Exception as e:
                logger.error(f"❌ [{name}] 初始化表结构失败: {e}")

    def update_stock_list(self):
        """获取全市场股票列表"""
        logger.info(f"📋 [1/2] 正在更新股票列表 [模式: {settings.APP_ENV}]")
        try:
            # 使用基类的重试机制
            df = self._retry_call(ak.stock_zh_a_spot_em)
            if df is None:
                return

            df = df.rename(columns={'代码': 'symbol', '名称': 'name'})
            df['symbol'] = df['symbol'].astype(str).str.zfill(6)
            df = df[['symbol', 'name']].drop_duplicates(subset=['symbol'])

            for name, engine in self.active_engines:
                with engine.begin() as conn:
                    if name == "local":
                        df.to_sql('stock_info', conn, if_exists='replace', index=False)
                    else:
                        conn.execute(text("DELETE FROM stock_info"))
                        df.to_sql('stock_info', conn, if_exists='append', index=False, method='multi', chunksize=1000)
                logger.info(f"✅ [{name}] 写入完成。")

        except Exception as e:
            logger.error(f"❌ 更新股票列表失败: {e}")

    def _fetch_and_save_sector_category(self, name_func, cons_func, type_label):
        """内部通用: 抓取并保存板块数据"""
        logger.info(f"🚀 正在处理 [{type_label}] 板块数据...")

        try:
            # 使用基类的重试机制
            df_list = self._retry_call(name_func)
            if df_list is None:
                return
            names = df_list['板块名称'].tolist()
            total = len(names)

            collected_data = []
            for i, name in enumerate(names):
                if i % 10 == 0:
                    print(f"   [{i+1}/{total}] 采集进度: {name} ...", end="\r")

                try:
                    cons = cons_func(symbol=name)
                    if cons is None or cons.empty:
                        continue

                    cons = cons.rename(columns={'代码': 'symbol', '名称': 'name'})
                    cons['symbol'] = cons['symbol'].astype(str).str.zfill(6)
                    cons['sector_name'] = name
                    cons['sector_type'] = type_label

                    collected_data.append(cons[['symbol', 'name', 'sector_name', 'sector_type']])

                    if len(collected_data) >= 50:
                        self._bulk_save_active(collected_data)
                        collected_data = []
                    time.sleep(0.05)
                except Exception:
                    continue

            if collected_data:
                self._bulk_save_active(collected_data)
            print()
            logger.info(f"✅ [{type_label}] 数据处理完毕。")

        except Exception as e:
            logger.error(f"❌ 获取 {type_label} 列表严重失败: {e}")

    def _bulk_save_active(self, df_list):
        """核心保存函数: 分发数据到所有激活的引擎"""
        if not df_list:
            return
        final_df = pd.concat(df_list, ignore_index=True)
        final_df = final_df.drop_duplicates(subset=['sector_name', 'symbol'])

        for name, engine in self.active_engines:
            try:
                with engine.begin() as conn:
                    method = 'multi' if name == "cloud" else None
                    final_df.to_sql('stock_sector_map', conn, if_exists='append', index=False, method=method, chunksize=1000)
            except Exception as e:
                logger.error(f"❌ [{name}] 批量写入失败: {e}")

    def update_sectors(self):
        """更新板块映射"""
        logger.info("🧩 [2/2] 开始更新板块映射...")

        for name, engine in self.active_engines:
            try:
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM stock_sector_map"))
                    logger.info(f"🧹 [{name}] 历史映射已清理")
            except Exception:
                pass

        self._fetch_and_save_sector_category(ak.stock_board_industry_name_em, ak.stock_board_industry_cons_em, 'Industry')
        self._fetch_and_save_sector_category(ak.stock_board_concept_name_em, ak.stock_board_concept_cons_em, 'Concept')

    def update_stock_info_industry(self):
        """同步更新 stock_info 表的 industry 字段"""
        logger.info("🔄 同步更新 stock_info 表的 industry 字段...")

        for name, engine in self.active_engines:
            try:
                with engine.begin() as conn:
                    # 从 stock_sector_map 获取每只股票的行业（优先使用行业板块）
                    query = """
                        SELECT symbol, sector_name
                        FROM stock_sector_map
                        WHERE sector_type = 'Industry'
                        GROUP BY symbol, sector_name
                    """
                    df_sector = pd.read_sql(query, engine)

                    if not df_sector.empty:
                        update_count = 0
                        for _, row in df_sector.iterrows():
                            conn.execute(text("""
                                UPDATE stock_info
                                SET industry = :industry
                                WHERE symbol = :symbol
                            """), {"industry": row['sector_name'], "symbol": row['symbol']})
                            update_count += 1

                        logger.info(f"✅ [{name}] 已更新 {update_count} 只股票的行业信息")
            except Exception as e:
                logger.error(f"❌ [{name}] 更新 stock_info.industry 失败: {e}")

    def run(self):
        """统一入口"""
        self.log_collection_start()

        if not self.active_engines:
            logger.error("🚫 无活跃数据库引擎，请检查 APP_ENV 设置")
            self.log_collection_end(False, "无数据库引擎")
            return

        try:
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_tables()
        self.update_stock_list()
        self.update_sectors()

        # 同步更新 stock_info 表的 industry 字段
        self.update_stock_info_industry()

        logger.info(f"🎉 任务圆满成功 [模式: {settings.APP_ENV}]")
        self.log_collection_end(True, f"完成 [{settings.APP_ENV}] 模式采集")


if __name__ == "__main__":
    collector = StockSectorListCollector()
    collector.run()
