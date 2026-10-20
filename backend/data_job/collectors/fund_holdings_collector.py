"""
EvoAlpha OS - 基金持股数据采集器
采集基金季度持仓数据
"""

import time
import pandas as pd
import akshare as ak
from datetime import date
from sqlalchemy import text

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


class FundHoldingsCollector(BaseCollector):
    """基金持股数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="fund_holdings",
            request_timeout=30,
            request_delay=2,
            max_retries=3
        )
        self.engine = get_engine()

    def _init_tables(self):
        """初始化基金持仓表"""
        with self.engine.begin() as conn:
            logger.info("🛠️ 创建基金持仓表...")
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS finance_fund_holdings (
                        symbol VARCHAR(20),
                        report_date DATE,
                        fund_count INTEGER,
                        hold_count FLOAT,
                        hold_value FLOAT,
                        hold_change VARCHAR(20),
                        change_value FLOAT,
                        change_ratio FLOAT,
                        PRIMARY KEY (symbol, report_date)
                    );
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_fund_date ON finance_fund_holdings (report_date);"))
                logger.info("✅ 基金持仓表创建成功")
            except Exception as e:
                logger.error(f"❌ 创建基金持仓表失败: {e}")

    @staticmethod
    def _build_target_quarters(years: int = 4) -> list[str]:
        """生成最近若干年的已到期基金持仓报告期。"""
        today_str = date.today().strftime("%Y%m%d")
        curr_year = date.today().year
        quarters = ["1231", "0930", "0630", "0331"]
        target_quarters = []
        for year in range(curr_year, curr_year - years, -1):
            for quarter in quarters:
                report_date = f"{year}{quarter}"
                if report_date <= today_str:
                    target_quarters.append(report_date)
        return target_quarters

    @staticmethod
    def _filter_a_share_codes(df: pd.DataFrame) -> pd.DataFrame:
        """过滤掉 B 股等非 A 股代码，保持和股票 K 线主体一致。"""
        return df[df["symbol"].astype(str).str.match(r"^(00|30|60|68|83|87|43|92)\d{4}$")].copy()

    def update_fund_holdings(self, years: int = 4):
        """采集基金持仓季度数据"""
        logger.info("🚀 开始采集基金持仓数据...")

        target_quarters = self._build_target_quarters(years=years)

        success_count = 0
        fail_count = 0

        for q_date in target_quarters:
            report_date = f"{q_date[:4]}-{q_date[4:6]}-{q_date[6:]}"

            try:
                with self.engine.connect() as conn:
                    row_count = conn.execute(text("""
                        SELECT COUNT(*) FROM finance_fund_holdings
                        WHERE report_date = :report_date LIMIT 1
                    """), {"report_date": report_date}).scalar()

                if row_count and row_count >= 1000:
                    logger.info(f"⏭️  {report_date} 数据已存在，跳过")
                    success_count += 1
                    continue
            except Exception:
                pass

            logger.info(f"📥 正在下载基金持仓: {report_date} ...")

            try:
                # 使用基类的重试机制
                df = self._retry_call(ak.stock_report_fund_hold, date=q_date)

                if df.empty:
                    logger.warning(f"⚠️  {report_date} 无数据")
                    fail_count += 1
                    time.sleep(2)
                    continue

                df_processed = df.rename(columns={
                    '股票代码': 'symbol',
                    '持有基金家数': 'fund_count',
                    '持股总数': 'hold_count',
                    '持股市值': 'hold_value',
                    '持股变化': 'hold_change',
                    '持股变动数值': 'change_value',
                    '持股变动比例': 'change_ratio'
                })

                df_processed['symbol'] = df_processed['symbol'].astype(str).str.zfill(6)
                df_processed['report_date'] = report_date

                for col in ['fund_count', 'hold_count', 'hold_value', 'change_value', 'change_ratio']:
                    df_processed[col] = pd.to_numeric(df_processed[col], errors='coerce')

                columns = ['symbol', 'report_date', 'fund_count', 'hold_count',
                          'hold_value', 'hold_change', 'change_value', 'change_ratio']
                df_save = df_processed[columns].copy()
                df_save = df_save.dropna(subset=['symbol', 'report_date'])
                df_save = self._filter_a_share_codes(df_save)

                if df_save.empty:
                    logger.warning(f"⚠️  {report_date} 清理后无有效数据")
                    fail_count += 1
                    time.sleep(2)
                    continue

                with self.engine.begin() as conn:
                    conn.execute(text("""
                        DELETE FROM finance_fund_holdings
                        WHERE report_date = :report_date
                    """), {"report_date": report_date})
                    df_save.to_sql('finance_fund_holdings', conn, if_exists='append',
                                  index=False, method='multi', chunksize=1000)

                logger.info(f"✅ {report_date} 入库成功: {len(df_save)} 条记录")
                success_count += 1
                time.sleep(3)

            except Exception as e:
                logger.error(f"❌ {report_date} 采集失败: {e}")
                fail_count += 1
                time.sleep(2)

        logger.info(f"\n📊 基金持仓采集完成: 成功 {success_count}/{len(target_quarters)} 个季度")

    def run(self):
        """执行基金持仓数据采集"""
        self.log_collection_start()
        logger.info("🚀 基金持仓数据采集任务启动")

        try:
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_tables()
        self.update_fund_holdings()

        logger.info("🎉 基金持仓数据采集完成！")
        self.log_collection_end(True, "基金持仓采集完成")


if __name__ == "__main__":
    collector = FundHoldingsCollector()
    collector.run()
