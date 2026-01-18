"""
EvoAlpha OS - ETF 基础信息采集
获取 ETF 基金的基本信息
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


# ================= ETF 列表配置 =================

# 五类资产 ETF 配置
ETF_CONFIG = {
    # 科技 ETF（进攻）
    'tech': [
        {'symbol': '159915', 'name': '科创板ETF', 'fund_type': 'tech'},
        {'symbol': '515000', 'name': '5GETF', 'fund_type': 'tech'},
        {'symbol': '512760', 'name': 'CXOETF', 'fund_type': 'tech'},
    ],

    # 红利 ETF（防御）
    'dividend': [
        {'symbol': '515080', 'name': '红利ETF', 'fund_type': 'dividend'},
        {'symbol': '512890', 'name': '红利低波', 'fund_type': 'dividend'},
        {'symbol': '510890', 'name': '红利指数', 'fund_type': 'dividend'},
    ],

    # 纳指（海外科技）
    'nasdaq': [
        {'symbol': '159941', 'name': '纳指ETF', 'fund_type': 'nasdaq'},
        {'symbol': '513100', 'name': '纳指ETF', 'fund_type': 'nasdaq'},
        {'symbol': '513500', 'name': '标普500', 'fund_type': 'nasdaq'},
    ],

    # 黄金（避险）
    'gold': [
        {'symbol': '518880', 'name': '黄金ETF', 'fund_type': 'gold'},
        {'symbol': '159934', 'name': '黄金ETF', 'fund_type': 'gold'},
    ],

    # 豆粕（特殊对冲）
    'soybean': [
        {'symbol': '159987', 'name': '豆粕ETF', 'fund_type': 'soybean'},
    ]
}


class ETFInfoManager:
    def __init__(self):
        self.engines = get_active_engines()
        self.table_name = "etf_info"

    def _init_table(self):
        """初始化 ETF 信息表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建表 {self.table_name}...")
            try:
                with engine.begin() as conn:
                    # 检查表是否存在
                    inspector_result = conn.execute(text(f"""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='{self.table_name}'
                    """))
                    exists = inspector_result.fetchone() is not None

                    if not exists:
                        conn.execute(text(f"""
                            CREATE TABLE {self.table_name} (
                                symbol VARCHAR(20) PRIMARY KEY,
                                name VARCHAR(100),
                                fund_type VARCHAR(50),
                                underlying_index VARCHAR(100),
                                launch_date DATE,
                                expense_ratio FLOAT,
                                fund_company VARCHAR(100),
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            );
                        """))
                        logger.info(f"✅ [{mode}] 表 {self.table_name} 创建成功")
                    else:
                        logger.info(f"ℹ️  [{mode}] 表 {self.table_name} 已存在")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建表失败: {e}")

    def fetch_etf_info(self, symbol):
        """
        获取单个 ETF 的详细信息

        Args:
            symbol: ETF 代码

        Returns:
            dict: ETF 信息
        """
        try:
            # 使用 AkShare 获取 ETF 基本信息
            df = ak.fund_etf_hist_sina(symbol=symbol)

            if df.empty:
                logger.warning(f"⚠️  ETF {symbol} 无数据")
                return None

            # 获取最新一天的数据
            latest = df.iloc[-1]

            return {
                'symbol': symbol,
                'name': latest.get('name', ''),
                'fund_type': '',
                'underlying_index': '',
                'launch_date': None,
                'expense_ratio': None,
                'fund_company': '',
            }
        except Exception as e:
            logger.error(f"❌ 获取 ETF {symbol} 信息失败: {e}")
            return None

    def save_etf_info(self, etf_list):
        """
        保存 ETF 信息到数据库

        Args:
            etf_list: ETF 信息列表
        """
        if not etf_list:
            logger.warning("⚠️  ETF 列表为空")
            return

        df = pd.DataFrame(etf_list)

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 先删除旧数据
                    conn.execute(text(f"DELETE FROM {self.table_name}"))

                    # 插入新数据
                    df.to_sql(self.table_name, conn, if_exists='append', index=False)

                logger.info(f"✅ [{mode}] 保存 {len(df)} 条 ETF 信息")
            except Exception as e:
                logger.error(f"❌ [{mode}] 保存 ETF 信息失败: {e}")

    def run(self):
        """执行 ETF 信息采集"""
        logger.info("🚀 开始采集 ETF 基础信息...")

        # 初始化表
        self._init_table()

        # 收集所有 ETF 信息
        all_etfs = []

        for fund_type, etf_list in ETF_CONFIG.items():
            logger.info(f"📊 处理 {fund_type} 类 ETF...")

            for etf_config in etf_list:
                symbol = etf_config['symbol']

                # 手动配置的信息
                etf_info = {
                    'symbol': symbol,
                    'name': etf_config['name'],
                    'fund_type': etf_config['fund_type'],
                    'underlying_index': '',
                    'launch_date': None,
                    'expense_ratio': None,
                    'fund_company': '',
                }

                all_etfs.append(etf_info)
                logger.info(f"  ✅ {symbol} - {etf_config['name']}")

                # 避免请求过快
                time.sleep(0.5)

        # 保存到数据库
        if all_etfs:
            self.save_etf_info(all_etfs)
            logger.info(f"🎉 ETF 信息采集完成，共 {len(all_etfs)} 只")
        else:
            logger.error("❌ 未采集到任何 ETF 信息")


if __name__ == "__main__":
    manager = ETFInfoManager()
    manager.run()
