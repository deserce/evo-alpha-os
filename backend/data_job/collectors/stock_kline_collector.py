"""
EvoAlpha OS - 个股K线数据采集器
采集股票的日级行情数据
"""

import time
import datetime
import pandas as pd
import akshare as ak
from sqlalchemy import text, inspect
from datetime import timedelta

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


class StockKlineCollector(BaseCollector):
    """个股K线数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="stock_kline",
            request_timeout=30,
            request_delay=0.01,
            max_retries=3
        )
        self.engine = get_engine()
        self.table_name = "stock_daily_prices"
        self.batch_size = 500

    def _init_table(self):
        """初始化 daily_prices 表结构"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 创建表 {self.table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        symbol VARCHAR(20),
                        trade_date DATE,
                        open FLOAT,
                        close FLOAT,
                        high FLOAT,
                        low FLOAT,
                        volume FLOAT,
                        amount FLOAT,
                        pct_chg FLOAT,
                        turnover_rate FLOAT,
                        PRIMARY KEY (symbol, trade_date)
                    );
                """))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_kline_symbol ON {self.table_name} (symbol);"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_kline_date ON {self.table_name} (trade_date);"))

    def get_stock_list(self):
        """获取股票名单：优先查数据库"""
        logger.info("📋 正在获取待更新的股票名单...")
        # 1. 尝试从【板块映射表】读取
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT DISTINCT symbol, name FROM stock_sector_map"), conn)
            if not df.empty:
                logger.info(f"✅ 从 stock_sector_map 获取到 {len(df)} 只股票")
                return df[df['symbol'].astype(str).str.match(r'^(00|30|60|68)')].to_dict('records')
        except Exception:
            pass

        # 2. 尝试从【基础信息表】读取
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT symbol, name FROM stock_info"), conn)
            if not df.empty:
                logger.info(f"✅ 从 stock_info 获取到 {len(df)} 只股票")
                return df.to_dict('records')
        except Exception:
            pass

        # 3. 最后联网获取
        for i in range(3):
            try:
                df = self._retry_call(ak.stock_zh_a_spot_em, max_retries=2)
                if df is not None:
                    df = df[['代码', '名称']].rename(columns={'代码': 'symbol', '名称': 'name'})
                    return df[df['symbol'].astype(str).str.match(r'^(00|30|60|68)')].to_dict('records')
            except Exception:
                time.sleep(2)
        return []

    def get_last_dates(self):
        """获取增量更新进度"""
        try:
            query = text(f"SELECT symbol, MAX(trade_date) as last_date FROM {self.table_name} GROUP BY symbol")
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
            if df.empty:
                return {}
            return dict(zip(df['symbol'], pd.to_datetime(df['last_date']).dt.date))
        except:
            return {}

    def _bulk_save_kline(self, df_list):
        """批量存入数据库"""
        if not df_list:
            return
        try:
            final_df = pd.concat(df_list, ignore_index=True)
            with self.engine.begin() as conn:
                final_df.to_sql(self.table_name, conn, if_exists='append', index=False, method='multi', chunksize=1000)
        except Exception as e:
            logger.error(f"❌ 批量写入失败: {e}")

    def run(self):
        """主执行入口"""
        self.log_collection_start()
        logger.info("🚀 [K线] 启动个股行情同步...")
        self._init_table()

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        stock_list = self.get_stock_list()
        if not stock_list:
            logger.error("❌ 未获取到股票列表")
            self.log_collection_end(False, "无股票列表")
            return

        existing_records = self.get_last_dates()
        DEFAULT_START_DATE = "20230101"
        today = datetime.date.today()
        total = len(stock_list)

        collected_data = []
        BATCH_SIZE = self.batch_size

        logger.info(f"📊 准备处理 {total} 只股票...")

        for i, stock in enumerate(stock_list):
            code = stock['symbol']
            name = stock['name']

            last_date = existing_records.get(code)
            if last_date:
                if last_date >= today:
                    continue
                start_date_str = (last_date + timedelta(days=1)).strftime("%Y%m%d")
            else:
                start_date_str = DEFAULT_START_DATE

            end_date_str = today.strftime("%Y%m%d")
            if start_date_str > end_date_str:
                continue

            if i % 10 == 0:
                print(f"[{i+1}/{total}] 同步进度: {code} {name} ...", end="\r")

            try:
                # 使用基类的重试机制
                df = self._retry_call(
                    ak.stock_zh_a_hist,
                    symbol=code, period="daily", start_date=start_date_str,
                    end_date=end_date_str, adjust="qfq"
                )

                if df is None or df.empty:
                    continue

                rename_dict = {
                    '日期': 'trade_date', '开盘': 'open', '收盘': 'close',
                    '最高': 'high', '最低': 'low', '成交量': 'volume',
                    '成交额': 'amount', '涨跌幅': 'pct_chg', '换手率': 'turnover_rate'
                }
                df = df.rename(columns=rename_dict)
                df['symbol'] = code

                for col in ['open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']:
                    if col not in df.columns:
                        df[col] = None

                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                save_df = df[['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']]

                collected_data.append(save_df)

                if len(collected_data) >= BATCH_SIZE:
                    self._bulk_save_kline(collected_data)
                    collected_data = []

            except Exception as e:
                logger.debug(f"采集 {code} 失败: {e}")
                time.sleep(0.2)

        if collected_data:
            self._bulk_save_kline(collected_data)

        logger.info(f"\n✅ 个股 K 线同步完成！")
        self.log_collection_end(True, f"处理 {total} 只股票")


if __name__ == "__main__":
    collector = StockKlineCollector()
    collector.run()
