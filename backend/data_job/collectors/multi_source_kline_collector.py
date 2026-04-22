"""
多数据源K线采集器基类
支持 pytdx、efinance、akshare 等数据源自动切换
"""

import time
import datetime
import pandas as pd
from sqlalchemy import text, inspect
from datetime import timedelta
from tqdm import tqdm
from typing import Optional, List

# 公共工具导入
from data_job.common import setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

# 多数据源适配器
from data_job.core.multi_source_adapter import MultiSourceAdapter

from app.core.database import get_engine

# 路径初始化
setup_backend_path()

# Logger配置
logger = setup_logger(__name__)


class MultiSourceKlineCollector(BaseCollector):
    """
    多数据源K线采集器基类

    数据源优先级：
    1. pytdx (通达信协议) - 稳定性最高
    2. efinance - 速度快
    3. akshare - 数据源最全

    子类需要实现：
    - get_stock_list(): 获取待采集的股票/板块/ETF列表
    - get_symbol_name(): 获取symbol对应的名称
    - get_table_name(): 返回目标表名
    """

    def __init__(self, collector_name: str = "multi_source_kline"):
        super().__init__(
            collector_name=collector_name,
            request_timeout=60,
            request_delay=0.1,  # pytdx延迟可以很小
            max_retries=3
        )
        self.engine = get_engine()
        self.batch_size = 500

        # 初始化多数据源适配器
        self.data_source = None
        self._init_data_source()

    def _init_data_source(self):
        """初始化多数据源适配器"""
        try:
            self.data_source = MultiSourceAdapter(data_type="kline")
            logger.info(f"✅ {self.collector_name} 多数据源适配器初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  多数据源适配器初始化失败: {e}")
            self.data_source = None

    def _init_table(self):
        """初始化K线表结构"""
        inspector = inspect(self.engine)
        table_name = self.get_table_name()

        if not inspector.has_table(table_name):
            logger.info(f"🛠️ 创建表 {table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {table_name} (
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
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_symbol ON {table_name} (symbol);"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_date ON {table_name} (trade_date);"))

    def get_table_name(self) -> str:
        """返回目标表名，子类需要重写"""
        raise NotImplementedError("子类需要实现 get_table_name()")

    def get_stock_list(self) -> List[dict]:
        """获取待采集的股票/板块/ETF列表，子类需要重写"""
        raise NotImplementedError("子类需要实现 get_stock_list()")

    def get_symbol_column(self) -> str:
        """返回symbol列名"""
        return "symbol"

    def fetch_kline_data(self, symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        使用多数据源获取K线数据

        Args:
            symbol: 股票/板块/ETF代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame or None
        """
        if self.data_source is None:
            logger.warning("多数据源适配器未初始化")
            return None

        try:
            df = self.data_source.get_stock_kline(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust='qfq'
            )

            return df

        except Exception as e:
            logger.warning(f"获取 {symbol} 数据失败: {e}")
            return None

    def get_last_dates(self) -> dict:
        """获取增量更新进度"""
        try:
            table_name = self.get_table_name()
            symbol_col = self.get_symbol_column()

            query = text(f"SELECT {symbol_col}, MAX(trade_date) as last_date FROM {table_name} GROUP BY {symbol_col}")
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
            if df.empty:
                return {}
            return dict(zip(df[symbol_col], pd.to_datetime(df['last_date']).dt.date))
        except:
            return {}

    def _bulk_save_kline(self, df_list: List[pd.DataFrame]):
        """批量存入数据库"""
        if not df_list:
            return
        try:
            final_df = pd.concat(df_list, ignore_index=True)
            table_name = self.get_table_name()
            with self.engine.begin() as conn:
                final_df.to_sql(table_name, conn, if_exists='append', index=False,
                               method='multi', chunksize=5000)
        except Exception as e:
            logger.error(f"❌ 批量写入失败: {e}")

    def run(self):
        """主执行入口"""
        self.log_collection_start()
        logger.info(f"🚀 [K线] 启动 {self.collector_name} 同步...")
        self._init_table()

        try:
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        stock_list = self.get_stock_list()
        if not stock_list:
            logger.error("❌ 未获取到采集列表")
            self.log_collection_end(False, "无采集列表")
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

        logger.info(f"📊 准备处理 {total} 条记录...")

        for i, item in enumerate(tqdm(stock_list, desc=f"  {self.collector_name} 采集进度", unit="条")):
            # 支持字典格式或简单格式
            if isinstance(item, dict):
                code = item.get('symbol') or item.get('code')
                name = item.get('name', '')
            else:
                code = item
                name = ''

            if not code:
                continue

            last_date = existing_records.get(code)
            if last_date:
                if last_date >= today:
                    skip_count += 1
                    continue
                start_date_str = (last_date + timedelta(days=1)).strftime("%Y%m%d")
            else:
                start_date_str = DEFAULT_START_DATE

            end_date_str = today.strftime("%Y%m%d")
            if start_date_str > end_date_str:
                skip_count += 1
                continue

            try:
                df = self.fetch_kline_data(code, start_date_str, end_date_str)

                if df is None or df.empty:
                    fail_count += 1
                    continue

                # 确保有必要的列
                required_cols = ['trade_date', 'open', 'close', 'high', 'low']
                if not all(col in df.columns for col in required_cols):
                    fail_count += 1
                    continue

                # 确保有symbol列
                if 'symbol' not in df.columns:
                    df['symbol'] = code

                # 添加 turnover_rate 列（如果没有）
                if 'turnover_rate' not in df.columns:
                    df['turnover_rate'] = None

                # 选择需要的列
                save_cols = ['symbol', 'trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']
                existing_cols = [c for c in save_cols if c in df.columns]
                save_df = df[existing_cols].copy()

                # 转换日期
                save_df['trade_date'] = pd.to_datetime(save_df['trade_date']).dt.date

                collected_data.append(save_df)

                if len(collected_data) >= BATCH_SIZE:
                    self._bulk_save_kline(collected_data)
                    collected_data = []

                success_count += 1

                # pytdx 不需要延迟，如果是其他数据源则添加小延迟
                if self.data_source and len(self.data_source.sources) > 0:
                    # 如果主数据源不是pytdx，添加延迟
                    if self.data_source.sources[0].name != "pytdx":
                        time.sleep(self.request_delay)

            except Exception as e:
                fail_count += 1
                logger.debug(f"  ❌ [{i+1}/{total}] {code}: {e}")
                time.sleep(0.1)

        # 保存剩余数据
        if collected_data:
            self._bulk_save_kline(collected_data)

        logger.info(f"\n✅ {self.collector_name} 同步完成！")
        logger.info(f"📊 统计: 成功 {success_count}, 失败 {fail_count}, 跳过 {skip_count}, 总计 {total}")
        self.log_collection_end(True, f"成功 {success_count}/{total} 条记录")


class StockKlineCollectorV2(MultiSourceKlineCollector):
    """个股K线数据采集器 (多数据源版本)"""

    def __init__(self):
        super().__init__(collector_name="stock_kline_v2")

    def get_table_name(self) -> str:
        return "stock_daily_prices"

    def get_stock_list(self) -> List[dict]:
        """获取股票名单：直接从数据库获取"""
        logger.info("📋 正在获取待更新的股票名单...")

        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT symbol, name FROM stock_info"), conn)
            if not df.empty:
                logger.info(f"✅ 从 stock_info 获取到 {len(df)} 只股票")
                return df.to_dict('records')
        except Exception as e:
            logger.warning(f"从 stock_info 获取失败: {e}")

        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT DISTINCT symbol, name FROM stock_sector_map"), conn)
            if not df.empty:
                logger.info(f"⚠️ 从 stock_sector_map 获取到 {len(df)} 只股票")
                return df.to_dict('records')
        except Exception as e:
            logger.warning(f"从 stock_sector_map 获取失败: {e}")

        return []


# 测试代码
if __name__ == "__main__":
    # 测试多数据源K线采集器
    collector = StockKlineCollectorV2()

    # 打印数据源状态
    if collector.data_source:
        print("\n可用数据源:")
        for source in collector.data_source.sources:
            print(f"  - {source.name}")

    # 测试获取单只股票数据
    print("\n测试获取 600519 数据...")
    df = collector.fetch_kline_data('600519', '2024-01-01', '2024-03-01')
    if df is not None:
        print(f"✅ 获取到 {len(df)} 条数据")
        print(df.tail())
    else:
        print("❌ 获取数据失败")
