"""
EvoAlpha OS - 北向资金持股数据采集器
采集每只股票的北向资金历史持仓数据
"""

import time
import pandas as pd
import akshare as ak
from sqlalchemy import text, inspect
from datetime import date

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


class NorthboundHoldingsCollector(BaseCollector):
    """北向资金持股数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="northbound_holdings",
            request_timeout=30,
            request_delay=1.0,  # API限制，延迟1秒
            max_retries=3
        )
        self.engine = get_engine()
        self.table_name = "stock_northbound_holdings"

    def _init_table(self):
        """初始化北向资金持股表"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 创建北向资金持股表 {self.table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        symbol VARCHAR(20),
                        name VARCHAR(100),
                        hold_date DATE,
                        close_price FLOAT,
                        pct_chg FLOAT,
                        hold_amount FLOAT,
                        hold_value FLOAT,
                        hold_ratio FLOAT,
                        change_amount FLOAT,
                        change_value FLOAT,
                        change_market_value FLOAT,
                        PRIMARY KEY (symbol, hold_date)
                    );
                """))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_date ON {self.table_name} (hold_date);"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_symbol ON {self.table_name} (symbol);"))
            logger.info(f"✅ 表 {self.table_name} 创建成功")
        else:
            logger.info(f"✅ 表 {self.table_name} 已存在")

    def fetch_stock_list(self):
        """从数据库获取股票列表"""
        logger.info("📋 获取股票列表...")

        with self.engine.connect() as conn:
            df = pd.read_sql("SELECT symbol, name FROM stock_info ORDER BY symbol", conn)

        logger.info(f"✅ 获取到 {len(df)} 只股票")
        return df

    def fetch_stock_holdings(self, symbol):
        """
        获取单只股票的北向资金持仓历史

        Args:
            symbol: 股票代码

        Returns:
            pd.DataFrame: 持仓数据
        """
        try:
            # 使用重试机制调用API
            df = self._retry_call(ak.stock_hsgt_individual_em, symbol=symbol)

            if df is None or df.empty:
                return None

            # 添加股票代码列
            df['symbol'] = symbol

            return df

        except Exception as e:
            logger.warning(f"⚠️  获取 {symbol} 数据失败: {e}")
            return None

    def process_data(self, df: pd.DataFrame, stock_name: str) -> pd.DataFrame:
        """
        处理持仓数据

        Args:
            df: 原始数据
            stock_name: 股票名称

        Returns:
            pd.DataFrame: 处理后的数据
        """
        if df is None or df.empty:
            return pd.DataFrame()

        # 字段映射
        df_processed = pd.DataFrame()
        df_processed['symbol'] = df['symbol']
        df_processed['name'] = stock_name
        df_processed['hold_date'] = pd.to_datetime(df['持股日期']).dt.date
        df_processed['close_price'] = df['当日收盘价']
        df_processed['pct_chg'] = df['当日涨跌幅']
        df_processed['hold_amount'] = df['持股数量']
        df_processed['hold_value'] = df['持股市值']
        df_processed['hold_ratio'] = df['持股数量占A股百分比']
        df_processed['change_amount'] = df['今日增持股数']
        df_processed['change_value'] = df['今日增持资金']
        df_processed['change_market_value'] = df['今日持股市值变化']

        # 处理空值
        df_processed = df_processed.fillna({
            'change_amount': 0,
            'change_value': 0,
            'change_market_value': 0
        })

        return df_processed

    def save_data(self, df: pd.DataFrame):
        """
        保存持仓数据

        Args:
            df: 要保存的数据
        """
        if df is None or df.empty:
            return

        try:
            # 使用去重保存
            self.save_with_deduplication(
                df=df,
                table_name=self.table_name,
                key_columns=['symbol', 'hold_date'],
                date_column='hold_date'
            )
            logger.info(f"💾 保存 {len(df)} 条记录到 {self.table_name}")

        except Exception as e:
            logger.error(f"❌ 保存数据失败: {e}")

    def run(self, collect_all_stocks=True):
        """
        执行采集

        Args:
            collect_all_stocks: 是否采集所有股票（True），还是只测试采集少量股票（False）
        """
        self.log_collection_start()
        logger.info("🚀 北向资金持股数据采集任务启动")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        # 初始化表
        self._init_table()

        # 获取股票列表
        stock_list = self.fetch_stock_list()

        if stock_list.empty:
            logger.error("❌ 股票列表为空")
            self.log_collection_end(False, "股票列表为空")
            return

        # 如果是测试模式，只采集前10只股票
        if not collect_all_stocks:
            logger.info("⚠️  测试模式：只采集前10只股票")
            stock_list = stock_list.head(10)

        total_stocks = len(stock_list)
        success_count = 0
        fail_count = 0
        total_records = 0

        logger.info(f"📊 开始采集 {total_stocks} 只股票的北向资金数据...")

        # 遍历每只股票
        for idx, row in stock_list.iterrows():
            symbol = row['symbol']
            name = row['name']

            logger.info(f"[{idx+1}/{total_stocks}] 正在处理 {symbol} ({name})...")

            try:
                # 获取数据
                df_raw = self.fetch_stock_holdings(symbol)

                if df_raw is not None and not df_raw.empty:
                    # 处理数据
                    df_processed = self.process_data(df_raw, name)

                    if not df_processed.empty:
                        # 保存数据
                        self.save_data(df_processed)
                        total_records += len(df_processed)
                        success_count += 1

                        # 显示最后一天的数据
                        last_date = df_processed['hold_date'].max()
                        last_record = df_processed[df_processed['hold_date'] == last_date].iloc[0]
                        logger.info(f"  ✅ 成功: {len(df_processed)} 条记录, 最新日期 {last_date}, "
                                  f"持股 {last_record['hold_amount']/10000:.2f} 万股")
                    else:
                        logger.warning(f"  ⚠️  处理后数据为空")
                        fail_count += 1
                else:
                    logger.warning(f"  ⚠️  无数据")
                    fail_count += 1

                # 延迟
                if idx < total_stocks - 1:
                    time.sleep(self.request_delay)

            except Exception as e:
                logger.error(f"  ❌ 失败: {e}")
                fail_count += 1
                continue

        # 输出统计
        logger.info(f"\n{'=' * 80}")
        logger.info(f"📊 采集完成统计:")
        logger.info(f"  总股票数: {total_stocks}")
        logger.info(f"  成功: {success_count} ({success_count/total_stocks*100:.1f}%)")
        logger.info(f"  失败: {fail_count} ({fail_count/total_stocks*100:.1f}%)")
        logger.info(f"  总记录数: {total_records:,} 条")
        logger.info(f"{'=' * 80}\n")

        self.log_collection_end(True, f"采集完成: {success_count}/{total_stocks} 成功, {total_records} 条记录")


if __name__ == "__main__":
    import sys

    # 检查命令行参数
    test_mode = '--test' in sys.argv

    collector = NorthboundHoldingsCollector()

    if test_mode:
        logger.info("🧪 测试模式: 只采集前10只股票")
        collector.run(collect_all_stocks=False)
    else:
        logger.info("🚀 生产模式: 采集所有股票")
        collector.run(collect_all_stocks=True)
