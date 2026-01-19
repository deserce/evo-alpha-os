"""
EvoAlpha OS - 财务摘要数据采集器
采集股票的财务业绩报表数据
"""

import time
import random
import pandas as pd
import akshare as ak
from datetime import date
from sqlalchemy import text, inspect

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


class FinanceSummaryCollector(BaseCollector):
    """财务摘要数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="finance_summary",
            request_timeout=30,
            request_delay=2,
            max_retries=3
        )
        self.engine = get_engine()
        self.table_name = "stock_finance_summary"

    def _init_table(self):
        """初始化表结构"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 创建表 {self.table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        code VARCHAR(20),
                        name VARCHAR(50),
                        report_date DATE,
                        eps FLOAT,
                        net_profit_up FLOAT,
                        revenue_up FLOAT,
                        roe FLOAT,
                        net_margin FLOAT,
                        PRIMARY KEY (code, report_date)
                    );
                """))
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_finance_code ON {self.table_name} (code);"))
                except Exception:
                    pass
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_finance_date ON {self.table_name} (report_date);"))
                except Exception:
                    pass
        else:
            logger.info(f"✅ 表 {self.table_name} 已存在，准备检查更新...")

    def check_date_exists(self, report_date_str: str) -> bool:
        """检查某个季度的数据是否已入库"""
        fmt_date = pd.to_datetime(report_date_str).strftime('%Y-%m-%d')
        try:
            with self.engine.connect() as conn:
                query = text(f"SELECT 1 FROM {self.table_name} WHERE report_date = :dt LIMIT 1")
                result = conn.execute(query, {"dt": fmt_date}).scalar()
                return result is not None
        except Exception:
            return False

    def fetch_and_save(self, target_date: str) -> bool:
        """核心抓取逻辑"""
        try:
            # 使用基类的重试机制
            df = self._retry_call(ak.stock_yjbb_em, date=target_date)

            if df is None or df.empty:
                return False

            rename_map = {
                '股票代码': 'code', '股票简称': 'name',
                '每股收益': 'eps', '净利润-同比增长': 'net_profit_up',
                '营业总收入-同比增长': 'revenue_up', '净资产收益率': 'roe',
                '销售毛利率': 'net_margin'
            }
            df = df.rename(columns=rename_map)

            required_cols = ['code', 'name', 'eps', 'net_profit_up', 'revenue_up', 'roe', 'net_margin']
            for col in required_cols:
                if col not in df.columns:
                    df[col] = 0

            df_save = df[required_cols].copy()
            df_save['report_date'] = pd.to_datetime(target_date).date()
            df_save['code'] = df_save['code'].astype(str).str.zfill(6)
            df_save = df_save.replace(['-', ''], 0)

            num_cols = ['eps', 'net_profit_up', 'revenue_up', 'roe', 'net_margin']
            for col in num_cols:
                df_save[col] = pd.to_numeric(df_save[col], errors='coerce').fillna(0)

            fmt_date = pd.to_datetime(target_date).strftime('%Y-%m-%d')

            with self.engine.begin() as conn:
                conn.execute(text(f"DELETE FROM {self.table_name} WHERE report_date = :dt"), {"dt": fmt_date})
                df_save.to_sql(self.table_name, conn, if_exists='append', index=False, method='multi', chunksize=100)

            return True

        except Exception as e:
            logger.error(f"抓取 {target_date} 异常: {e}")
            raise e

    def run(self):
        """执行财务数据采集"""
        self.log_collection_start()
        logger.info("📈 启动财务业绩报表同步...")

        try:
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_table()

        curr_year = date.today().year
        years = range(curr_year, curr_year - 6, -1)
        quarters = ["1231", "0930", "0630", "0331"]

        date_tasks = []
        for y in years:
            for q in quarters:
                d_str = f"{y}{q}"
                if d_str <= date.today().strftime("%Y%m%d"):
                    date_tasks.append(d_str)

        total = len(date_tasks)
        success_count = 0

        for i, target_date in enumerate(date_tasks):
            if self.check_date_exists(target_date):
                print(f"[{i+1}/{total}] ⏩ {target_date} 已存在，跳过...", end="\r")
                success_count += 1
                continue

            max_retries = 3
            success = False

            for attempt in range(max_retries):
                try:
                    logger.info(f"[{i+1}/{total}] ⏳ 正在抓取 {target_date} (Try {attempt+1})...")
                    has_data = self.fetch_and_save(target_date)

                    if has_data:
                        logger.info(f"   ✅ {target_date} 入库成功")
                        success = True
                        success_count += 1
                    else:
                        logger.warning(f"   ⚠️ {target_date} 无数据 (可能是财报未出)")
                        success = True

                    time.sleep(random.uniform(2, 4))
                    break

                except Exception:
                    time.sleep(5 * (attempt + 1))

            if not success:
                logger.error(f"   ❌ {target_date} 多次重试失败，跳过。")

        logger.info(f"🎉 财务数据同步完成！成功: {success_count}/{total}")
        self.log_collection_end(True, f"成功 {success_count}/{total} 个季度")


if __name__ == "__main__":
    collector = FinanceSummaryCollector()
    collector.run()
