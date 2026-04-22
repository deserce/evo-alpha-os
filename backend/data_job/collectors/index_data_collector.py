"""
指数数据采集器（继承 BaseCollector）
采集上证指数、深证成指、创业板指、A股指数的日线数据

数据源: AkShare
更新频率: 每日收盘后
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
from sqlalchemy import text, inspect

# 添加项目路径
current_dir = Path(__file__).parent
backend_dir = current_dir.parent
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


class IndexDataCollector(BaseCollector):
    """指数数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="index_data",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.table_name = "index_daily"

        # 定义主要指数（使用国证A指替代中证全指）
        self.index_list = [
            {"code": "sh000001", "name": "上证指数", "symbol": "000001"},
            {"code": "sz399001", "name": "深证成指", "symbol": "399001"},
            {"code": "sz399006", "name": "创业板指", "symbol": "399006"},
            {"code": "sz399317", "name": "国证A指", "symbol": "399317"},  # 全市场指数，替代中证全指
            {"code": "sh000300", "name": "沪深300", "symbol": "000300"},   # 大盘蓝筹
            {"code": "sh000688", "name": "科创50", "symbol": "000688"},    # 科创板
            {"code": "sh000905", "name": "中证500", "symbol": "000905"},   # 中盘
            {"code": "sh000852", "name": "中证1000", "symbol": "000852"}   # 小盘
        ]

    def _init_table(self):
        """初始化指数数据表"""
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
        else:
            # 检查是否需要添加 change 字段
            try:
                columns = [col['name'] for col in inspector.get_columns(self.table_name)]
                if 'change' not in columns:
                    logger.info("🔄 添加 change 字段到现有表...")
                    with self.engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {self.table_name} ADD COLUMN change DECIMAL(10,2)"))
                    logger.info("✅ change 字段添加成功")
            except Exception as e:
                logger.warning(f"⚠️ 检查/添加字段时出错: {e}")

    def fetch_data(self):
        """
        获取指数数据

        Returns:
            dict: {index_code: DataFrame}
        """
        import akshare as ak
        import pandas as pd

        # 采集最近三年的数据
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365*3)

        logger.info(f"采集时间范围: {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}")

        index_data = {}

        for idx in self.index_list:
            try:
                logger.info(f"  采集 {idx['name']} ({idx['code']})...")

                # 使用基类的重试机制调用API
                df = self._retry_call(
                    ak.stock_zh_index_daily,
                    symbol=idx["code"]
                )

                if df is not None and not df.empty:
                    # 重置索引
                    df = df.reset_index()

                    # 添加指数代码和名称
                    df['index_code'] = idx['symbol']
                    df['index_name'] = idx['name']

                    # 转换日期格式
                    df['trade_date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

                    # 计算涨跌点数（当前收盘价 - 前一日收盘价）
                    df['change'] = df['close'].diff()

                    # 计算涨跌幅
                    df['pct_chg'] = df['close'].pct_change() * 100

                    # AkShare 的指数接口不提供 amount 字段，需要计算
                    # 成交额 = 成交量 × 收盘价
                    df['amount'] = df['volume'] * df['close']
                    logger.info(f"  💰 {idx['name']} 成交额已计算: {df['amount'].iloc[0]/100000000:.2f} 亿元")

                    # 确保没有空值
                    df['amount'] = df['amount'].fillna(0)

                    # 选择需要的列
                    df = df[['index_code', 'index_name', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'change', 'pct_chg']]

                    # 筛选日期范围
                    start_str = start_date.strftime('%Y-%m-%d')
                    end_str = end_date.strftime('%Y-%m-%d')
                    df = df[(df['trade_date'] >= start_str) & (df['trade_date'] <= end_str)]

                    index_data[idx['symbol']] = df
                    logger.info(f"  ✅ {idx['name']} 采集成功: {len(df)} 条记录")
                else:
                    logger.warning(f"  ⚠️  {idx['name']} 无数据")

            except Exception as e:
                logger.error(f"  ❌ {idx['name']} 采集失败: {e}")

        return index_data

    def process_data(self, index_data):
        """
        处理指数数据

        Args:
            index_data: dict, {index_code: DataFrame}

        Returns:
            pd.DataFrame: 合并后的数据
        """
        import pandas as pd

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
            # 删除旧数据（幂等性保证）
            for index_code in df['index_code'].unique():
                conn.execute(text(f"""
                    DELETE FROM {self.table_name} WHERE index_code = :code
                """), {"code": index_code})

            # 插入新数据
            df.to_sql(self.table_name, conn, if_exists='append', index=False)

        logger.info(f"✅ 数据已保存到数据库: {len(df)} 条记录")

    def run(self):
        """执行指数数据采集"""
        self.log_collection_start()
        logger.info("🚀 启动 [指数数据采集] 任务...")

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
            self.log_collection_end(True, f"采集 {total_indices} 个指数，{total_records} 条记录")
        else:
            logger.error("❌ 无数据获取")
            self.log_collection_end(False, "无数据获取")


if __name__ == "__main__":
    collector = IndexDataCollector()
    collector.run()
