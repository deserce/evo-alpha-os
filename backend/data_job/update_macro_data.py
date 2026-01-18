"""
EvoAlpha OS - 宏观经济数据采集
采集 GDP、CPI、PMI 等宏观数据
"""

import sys
import os
import time
import logging
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime, timedelta

# ================= 网络急救包 =================
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if k in os.environ:
        del os.environ[k]

import ssl
ssl._create_default_https_context = ssl._create_unverified_context
# ==========================================================

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.database import get_active_engines

# ================= 日志配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class MacroDataManager:
    def __init__(self):
        self.engines = get_active_engines()
        self.table_name = "macro_indicators"

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
                            unit VARCHAR(20),
                            publish_date DATE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (indicator_code, period)
                        );
                    """))
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_macro_date ON {self.table_name} (publish_date);"))
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_macro_name ON {self.table_name} (indicator_name);"))
                    logger.info(f"✅ [{mode}] 宏观指标表创建成功")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建宏观指标表失败: {e}")

    def fetch_gdp(self):
        """获取GDP数据"""
        try:
            df = ak.macro_china_gdp()
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
            df['publish_date'] = pd.to_datetime(df['period'])

            df = df[['indicator_name', 'indicator_code', 'period', 'value', 'unit', 'publish_date']]
            logger.info(f"  ✅ GDP: {len(df)} 条数据")
            return df

        except Exception as e:
            logger.error(f"❌ GDP数据获取失败: {e}")
            return None

    def fetch_cpi(self):
        """获取CPI数据"""
        try:
            df = ak.macro_china_cpi_yearly()
            if df.empty:
                return None

            # 数据清洗
            df = df.rename(columns={
                '年份': 'period',
                '全国': 'value',
            })
            df['indicator_name'] = 'CPI'
            df['indicator_code'] = 'CPI'
            df['unit'] = '%'
            df['publish_date'] = pd.to_datetime(df['period'], format='%Y')

            df = df[['indicator_name', 'indicator_code', 'period', 'value', 'unit', 'publish_date']]
            logger.info(f"  ✅ CPI: {len(df)} 条数据")
            return df

        except Exception as e:
            logger.error(f"❌ CPI数据获取失败: {e}")
            return None

    def fetch_pmi(self):
        """获取PMI数据（制造业采购经理指数）"""
        try:
            df = ak.macro_china_pmie_yearly()
            if df.empty:
                return None

            # 数据清洗
            df = df.rename(columns={
                '年份': 'period',
                '制造业': 'value',
            })
            df['indicator_name'] = 'PMI'
            df['indicator_code'] = 'PMI'
            df['unit'] = '%'
            df['publish_date'] = pd.to_datetime(df['period'], format='%Y')

            df = df[['indicator_name', 'indicator_code', 'period', 'value', 'unit', 'publish_date']]
            logger.info(f"  ✅ PMI: {len(df)} 条数据")
            return df

        except Exception as e:
            logger.error(f"❌ PMI数据获取失败: {e}")
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
                            WHERE indicator_code = '{indicator_code}'
                        """))

                        # 插入新数据
                        df_indicator.to_sql(self.table_name, conn, if_exists='append', index=False)

                    logger.info(f"✅ [{mode}] 保存 {len(combined_df)} 条宏观数据")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存宏观数据失败: {e}")

    def run(self):
        """执行宏观数据采集"""
        logger.info("🚀 开始采集宏观数据...")

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

        # 保存数据
        if all_data:
            self.save_macro_data(all_data)
            logger.info(f"🎉 宏观数据采集完成，共 {len(pd.concat(all_data))} 条")
        else:
            logger.error("❌ 未获取到任何宏观数据")


if __name__ == "__main__":
    manager = MacroDataManager()
    manager.run()
