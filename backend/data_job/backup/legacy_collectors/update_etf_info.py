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

# 全面 ETF 配置（涵盖宽基、所有行业、全球市场、商品等）
ETF_CONFIG = {
    # ==================== 宽基指数 ETF ====================
    'broad_market': [
        {'symbol': '510300', 'name': '沪深300ETF', 'fund_type': 'broad_market'},
        {'symbol': '510500', 'name': '中证500ETF', 'fund_type': 'broad_market'},
        {'symbol': '159915', 'name': '创业板ETF', 'fund_type': 'broad_market'},
        {'symbol': '588000', 'name': '科创50ETF', 'fund_type': 'broad_market'},
        {'symbol': '512100', 'name': '中证1000ETF', 'fund_type': 'broad_market'},
        {'symbol': '159901', 'name': '深100ETF', 'fund_type': 'broad_market'},
        {'symbol': '510310', 'name': '沪深300ETF华泰', 'fund_type': 'broad_market'},
    ],

    # ==================== 科技/半导体 ETF ====================
    'tech': [
        {'symbol': '515000', 'name': '5GETF', 'fund_type': 'tech'},
        {'symbol': '515030', 'name': '新能源ETF', 'fund_type': 'tech'},
        {'symbol': '159745', 'name': '芯片ETF', 'fund_type': 'tech'},
        {'symbol': '512760', 'name': 'CXOETF', 'fund_type': 'tech'},
        {'symbol': '515980', 'name': '人工智能ETF', 'fund_type': 'tech'},
        {'symbol': '159857', 'name': '半导体ETF', 'fund_type': 'tech'},
        {'symbol': '159801', 'name': '中韩半导体', 'fund_type': 'tech'},
        {'symbol': '515880', 'name': '通信ETF', 'fund_type': 'tech'},
        {'symbol': '159995', 'name': '芯片ETF', 'fund_type': 'tech'},
    ],

    # ==================== 医药/医疗 ETF ====================
    'healthcare': [
        {'symbol': '512010', 'name': '医药ETF', 'fund_type': 'healthcare'},
        {'symbol': '159938', 'name': '生物医药ETF', 'fund_type': 'healthcare'},
        {'symbol': '512290', 'name': '生物医药', 'fund_type': 'healthcare'},
        {'symbol': '512980', 'name': '传媒ETF', 'fund_type': 'healthcare'},
        {'symbol': '159919', 'name': '医药ETF华泰', 'fund_type': 'healthcare'},
    ],

    # ==================== 消费/酒类 ETF ====================
    'consumer': [
        {'symbol': '159928', 'name': '消费ETF', 'fund_type': 'consumer'},
        {'symbol': '512200', 'name': '消费ETF华宝', 'fund_type': 'consumer'},
        {'symbol': '512170', 'name': '白酒ETF', 'fund_type': 'consumer'},
        {'symbol': '161725', 'name': '白酒ETF招商', 'fund_type': 'consumer'},
        {'symbol': '512600', 'name': '白酒基金', 'fund_type': 'consumer'},
        {'symbol': '159936', 'name': '消费ETF华夏', 'fund_type': 'consumer'},
    ],

    # ==================== 金融 ETF ====================
    'financial': [
        {'symbol': '512800', 'name': '银行ETF', 'fund_type': 'financial'},
        {'symbol': '512880', 'name': '证券ETF', 'fund_type': 'financial'},
        {'symbol': '159940', 'name': '券商ETF', 'fund_type': 'financial'},
        {'symbol': '512870', 'name': '证券ETF华泰', 'fund_type': 'financial'},
        {'symbol': '512000', 'name': '券商ETF华夏', 'fund_type': 'financial'},
    ],

    # ==================== 新能源/光伏/电网 ====================
    'new_energy': [
        {'symbol': '516160', 'name': '新能源ETF', 'fund_type': 'new_energy'},
        {'symbol': '516090', 'name': '光伏ETF', 'fund_type': 'new_energy'},
        {'symbol': '515790', 'name': '光伏ETF华泰', 'fund_type': 'new_energy'},
        {'symbol': '159863', 'name': '光伏ETF华夏', 'fund_type': 'new_energy'},
        {'symbol': '516110', 'name': '新能源车ETF', 'fund_type': 'new_energy'},
    ],

    # ==================== 行业主题 ETF ====================
    'sector_theme': [
        {'symbol': '159993', 'name': '电网设备ETF', 'fund_type': 'sector_theme'},
        {'symbol': '159949', 'name': '软件ETF', 'fund_type': 'sector_theme'},
        {'symbol': '516220', 'name': '化工ETF', 'fund_type': 'sector_theme'},
        {'symbol': '159867', 'name': '化工ETF华泰', 'fund_type': 'sector_theme'},
        {'symbol': '512400', 'name': '有色金属ETF', 'fund_type': 'sector_theme'},
        {'symbol': '516790', 'name': '有色ETF', 'fund_type': 'sector_theme'},
        {'symbol': '159873', 'name': '钢铁ETF', 'fund_type': 'sector_theme'},
        {'symbol': '164403', 'name': '养殖ETF', 'fund_type': 'sector_theme'},
        {'symbol': '159865', 'name': '新能源车ETF华夏', 'fund_type': 'sector_theme'},
    ],

    # ==================== 军工/国防 ====================
    'military': [
        {'symbol': '512660', 'name': '军工ETF', 'fund_type': 'military'},
        {'symbol': '512810', 'name': '军工ETF华宝', 'fund_type': 'military'},
        {'symbol': '515220', 'name': '国防ETF', 'fund_type': 'military'},
        {'symbol': '512670', 'name': '国防ETF华泰', 'fund_type': 'military'},
    ],

    # ==================== 红利/价值 ====================
    'dividend': [
        {'symbol': '515080', 'name': '红利ETF', 'fund_type': 'dividend'},
        {'symbol': '512890', 'name': '红利低波', 'fund_type': 'dividend'},
        {'symbol': '515180', 'name': '红利ETF华泰', 'fund_type': 'dividend'},
        {'symbol': '159905', 'name': '红利ETF华夏', 'fund_type': 'dividend'},
    ],

    # ==================== 港股 ETF ====================
    'hongkong': [
        {'symbol': '513600', 'name': '恒指ETF', 'fund_type': 'hongkong'},
        {'symbol': '159920', 'name': '恒生ETF', 'fund_type': 'hongkong'},
        {'symbol': '159760', 'name': '港股ETF', 'fund_type': 'hongkong'},
        {'symbol': '513660', 'name': '恒生科技ETF', 'fund_type': 'hongkong'},
        {'symbol': '159741', 'name': '港股通50', 'fund_type': 'hongkong'},
    ],

    # ==================== 美股 ETF ====================
    'us_market': [
        {'symbol': '513100', 'name': '纳指ETF', 'fund_type': 'us_market'},
        {'symbol': '513500', 'name': '标普500', 'fund_type': 'us_market'},
        {'symbol': '159941', 'name': '纳指ETF华夏', 'fund_type': 'us_market'},
        {'symbol': '513650', 'name': '纳斯达克ETF', 'fund_type': 'us_market'},
        {'symbol': '513300', 'name': '纳斯达克ETF华泰', 'fund_type': 'us_market'},
    ],

    # ==================== 日经/其他海外 ====================
    'global_overseas': [
        {'symbol': '513000', 'name': '日经225ETF', 'fund_type': 'global_overseas'},
        {'symbol': '513800', 'name': '日经ETF', 'fund_type': 'global_overseas'},
        {'symbol': '513520', 'name': '日经225ETF华泰', 'fund_type': 'global_overseas'},
    ],

    # ==================== 商品/黄金 ====================
    'commodity': [
        {'symbol': '518880', 'name': '黄金ETF', 'fund_type': 'commodity'},
        {'symbol': '159934', 'name': '黄金ETF华安', 'fund_type': 'commodity'},
        {'symbol': '159985', 'name': '豆粕ETF', 'fund_type': 'commodity'},
        {'symbol': '159937', 'name': '黄金基金', 'fund_type': 'commodity'},
    ],
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
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            );
                        """))
                        logger.info(f"✅ [{mode}] 表 {self.table_name} 创建成功")
                    else:
                        logger.info(f"ℹ️  [{mode}] 表 {self.table_name} 已存在")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建表失败: {e}")

    def fetch_etf_info(self, symbol, name, fund_type):
        """
        获取单个 ETF 的基本信息

        Args:
            symbol: ETF 代码
            name: ETF 名称
            fund_type: ETF 类型

        Returns:
            dict: ETF 信息
        """
        try:
            # 注意：AkShare 的 ETF 接口不提供基金的静态信息
            # 我们使用配置中的基础信息
            return {
                'symbol': symbol,
                'name': name,
                'fund_type': fund_type,
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
                name = etf_config['name']
                fund_type_value = etf_config['fund_type']

                # 获取 ETF 基础信息
                etf_info = self.fetch_etf_info(symbol, name, fund_type_value)

                if etf_info:
                    all_etfs.append(etf_info)
                    logger.info(f"  ✅ {symbol} - {name}")

        # 保存到数据库
        if all_etfs:
            self.save_etf_info(all_etfs)
            logger.info(f"🎉 ETF 信息采集完成，共 {len(all_etfs)} 只")
        else:
            logger.error("❌ 未采集到任何 ETF 信息")


if __name__ == "__main__":
    manager = ETFInfoManager()
    manager.run()
