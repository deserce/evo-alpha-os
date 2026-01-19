"""
EvoAlpha OS - ETF 基础信息采集器
获取 ETF 基金的基本信息
"""

import pandas as pd
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


# ETF 列表配置
ETF_CONFIG = {
    'broad_market': [
        {'symbol': '510300', 'name': '沪深300ETF', 'fund_type': 'broad_market'},
        {'symbol': '510500', 'name': '中证500ETF', 'fund_type': 'broad_market'},
        {'symbol': '159915', 'name': '创业板ETF', 'fund_type': 'broad_market'},
        {'symbol': '588000', 'name': '科创50ETF', 'fund_type': 'broad_market'},
        {'symbol': '512100', 'name': '中证1000ETF', 'fund_type': 'broad_market'},
        {'symbol': '159901', 'name': '深100ETF', 'fund_type': 'broad_market'},
        {'symbol': '510310', 'name': '沪深300ETF华泰', 'fund_type': 'broad_market'},
    ],
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
    'healthcare': [
        {'symbol': '512010', 'name': '医药ETF', 'fund_type': 'healthcare'},
        {'symbol': '159938', 'name': '生物医药ETF', 'fund_type': 'healthcare'},
        {'symbol': '512290', 'name': '生物医药', 'fund_type': 'healthcare'},
        {'symbol': '512980', 'name': '传媒ETF', 'fund_type': 'healthcare'},
        {'symbol': '159919', 'name': '医药ETF华泰', 'fund_type': 'healthcare'},
    ],
    'consumer': [
        {'symbol': '159928', 'name': '消费ETF', 'fund_type': 'consumer'},
        {'symbol': '512200', 'name': '消费ETF华宝', 'fund_type': 'consumer'},
        {'symbol': '512170', 'name': '白酒ETF', 'fund_type': 'consumer'},
        {'symbol': '161725', 'name': '白酒ETF招商', 'fund_type': 'consumer'},
        {'symbol': '512600', 'name': '白酒基金', 'fund_type': 'consumer'},
        {'symbol': '159936', 'name': '消费ETF华夏', 'fund_type': 'consumer'},
    ],
    'financial': [
        {'symbol': '512800', 'name': '银行ETF', 'fund_type': 'financial'},
        {'symbol': '512880', 'name': '证券ETF', 'fund_type': 'financial'},
        {'symbol': '159940', 'name': '券商ETF', 'fund_type': 'financial'},
        {'symbol': '512870', 'name': '证券ETF华泰', 'fund_type': 'financial'},
        {'symbol': '512000', 'name': '券商ETF华夏', 'fund_type': 'financial'},
    ],
    'new_energy': [
        {'symbol': '516160', 'name': '新能源ETF', 'fund_type': 'new_energy'},
        {'symbol': '516090', 'name': '光伏ETF', 'fund_type': 'new_energy'},
        {'symbol': '515790', 'name': '光伏ETF华泰', 'fund_type': 'new_energy'},
        {'symbol': '159863', 'name': '光伏ETF华夏', 'fund_type': 'new_energy'},
        {'symbol': '516110', 'name': '新能源车ETF', 'fund_type': 'new_energy'},
    ],
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
    'military': [
        {'symbol': '512660', 'name': '军工ETF', 'fund_type': 'military'},
        {'symbol': '512810', 'name': '军工ETF华宝', 'fund_type': 'military'},
        {'symbol': '515220', 'name': '国防ETF', 'fund_type': 'military'},
        {'symbol': '512670', 'name': '国防ETF华泰', 'fund_type': 'military'},
    ],
    'dividend': [
        {'symbol': '515080', 'name': '红利ETF', 'fund_type': 'dividend'},
        {'symbol': '512890', 'name': '红利低波', 'fund_type': 'dividend'},
        {'symbol': '515180', 'name': '红利ETF华泰', 'fund_type': 'dividend'},
        {'symbol': '159905', 'name': '红利ETF华夏', 'fund_type': 'dividend'},
    ],
    'hongkong': [
        {'symbol': '513600', 'name': '恒指ETF', 'fund_type': 'hongkong'},
        {'symbol': '159920', 'name': '恒生ETF', 'fund_type': 'hongkong'},
        {'symbol': '159760', 'name': '港股ETF', 'fund_type': 'hongkong'},
        {'symbol': '513660', 'name': '恒生科技ETF', 'fund_type': 'hongkong'},
        {'symbol': '159741', 'name': '港股通50', 'fund_type': 'hongkong'},
    ],
    'us_market': [
        {'symbol': '513100', 'name': '纳指ETF', 'fund_type': 'us_market'},
        {'symbol': '513500', 'name': '标普500', 'fund_type': 'us_market'},
        {'symbol': '159941', 'name': '纳指ETF华夏', 'fund_type': 'us_market'},
        {'symbol': '513650', 'name': '纳斯达克ETF', 'fund_type': 'us_market'},
        {'symbol': '513300', 'name': '纳斯达克ETF华泰', 'fund_type': 'us_market'},
    ],
    'global_overseas': [
        {'symbol': '513000', 'name': '日经225ETF', 'fund_type': 'global_overseas'},
        {'symbol': '513800', 'name': '日经ETF', 'fund_type': 'global_overseas'},
        {'symbol': '513520', 'name': '日经225ETF华泰', 'fund_type': 'global_overseas'},
    ],
    'commodity': [
        {'symbol': '518880', 'name': '黄金ETF', 'fund_type': 'commodity'},
        {'symbol': '159934', 'name': '黄金ETF华安', 'fund_type': 'commodity'},
        {'symbol': '159985', 'name': '豆粕ETF', 'fund_type': 'commodity'},
        {'symbol': '159937', 'name': '黄金基金', 'fund_type': 'commodity'},
    ],
}


