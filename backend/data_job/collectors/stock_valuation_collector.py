"""
EvoAlpha OS - 股票估值数据采集器
采集全市场股票的实时估值数据（PE、PB、市值等）
"""

import pandas as pd
import akshare as ak
from datetime import date
from sqlalchemy import text, inspect
from tqdm import tqdm

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class StockValuationCollector(BaseCollector):
    """股票估值数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="stock_valuation",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.table_name = "stock_valuation_daily"

    def _init_table(self):
        """初始化估值数据表"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 初始化估值表 {self.table_name}...")
            with self.engine.begin() as conn:
                # 创建表
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        code VARCHAR(20),
                        name VARCHAR(50),
                        trade_date DATE,
                        price FLOAT,
                        pe_ttm FLOAT,            -- 市盈率(动态)
                        pb FLOAT,                -- 市净率
                        total_mv FLOAT,          -- 总市值
                        circ_mv FLOAT,           -- 流通市值
                        pct_chg FLOAT,           -- 涨跌幅
                        turnover FLOAT,          -- 换手率
                        volume_ratio FLOAT,      -- 量比
                        PRIMARY KEY (code, trade_date)
                    );
                """))
                # 创建索引
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_val_code ON {self.table_name} (code);"))
                except Exception:
                    pass
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_val_date ON {self.table_name} (trade_date);"))
                except Exception:
                    pass

    def fetch_data(self):
        """
        获取全市场当天实时估值数据

        Returns:
            pd.DataFrame: 估值数据
        """
        # 使用基类的重试机制调用API
        return self._retry_call(ak.stock_zh_a_spot_em)

    def process_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        处理估值数据（清洗、转换）

        Args:
            df: 原始数据

        Returns:
            pd.DataFrame: 处理后的数据
        """
        if df is None or df.empty:
            return df

        # 1. 字段映射
        rename_map = {
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "pct_chg", "总市值": "total_mv", "流通市值": "circ_mv",
            "市盈率-动态": "pe_ttm", "市净率": "pb",
            "换手率": "turnover", "量比": "volume_ratio"
        }
        df = df.rename(columns=rename_map)

        # 2. 筛选列
        cols = list(rename_map.values())
        # 容错处理：确保列都存在
        for c in cols:
            if c not in df.columns:
                df[c] = 0
        df = df[cols]

        # 3. 清洗与类型
        df['code'] = df['code'].astype(str).str.zfill(6)
        # 增加日期列
        df['trade_date'] = date.today()

        # 数值清洗
        numeric_cols = ["price", "pe_ttm", "pb", "total_mv", "circ_mv", "pct_chg", "turnover", "volume_ratio"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        return df

    def save_data(self, df: pd.DataFrame):
        """
        保存估值数据

        Args:
            df: 要保存的数据
        """
        if df.empty:
            return

        current_date = date.today()
        date_str = current_date.strftime('%Y-%m-%d')

        logger.info(f"💾 正在存储 {date_str} 的估值数据 ({len(df)}条)...")

        with self.engine.begin() as conn:
            # 幂等性删除：如果今天已经跑过一次，先删掉今天的，避免主键冲突
            conn.execute(text(f"DELETE FROM {self.table_name} WHERE trade_date = :dt"), {"dt": date_str})

            # 追加插入
            df.to_sql(self.table_name, conn, if_exists='append', index=False)

            # 同步更新 stock_info 表的估值字段
            logger.info("🔄 同步更新 stock_info 表的估值字段...")
            update_count = 0
            for _, row in tqdm(df.iterrows(), total=len(df), desc="  更新估值信息", unit="只"):
                # total_mv 单位是元，需要转换为亿元
                market_cap = row['total_mv'] / 100000000 if pd.notna(row['total_mv']) else None

                conn.execute(text("""
                    UPDATE stock_info
                    SET pe = :pe, pb = :pb, market_cap = :market_cap
                    WHERE symbol = :symbol
                """), {
                    "pe": row['pe_ttm'],
                    "pb": row['pb'],
                    "market_cap": market_cap,
                    "symbol": row['code']
                })
                update_count += 1

            logger.info(f"✅ {date_str} 估值数据入库成功！")
            logger.info(f"✅ 已同步更新 {update_count} 只股票的估值信息到 stock_info 表")

    def run(self):
        """执行估值数据采集"""
        self.log_collection_start()
        logger.info("🚀 启动 [估值数据存盘] 任务...")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        # 初始化表
        self._init_table()

        # 获取数据
        df_raw = self.fetch_data()
        if df_raw is not None and not df_raw.empty:
            df_clean = self.process_data(df_raw)
            self.save_data(df_clean)
            self.log_collection_end(True, f"采集 {len(df_clean)} 条数据")
        else:
            logger.error("❌ 无数据获取")
            self.log_collection_end(False, "无数据获取")


if __name__ == "__main__":
    collector = StockValuationCollector()
    collector.run()
