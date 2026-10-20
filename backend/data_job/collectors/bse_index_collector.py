"""
北交所板块指数数据采集器

采集北证50等北交所板块指数的日线数据

数据源: 多源架构 (东方财富 > AkShare > mootdx)
更新频率: 每日收盘后
"""
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from sqlalchemy import text, inspect
import pandas as pd

# 添加项目路径
current_dir = Path(__file__).parent
backend_dir = current_dir.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# 公共工具导入
from data_job.common import setup_network_emergency_kit, setup_backend_path, setup_logger

# 基类导入
from data_job.core.base_collector import BaseCollector

# 路径和网络初始化
setup_backend_path()
setup_network_emergency_kit()

# Logger配置
logger = setup_logger(__name__)


class BSEIndexCollector(BaseCollector):
    """北交所板块指数采集器"""

    def __init__(self):
        super().__init__(
            collector_name="bse_index",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.table_name = "index_daily"

        # 北交所主要指数列表
        self.index_list = [
            {"code": "899560", "name": "北证50", "market": "bse"},
        ]

    def _init_table(self):
        """初始化指数数据表（如果不存在）"""
        inspector = inspect(self.engine)
        if not inspector.has_table(self.table_name):
            logger.info(f"🛠️ 初始化指数表 {self.table_name}...")
            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {self.table_name} (
                        index_code VARCHAR(10) NOT NULL,
                        index_name VARCHAR(20) NOT NULL,
                        trade_date DATE NOT NULL,
                        open DECIMAL(10,2),
                        high DECIMAL(10,2),
                        low DECIMAL(10,2),
                        close DECIMAL(10,2),
                        volume BIGINT,
                        amount BIGINT,
                        change DECIMAL(10,2),
                        pct_chg DECIMAL(10,2),
                        PRIMARY KEY (index_code, trade_date)
                    );
                """))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_date ON {self.table_name}(trade_date)"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_code ON {self.table_name}(index_code)"))

            logger.info(f"✅ {self.table_name} 表创建成功")

    def _fetch_from_eastmoney(self, index_code: str) -> pd.DataFrame | None:
        """
        从东方财富获取北交所指数数据

        Args:
            index_code: 指数代码，如 '899560'

        Returns:
            DataFrame or None
        """
        try:
            import requests

            url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"

            # 计算3年前的日期
            end_date = datetime.now()
            start_date = end_date - timedelta(days=365*3)

            params = {
                "secid": f"1.{index_code}",  # 1=上海市场（北交所指数归类在此）
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "klt": "101",  # 日K
                "fqt": "0",    # 不复权
                "beg": start_date.strftime("%Y%m%d"),
                "end": end_date.strftime("%Y%m%d"),
            }

            logger.info(f"  从东方财富获取 {index_code}...")

            response = self._retry_call(
                requests.get,
                url,
                params=params,
                timeout=30
            )

            if response and response.json():
                data = response.json()

                if data.get("data") and data["data"].get("klines"):
                    klines = data["data"]["klines"]

                    # 解析数据
                    records = []
                    for kline in klines:
                        parts = kline.split(",")
                        if len(parts) >= 7:
                            records.append({
                                "trade_date": parts[0],
                                "open": float(parts[1]),
                                "close": float(parts[2]),
                                "high": float(parts[3]),
                                "low": float(parts[4]),
                                "volume": int(float(parts[5])),
                                "amount": float(parts[6]),
                            })

                    df = pd.DataFrame(records)
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')

                    # 计算涨跌点数和涨跌幅
                    df = df.sort_values('trade_date').reset_index(drop=True)
                    df['change'] = df['close'].diff()
                    df['pct_chg'] = df['close'].pct_change() * 100

                    logger.info(f"  ✅ 从东方财富获取到 {len(df)} 条数据")
                    return df

            logger.warning(f"  ⚠️ 东方财富返回空数据")
            return None

        except Exception as e:
            logger.warning(f"  ⚠️ 从东方财富获取失败: {e}")
            return None

    def _fetch_from_akshare(self, index_code: str) -> pd.DataFrame | None:
        """
        从AkShare获取北交所指数数据

        Args:
            index_code: 指数代码

        Returns:
            DataFrame or None
        """
        try:
            import akshare as ak

            logger.info(f"  从AkShare获取 {index_code}...")

            # AkShare 的指数历史接口
            end_date = datetime.now()
            start_date = end_date - timedelta(days=365*3)

            df = self._retry_call(
                ak.index_zh_a_hist,
                symbol=index_code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d")
            )

            if df is not None and not df.empty:
                # 重置索引
                df = df.reset_index()

                # 重命名列
                df = df.rename(columns={
                    'date': 'trade_date',
                    '开盘': 'open',
                    '收盘': 'close',
                    '最高': 'high',
                    '最低': 'low',
                    '成交量': 'volume',
                })

                # 转换日期格式
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')

                # 计算成交额（如果没有的话）
                if 'amount' not in df.columns:
                    df['amount'] = df['volume'] * df['close']

                # 计算涨跌点数和涨跌幅
                df = df.sort_values('trade_date').reset_index(drop=True)
                df['change'] = df['close'].diff()
                df['pct_chg'] = df['close'].pct_change() * 100

                # 选择需要的列
                df = df[['trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'change', 'pct_chg']]

                logger.info(f"  ✅ 从AkShare获取到 {len(df)} 条数据")
                return df

            logger.warning(f"  ⚠️ AkShare返回空数据")
            return None

        except Exception as e:
            logger.warning(f"  ⚠️ 从AkShare获取失败: {e}")
            return None

    def fetch_data(self):
        """
        获取北交所指数数据（多源架构）

        Returns:
            dict: {index_code: DataFrame}
        """
        index_data = {}

        for idx in self.index_list:
            index_code = idx['code']
            index_name = idx['name']

            logger.info(f"采集 {index_name} ({index_code})...")

            df = None

            # 数据源优先级: 东方财富 > AkShare
            df = self._fetch_from_eastmoney(index_code)

            if df is None or df.empty:
                df = self._fetch_from_akshare(index_code)

            if df is not None and not df.empty:
                df['index_code'] = index_code
                df['index_name'] = index_name
                index_data[index_code] = df
                logger.info(f"  ✅ {index_name} 采集成功: {len(df)} 条记录")
            else:
                logger.error(f"  ❌ {index_name} 所有数据源均失败")

        return index_data

    def process_data(self, index_data):
        """
        处理指数数据

        Args:
            index_data: dict, {index_code: DataFrame}

        Returns:
            pd.DataFrame: 合并后的数据
        """
        if not index_data:
            return pd.DataFrame()

        # 合并所有指数数据
        all_data = []
        for index_code, df in index_data.items():
            all_data.append(df)

        if all_data:
            result_df = pd.concat(all_data, ignore_index=True)
            logger.info(f"✅ 数据处理完成: {len(result_df)} 条记录，{len(index_data)} 个指数")
            return result_df
        else:
            return pd.DataFrame()

    def save_data(self, df):
        """
        保存指数数据

        Args:
            df: 要保存的数据
        """
        if df.empty:
            return

        with self.engine.begin() as conn:
            # 对于每个指数，删除该指数的旧数据再插入新数据
            for index_code in df['index_code'].unique():
                conn.execute(text(f"""
                    DELETE FROM {self.table_name}
                    WHERE index_code = :code
                      AND trade_date >= (SELECT MIN(trade_date) FROM (
                          SELECT trade_date FROM {self.table_name} WHERE index_code = :code
                      ) AS tmp)
                """), {"code": index_code})

            # 插入或更新数据
            for _, row in df.iterrows():
                conn.execute(text(f"""
                    INSERT OR REPLACE INTO {self.table_name}
                    (index_code, index_name, trade_date, open, high, low, close, volume, amount, change, pct_chg)
                    VALUES (:code, :name, :date, :open, :high, :low, :close, :vol, :amt, :change, :pct)
                """), {
                    "code": row['index_code'],
                    "name": row['index_name'],
                    "date": row['trade_date'],
                    "open": row['open'],
                    "high": row['high'],
                    "low": row['low'],
                    "close": row['close'],
                    "vol": int(row['volume']) if pd.notna(row['volume']) else 0,
                    "amt": float(row['amount']) if pd.notna(row['amount']) else 0,
                    "change": float(row['change']) if pd.notna(row['change']) else None,
                    "pct": float(row['pct_chg']) if pd.notna(row['pct_chg']) else None,
                })

        logger.info(f"✅ 数据已保存到数据库: {len(df)} 条记录")

    def run(self):
        """执行北交所指数数据采集"""
        self.log_collection_start()
        logger.info("🚀 启动 [北交所板块指数采集] 任务...")

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
        index_data = self.fetch_data()
        if index_data:
            # 处理数据
            df_clean = self.process_data(index_data)
            # 保存数据
            self.save_data(df_clean)

            total_records = len(df_clean)
            total_indices = len(index_data)
            self.log_collection_end(True, f"采集 {total_indices} 个北交所指数，{total_records} 条记录")
        else:
            logger.error("❌ 无数据获取")
            self.log_collection_end(False, "无数据获取")


if __name__ == "__main__":
    collector = BSEIndexCollector()
    collector.run()
