# 获取板块指数k线
import sys
import os
import time
import logging
import pandas as pd
import akshare as ak
from datetime import timedelta
from sqlalchemy import text
import ssl

# ================= 🚑 网络急救包 (新增部分) =================
# 1. 强制关闭系统代理 (解决 Mac 开 VPN 导致无法连接国内接口的问题)
# 这一步非常关键！防止 requests 库自动读取你的梯子配置
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if k in os.environ:
        del os.environ[k]

# 2. 忽略 SSL 证书验证 (解决 HTTPSConnectionPool 报错)
ssl._create_default_https_context = ssl._create_unverified_context
# ==========================================================
# ================= 环境路径适配 =================
# 确保脚本能找到 backend/app 目录 (无论是在根目录还是子目录运行)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

# 导入你的配置核心
from app.core.database import get_engine

# ================= 日志配置 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SectorKlineManager:
    def __init__(self):
        self.engine = get_engine()
        self.table_name = "sector_daily_prices"
        self.temp_table = "temp_sector_k_update"

    def _init_table(self):
        """确保目标表存在"""
        with self.engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    sector_name TEXT,
                    trade_date DATE,
                    open FLOAT,
                    close FLOAT,
                    high FLOAT,
                    low FLOAT,
                    volume FLOAT,
                    PRIMARY KEY (sector_name, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_sector_date ON {self.table_name} (trade_date);
            """))

    def get_start_date(self, sector_name: str) -> str:
        """
        核心逻辑：检查数据库，决定是【全量下载】还是【增量更新】
        返回格式: 'YYYYMMDD'
        """
        query = text(f"SELECT MAX(trade_date) FROM {self.table_name} WHERE sector_name = :name")
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query, {"name": sector_name}).scalar()
            
            if result:
                # 如果有数据，从最后一天数据的"下一天"开始下载
                next_date = result + timedelta(days=1)
                return next_date.strftime("%Y%m%d")
            else:
                # 如果没数据，默认从 2000年开始 (全量)
                return "20000101"
        except Exception as e:
            logger.warning(f"获取起始日期失败，默认全量下载: {e}")
            return "20000101"

    def fetch_data(self, name: str, s_type: str, start_date: str) -> pd.DataFrame:
        """调用 AkShare 接口，支持指定开始日期"""
        # AkShare 的接口通常是用 end_date='20500101' 来代表“直到最新”
        end_date = "20500101" 
        
        try:
            if s_type == 'Industry':
                # 注意：部分 Akshare 接口参数名可能不同，这里以标准接口为例
                # 东方财富行业板块
                df = ak.stock_board_industry_hist_em(
                    symbol=name, 
                    start_date=start_date, 
                    end_date=end_date, 
                    adjust=""
                )
            else:
                # 东方财富概念板块
                df = ak.stock_board_concept_hist_em(
                    symbol=name, 
                    start_date=start_date, 
                    end_date=end_date, 
                    adjust=""
                )
            return df
        except Exception as e:
            # 某些极个别板块可能接口报错，或者该时间段无数据
            return pd.DataFrame()

    def save_data(self, df: pd.DataFrame, name: str):
        """清洗并执行 Upsert (更新插入)"""
        if df is None or df.empty:
            return False

        # 1. 字段映射与清洗
        cols_map = {
            '日期': 'trade_date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume'
        }
        # 兼容可能存在的不同列名
        df = df.rename(columns=cols_map)
        
        # 确保必备列存在
        required_cols = ['trade_date', 'open', 'close', 'high', 'low', 'volume']
        if not all(col in df.columns for col in required_cols):
            return False

        df['sector_name'] = name
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        
        # 过滤掉非交易日或空数据
        final_df = df[['sector_name', 'trade_date', 'open', 'close', 'high', 'low', 'volume']].dropna()

        if final_df.empty:
            return False

        # 2. 入库逻辑 (使用临时表 + Upsert 以保证幂等性)
        with self.engine.begin() as conn:
            # 写入临时表
            final_df.to_sql(self.temp_table, conn, if_exists='replace', index=False)
            
            # 执行合并：如果 (name, date) 冲突，则更新数据（修复历史），否则插入
            upsert_sql = text(f"""
                INSERT INTO {self.table_name} 
                SELECT * FROM {self.temp_table}
                ON CONFLICT (sector_name, trade_date) 
                DO UPDATE SET 
                    open = EXCLUDED.open,
                    close = EXCLUDED.close,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    volume = EXCLUDED.volume;
            """)
            conn.execute(upsert_sql)
            
            # 清理临时表 (可选，drop table)
            # conn.execute(text(f"DROP TABLE {self.temp_table}"))
            
        return True

    def run(self):
        logger.info("🚀 启动 [板块 K 线] 智能同步任务...")
        self._init_table()

        # 1. 获取所有板块列表
        try:
            df_sectors = pd.read_sql("SELECT DISTINCT sector_name, sector_type FROM stock_sector_map", self.engine)
        except Exception:
            logger.error("❌ 无法读取 stock_sector_map 表，请先运行 init_sector_data.py！")
            return

        total = len(df_sectors)
        logger.info(f"📋 待处理板块总数: {total}")

        update_count = 0
        skip_count = 0

        for i, row in df_sectors.iterrows():
            name = row['sector_name']
            s_type = row['sector_type']
            
            # 2. 智能判断起始日期
            start_date = self.get_start_date(name)
            is_incremental = start_date != "20000101"
            mode_str = f"增量[{start_date}]" if is_incremental else "全量"

            print(f"[{i+1}/{total}] {mode_str}同步: {name} ...", end="\r")

            # 3. 下载数据
            df_raw = self.fetch_data(name, s_type, start_date)

            # 4. 保存数据
            if not df_raw.empty:
                if self.save_data(df_raw, name):
                    update_count += 1
            else:
                skip_count += 1
            
            # 礼貌爬虫，避免被封 IP
            time.sleep(0.05)

        print(f"\n🎉 同步完成！更新/插入板块数: {update_count}, 无新数据/跳过: {skip_count}")

if __name__ == "__main__":
    manager = SectorKlineManager()
    manager.run()