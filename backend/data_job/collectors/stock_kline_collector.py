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
from tqdm import tqdm

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
            request_timeout=60,           # 增加超时时间
            request_delay=0.5,             # 增加延迟到0.5秒，避免频繁请求
            max_retries=5                  # 增加重试次数到5次
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
        """获取股票名单：直接从数据库获取（禁用缓存）"""
        logger.info("📋 正在获取待更新的股票名单...")

        # 直接从数据库查询（禁用缓存以确保获取所有股票）
        # 1. 优先从【基础信息表】读取（包含所有股票）
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT symbol, name FROM stock_info"), conn)
            if not df.empty:
                logger.info(f"✅ 从 stock_info 获取到 {len(df)} 只股票")
                return df.to_dict('records')
        except Exception:
            pass

        # 2. 回退到【板块映射表】读取
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT DISTINCT symbol, name FROM stock_sector_map"), conn)
            if not df.empty:
                logger.warning(f"⚠️ stock_info表为空，从 stock_sector_map 获取到 {len(df)} 只股票")
                return df[df['symbol'].astype(str).str.match(r'^(00|30|60|68)')].to_dict('records')
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
        """批量存入数据库（优化版：增大chunksize提升性能）"""
        if not df_list:
            return
        try:
            final_df = pd.concat(df_list, ignore_index=True)
            with self.engine.begin() as conn:
                # 使用更大的chunksize (5000) 提升批量插入性能
                final_df.to_sql(self.table_name, conn, if_exists='append', index=False,
                               method='multi', chunksize=5000)
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

        # 统计计数器
        success_count = 0
        fail_count = 0
        skip_count = 0

        logger.info(f"📊 准备处理 {total} 只股票...")

        for i, stock in enumerate(tqdm(stock_list, desc="  K线采集进度", unit="只")):
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

                success_count += 1

            except Exception as e:
                fail_count += 1
                error_msg = str(e)
                if "Connection" in error_msg or "aborted" in error_msg:
                    logger.warning(f"  ⚠️  [{i+1}/{total}] {code} {name}: 网络错误")
                    # 网络错误时增加延迟
                    time.sleep(1)
                else:
                    logger.debug(f"  ❌ [{i+1}/{total}] {code} {name}: {error_msg}")
                    time.sleep(0.5)

        if collected_data:
            self._bulk_save_kline(collected_data)

        logger.info(f"\n✅ 个股 K 线同步完成！")
        logger.info(f"📊 统计: 成功 {success_count}, 失败 {fail_count}, 跳过 {skip_count}, 总计 {total}")
        self.log_collection_end(True, f"成功 {success_count}/{total} 只股票")


if __name__ == "__main__":
    collector = StockKlineCollector()
    collector.run()