class ETFInfoCollector(BaseCollector):
    """ETF 基础信息采集器"""

    def __init__(self):
        super().__init__(
            collector_name="etf_info",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.engines = get_active_engines()
        self.table_name = "etf_info"

    def _init_table(self):
        """初始化 ETF 信息表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建表 {self.table_name}...")
            try:
                with engine.begin() as conn:
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
        """获取单个 ETF 的基本信息"""
        return {
            'symbol': symbol,
            'name': name,
            'fund_type': fund_type,
        }

    def save_etf_info(self, etf_list):
        """保存 ETF 信息到数据库"""
        if not etf_list:
            logger.warning("⚠️  ETF 列表为空")
            return

        df = pd.DataFrame(etf_list)

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"DELETE FROM {self.table_name}"))
                    df.to_sql(self.table_name, conn, if_exists='append', index=False)

                logger.info(f"✅ [{mode}] 保存 {len(df)} 条 ETF 信息")
            except Exception as e:
                logger.error(f"❌ [{mode}] 保存 ETF 信息失败: {e}")

    def run(self):
        """执行 ETF 信息采集"""
        self.log_collection_start()
        logger.info("🚀 开始采集 ETF 基础信息...")

        try:
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        self._init_table()

        all_etfs = []

        for fund_type, etf_list in ETF_CONFIG.items():
            logger.info(f"📊 处理 {fund_type} 类 ETF...")

            for etf_config in etf_list:
                symbol = etf_config['symbol']
                name = etf_config['name']
                fund_type_value = etf_config['fund_type']

                etf_info = self.fetch_etf_info(symbol, name, fund_type_value)

                if etf_info:
                    all_etfs.append(etf_info)
                    logger.info(f"  ✅ {symbol} - {name}")

        if all_etfs:
            self.save_etf_info(all_etfs)
            logger.info(f"🎉 ETF 信息采集完成，共 {len(all_etfs)} 只")
            self.log_collection_end(True, f"采集 {len(all_etfs)} 只ETF")
        else:
            logger.error("❌ 未采集到任何 ETF 信息")
            self.log_collection_end(False, "无数据")


if __name__ == "__main__":
    collector = ETFInfoCollector()
    collector.run()
