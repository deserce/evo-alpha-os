"""
EvoAlpha OS - 板块K线数据采集器
采集板块指数的日级行情数据
"""

import time
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import timedelta, datetime

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

from app.core.database import get_engine

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class SectorKlineCollector(BaseCollector):
    """板块K线数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="sector_kline",
            request_timeout=30,
            request_delay=0.05,
            max_retries=3
        )
        self.engine = get_engine()
        self.table_name = "sector_daily_prices"

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
                    amount FLOAT,
                    pct_chg FLOAT,
                    PRIMARY KEY (sector_name, trade_date)
                );
            """))
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_sector_date ON {self.table_name} (trade_date);"))
            except Exception:
                pass

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
                # 确保是 date 对象
                if isinstance(result, str):
                    result = datetime.strptime(result, '%Y-%m-%d').date()
                elif isinstance(result, datetime):
                    result = result.date()
                next_date = result + timedelta(days=1)
                return next_date.strftime("%Y%m%d")
            else:
                three_years_ago = (datetime.now() - timedelta(days=1095)).strftime("%Y%m%d")
                return three_years_ago
        except Exception as e:
            logger.warning(f"获取起始日期失败，默认下载3年数据: {e}")
            three_years_ago = (datetime.now() - timedelta(days=1095)).strftime("%Y%m%d")
            return three_years_ago

    def fetch_data(self, name: str, s_type: str, start_date: str) -> pd.DataFrame:
        """调用 AkShare 接口，支持指定开始日期"""
        end_date = "20500101"

        try:
            if s_type == 'Industry':
                # 使用基类的重试机制
                df = self._retry_call(
                    ak.stock_board_industry_hist_em,
                    symbol=name,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=""
                )
            else:
                df = self._retry_call(
                    ak.stock_board_concept_hist_em,
                    symbol=name,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=""
                )
            return df
        except Exception as e:
            return pd.DataFrame()

    def save_data(self, df: pd.DataFrame, name: str):
        """清洗并执行 Upsert"""
        if df is None or df.empty:
            return False

        # 1. 字段映射与清洗
        cols_map = {
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg'
        }
        df = df.rename(columns=cols_map)

        # 确保必备列存在
        required_cols = ['trade_date', 'open', 'close', 'high', 'low', 'volume']
        if not all(col in df.columns for col in required_cols):
            return False

        df['sector_name'] = name
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        # 数值型字段转换
        numeric_cols = ['open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 选择需要的列
        final_df = df[['sector_name', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']].dropna(subset=['trade_date'])

        if final_df.empty:
            return False

        # 2. 入库逻辑
        with self.engine.begin() as conn:
            # 删除已存在的数据
            for _, row in final_df.iterrows():
                conn.execute(text(f"""
                    DELETE FROM {self.table_name}
                    WHERE sector_name = :sector_name
                    AND trade_date = :trade_date
                """), {
                    'sector_name': row['sector_name'],
                    'trade_date': row['trade_date']
                })

            # 插入新数据
            final_df.to_sql(self.table_name, conn, if_exists='append', index=False)

        return True

    def run(self):
        """执行板块K线采集"""
        self.log_collection_start()
        logger.info("🚀 启动 [板块 K 线] 智能同步任务...")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_table()

        # 获取所有板块列表
        try:
            df_sectors = pd.read_sql("SELECT DISTINCT sector_name, sector_type FROM stock_sector_map", self.engine)
        except Exception:
            logger.error("❌ 无法读取 stock_sector_map 表，请先运行板块数据采集！")
            self.log_collection_end(False, "无板块数据")
            return

        total = len(df_sectors)
        logger.info(f"📋 待处理板块总数: {total}")

        update_count = 0
        skip_count = 0

        for i, row in df_sectors.iterrows():
            name = row['sector_name']
            s_type = row['sector_type']

            # 智能判断起始日期
            start_date = self.get_start_date(name)
            three_years_ago = (datetime.now() - timedelta(days=1095)).strftime("%Y%m%d")
            is_incremental = start_date != three_years_ago
            mode_str = f"增量[{start_date}]" if is_incremental else "全量[3年]"

            print(f"[{i+1}/{total}] {mode_str}同步: {name} ...", end="\r")

            # 下载数据
            df_raw = self.fetch_data(name, s_type, start_date)

            # 保存数据
            if df_raw is not None and not df_raw.empty:
                if self.save_data(df_raw, name):
                    update_count += 1
            else:
                skip_count += 1

            # 避免请求过快
            time.sleep(self.request_delay)

        print(f"\n🎉 同步完成！更新/插入板块数: {update_count}, 无新数据/跳过: {skip_count}")
        self.log_collection_end(True, f"更新 {update_count}/{total} 个板块")


if __name__ == "__main__":
    collector = SectorKlineCollector()
    collector.run()
