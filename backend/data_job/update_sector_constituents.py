"""
EvoAlpha OS - 板块成分股更新
更新板块成分股列表和权重
"""

import sys
import os
import time
import logging
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime

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


class SectorConstituentsManager:
    def __init__(self):
        self.engines = get_active_engines()
        self.table_name = "stock_sector_map"

    def get_sector_list(self):
        """获取板块列表"""
        try:
            # 获取行业板块列表
            df = ak.stock_board_industry_name_em()
            logger.info(f"✅ 获取到 {len(df)} 个行业板块")
            return df
        except Exception as e:
            logger.error(f"❌ 获取板块列表失败: {e}")
            return None

    def fetch_sector_stocks(self, sector_name):
        """
        获取板块成分股

        Args:
            sector_name: 板块名称

        Returns:
            DataFrame: 成分股数据
        """
        try:
            # 获取板块成分股
            df = ak.stock_board_industry_cons_em(symbol=sector_name)

            if df.empty:
                logger.warning(f"⚠️  板块 {sector_name} 无成分股数据")
                return None

            # 数据清洗
            df = df.rename(columns={
                '代码': 'symbol',
                '名称': 'name',
                '权重': 'weight',
            })

            # 添加板块名称
            df['sector_name'] = sector_name

            # 选择需要的列
            df = df[['symbol', 'sector_name', 'weight']]

            logger.info(f"  ✅ {sector_name}: {len(df)} 只成分股")
            return df

        except Exception as e:
            logger.error(f"❌ 获取 {sector_name} 成分股失败: {e}")
            return None

    def save_sector_stocks(self, df):
        """
        保存板块成分股

        Args:
            df: 成分股数据
        """
        if df is None or df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 删除该板块的旧数据
                    for sector_name in df['sector_name'].unique():
                        conn.execute(text(f"""
                            DELETE FROM {self.table_name}
                            WHERE sector_name = '{sector_name}'
                        """))

                    # 插入新数据
                    df.to_sql(self.table_name, conn, if_exists='append', index=False)

                    logger.info(f"✅ [{mode}] 保存 {len(df)} 条成分股关系")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存成分股失败: {e}")

    def run(self, top_n=20):
        """
        执行板块成分股更新

        Args:
            top_n: 更新前N个板块的成分股
        """
        logger.info("🚀 开始更新板块成分股...")

        # 获取板块列表
        sector_list = self.get_sector_list()

        if sector_list is None or sector_list.empty:
            logger.error("❌ 未获取到板块列表")
            return

        # 更新前N个板块
        sectors_to_update = sector_list.head(top_n)['板块名称'].tolist()

        logger.info(f"📊 将更新前 {len(sectors_to_update)} 个板块的成分股...")

        all_stocks = []

        for i, sector_name in enumerate(sectors_to_update, 1):
            logger.info(f"[{i}/{len(sectors_to_update)}] 更新 {sector_name}...")

            try:
                df = self.fetch_sector_stocks(sector_name)
                if df is not None:
                    all_stocks.append(df)

                # 避免请求过快
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"❌ {sector_name} 更新失败: {e}")
                continue

        # 合并所有数据并保存
        if all_stocks:
            combined_df = pd.concat(all_stocks, ignore_index=True)
            self.save_sector_stocks(combined_df)
            logger.info(f"🎉 板块成分股更新完成，共 {len(combined_df)} 条关系")
        else:
            logger.error("❌ 未获取到任何成分股数据")


if __name__ == "__main__":
    manager = SectorConstituentsManager()
    manager.run(top_n=20)  # 默认更新前20个板块
