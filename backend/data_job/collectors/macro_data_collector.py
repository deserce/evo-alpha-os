"""
EvoAlpha OS - 宏观经济数据采集器
采集 GDP、CPI、PMI 等宏观数据
"""

import time
import pandas as pd
import akshare as ak
from sqlalchemy import text

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

from app.core.database import get_active_engines

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class MacroDataCollector(BaseCollector):
    """宏观经济数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="macro_data",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.engines = get_active_engines()
        self.table_name = "macro_indicators"

    @staticmethod
    def _parse_chinese_month(period_series: pd.Series) -> pd.Series:
        """将 2026年03月份 统一转换成月初日期。"""
        parts = period_series.astype(str).str.extract(r"(?P<year>\d{4})年(?P<month>\d{1,2})月")
        normalized = parts["year"] + "-" + parts["month"].str.zfill(2) + "-01"
        return pd.to_datetime(normalized, errors="coerce")

    def _init_table(self):
        """初始化宏观指标表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建宏观指标表...")
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.table_name} (
                            indicator_name VARCHAR(50),
                            indicator_code VARCHAR(20),
                            period VARCHAR(20),
                            value FLOAT,
                            forecast_value FLOAT,
                            previous_value FLOAT,
                            unit VARCHAR(20),
                            publish_date DATE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (indicator_code, period)
                        );
                    """))
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_macro_date ON {self.table_name} (publish_date);"))
                    except Exception:
                        pass
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_macro_name ON {self.table_name} (indicator_name);"))
                    except Exception:
                        pass
                    logger.info(f"✅ [{mode}] 宏观指标表创建成功")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建宏观指标表失败: {e}")

    def fetch_gdp(self):
        """获取GDP数据"""
        try:
            # 使用基类的重试机制
            df = self._retry_call(ak.macro_china_gdp)
            if df.empty:
                return None

            # 数据清洗
            df = df.rename(columns={
                '季度': 'period',
                '国内生产总值-绝对值': 'value',
            })
            df['indicator_name'] = 'GDP'
            df['indicator_code'] = 'GDP'
            df['unit'] = '亿元'
            df['forecast_value'] = None
            df['previous_value'] = None

            # 季度格式转换：2025年第1-4季度 -> 2025-Q4
            df['period'] = df['period'].str.replace('年第1-4季度', '-Q4').str.replace('年第1-3季度', '-Q3').str.replace('年第1-2季度', '-Q2').str.replace('年第1季度', '-Q1')

            # 转换为日期（季度末）
            quarter_period = pd.PeriodIndex(df['period'], freq='Q')
            df['publish_date'] = quarter_period.to_timestamp(how='end').date

            df = df[['indicator_name', 'indicator_code', 'period', 'value', 'forecast_value', 'previous_value', 'unit', 'publish_date']]
            logger.info(f"  ✅ GDP: {len(df)} 条数据")
            return df

        except Exception as e:
            logger.error(f"❌ GDP数据获取失败: {e}")
            return None

    def fetch_cpi(self):
        """获取CPI数据"""
        try:
            # 使用基类的重试机制
            df = self._retry_call(ak.macro_china_cpi)
            if df.empty:
                return None

            # 东方财富月度 CPI 更稳定，这里保存全国同比作为核心指标。
            df = df.rename(columns={
                '月份': 'period',
                '全国-同比增长': 'value',
            })
            df['indicator_name'] = 'CPI同比'
            df['indicator_code'] = 'CPI_YOY'
            df['unit'] = '%'
            df['forecast_value'] = None
            df['previous_value'] = df['value'].shift(-1)
            parsed_date = self._parse_chinese_month(df['period'])
            df['period'] = parsed_date.dt.strftime('%Y-%m')
            df['publish_date'] = parsed_date.dt.date

            # 删除重复数据（保留最新的）
            df = df.drop_duplicates(subset=['period'], keep='last')

            # 只保留需要的列
            df = df[['indicator_name', 'indicator_code', 'period', 'value', 'forecast_value', 'previous_value', 'unit', 'publish_date']]
            logger.info(f"  ✅ CPI: {len(df)} 条数据")
            return df

        except Exception as e:
            logger.error(f"❌ CPI数据获取失败: {e}")
            return None

    def fetch_pmi(self):
        """获取PMI数据（制造业采购经理指数）"""
        try:
            # 使用基类的重试机制
            df = self._retry_call(ak.macro_china_pmi_yearly)
            if df.empty:
                return None

            # 数据清洗：映射实际字段
            df = df.rename(columns={
                '商品': 'indicator_name',
                '日期': 'publish_date',
                '今值': 'value',
                '预测值': 'forecast_value',
                '前值': 'previous_value'
            })
            df['indicator_code'] = 'PMI'
            df['unit'] = '%'
            df['publish_date'] = pd.to_datetime(df['publish_date'], format='%Y-%m-%d', errors='coerce')
            df['period'] = df['publish_date'].dt.strftime('%Y-%m-%d')

            # 删除重复数据（保留最新的）
            df = df.drop_duplicates(subset=['period'], keep='last')

            # 只保留需要的列
            df = df[['indicator_name', 'indicator_code', 'period', 'value', 'forecast_value', 'previous_value', 'unit', 'publish_date']]
            logger.info(f"  ✅ PMI: {len(df)} 条数据")
            return df

        except Exception as e:
            logger.error(f"❌ PMI数据获取失败: {e}")
            return None

    def fetch_ppi(self):
        """获取PPI数据（工业生产者出厂价格指数）"""
        try:
            df = self._retry_call(ak.macro_china_ppi)
            if df.empty:
                return None

            df = df.rename(columns={
                '月份': 'period',
                '当月同比增长': 'value',
            })
            df['indicator_name'] = 'PPI同比'
            df['indicator_code'] = 'PPI_YOY'
            df['unit'] = '%'
            df['forecast_value'] = None
            df['previous_value'] = df['value'].shift(-1)
            parsed_date = self._parse_chinese_month(df['period'])
            df['period'] = parsed_date.dt.strftime('%Y-%m')
            df['publish_date'] = parsed_date.dt.date

            df = df.drop_duplicates(subset=['period'], keep='last')
            df = df[['indicator_name', 'indicator_code', 'period', 'value', 'forecast_value', 'previous_value', 'unit', 'publish_date']]
            logger.info(f"  ✅ PPI: {len(df)} 条数据")
            return df

        except Exception as e:
            logger.error(f"❌ PPI数据获取失败: {e}")
            return None

    def save_macro_data(self, all_data):
        """
        保存宏观数据

        Args:
            all_data: 所有宏观数据的列表
        """
        if not all_data:
            logger.warning("⚠️  宏观数据为空")
            return

        # 合并所有数据
        combined_df = pd.concat(all_data, ignore_index=True)

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 逐个指标删除旧数据并插入新数据
                    for indicator_code in combined_df['indicator_code'].unique():
                        df_indicator = combined_df[combined_df['indicator_code'] == indicator_code]

                        # 删除旧数据
                        conn.execute(text(f"""
                            DELETE FROM {self.table_name}
                            WHERE indicator_code = :indicator_code
                        """), {"indicator_code": indicator_code})

                        # 插入新数据（使用 chunksize 避免 SQLite 变量限制）
                        df_indicator.to_sql(self.table_name, conn, if_exists='append', index=False,
                                          method='multi', chunksize=100)

                    logger.info(f"✅ [{mode}] 保存 {len(combined_df)} 条宏观数据")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存宏观数据失败: {e}")

    def run(self):
        """执行宏观数据采集"""
        self.log_collection_start()
        logger.info("🚀 开始采集宏观数据...")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        # 初始化表
        self._init_table()

        # 采集各类宏观数据
        all_data = []

        # GDP
        logger.info("📊 采集 GDP 数据...")
        gdp_data = self.fetch_gdp()
        if gdp_data is not None:
            all_data.append(gdp_data)

        time.sleep(1)

        # CPI
        logger.info("📊 采集 CPI 数据...")
        cpi_data = self.fetch_cpi()
        if cpi_data is not None:
            all_data.append(cpi_data)

        time.sleep(1)

        # PMI
        logger.info("📊 采集 PMI 数据...")
        pmi_data = self.fetch_pmi()
        if pmi_data is not None:
            all_data.append(pmi_data)

        time.sleep(1)

        # PPI
        logger.info("📊 采集 PPI 数据...")
        ppi_data = self.fetch_ppi()
        if ppi_data is not None:
            all_data.append(ppi_data)

        # 保存数据
        if all_data:
            self.save_macro_data(all_data)
            total_count = sum(len(d) for d in all_data)
            logger.info(f"🎉 宏观数据采集完成，共 {total_count} 条")
            self.log_collection_end(True, f"采集 {total_count} 条数据")
        else:
            logger.error("❌ 未获取到任何宏观数据")
            self.log_collection_end(False, "无数据获取")


if __name__ == "__main__":
    collector = MacroDataCollector()
    collector.run()
