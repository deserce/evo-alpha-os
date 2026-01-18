"""
EvoAlpha OS - 连板数据采集
采集涨停板数据和连板统计
"""

import sys
import os
import time
import logging
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime, timedelta

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


class LimitBoardManager:
    def __init__(self):
        self.engines = get_active_engines()
        self.boards_table = "limit_board_trading"
        self.stats_table = "consecutive_boards_stats"

    def _init_tables(self):
        """初始化连板数据表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建连板数据表...")
            try:
                with engine.begin() as conn:
                    # 涨停板交易表
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.boards_table} (
                            trade_date DATE,
                            symbol VARCHAR(20),
                            name VARCHAR(100),
                            limit_time TIME,
                            limit_price FLOAT,
                            turnover_rate FLOAT,
                            amount FLOAT,
                            is_new_high BOOLEAN,
                            boards INT,
                            PRIMARY KEY (trade_date, symbol)
                        );
                    """))

                    # 连板统计表
                    conn.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {self.stats_table} (
                            trade_date DATE,
                            boards INT,
                            stock_count INT,
                            PRIMARY KEY (trade_date, boards)
                        );
                    """))

                    # 创建索引
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_boards_date ON {self.boards_table} (trade_date);"))
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_boards_symbol ON {self.boards_table} (symbol);"))
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_stats_date ON {self.stats_table} (trade_date);"))

                    logger.success(f"✅ [{mode}] 连板表创建成功")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建连板表失败: {e}")

    def fetch_limit_boards(self, date_str=None):
        """
        获取涨停板数据

        Args:
            date_str: 日期字符串（YYYYMMDD）

        Returns:
            DataFrame: 涨停板数据
        """
        try:
            if date_str is None:
                date_str = datetime.now().strftime('%Y%m%d')

            # 使用 AkShare 获取涨停板
            df = ak.stock_zt_pool_em(date=date_str)

            if df.empty:
                logger.warning(f"⚠️  {date_str} 无涨停板数据")
                return None

            # 数据清洗
            df = df.rename(columns={
                '代码': 'symbol',
                '名称': 'name',
                '涨速': 'pct_chg',
                '换手率': 'turnover_rate',
                '成交额': 'amount',
            })

            # 提取涨停时间（从"reason"列或使用默认值）
            if 'reason' in df.columns:
                df['limit_time'] = df['reason'].str.extract(r'(\d{2}:\d{2})').fillna('15:00:00')
            else:
                df['limit_time'] = '15:00:00'

            # 添加日期
            df['trade_date'] = pd.to_datetime(date_str)

            # 转换数据类型
            df['amount'] = df['amount'].astype(float)
            df['turnover_rate'] = df['turnover_rate'].astype(float)

            # 判断是否新高（这里简化处理，实际需要历史数据）
            df['is_new_high'] = False

            # 连板数（需要历史数据计算，先设为0）
            df['boards'] = 0

            # 选择需要的列
            df = df[['trade_date', 'symbol', 'name', 'limit_time', 'limit_price',
                      'turnover_rate', 'amount', 'is_new_high', 'boards']]

            logger.info(f"  ✅ 涨停板: {len(df)} 只")
            return df

        except Exception as e:
            logger.error(f"❌ 获取涨停板数据失败: {e}")
            return None

    def calculate_consecutive_boards(self, symbol, end_date):
        """
        计算连板数（简化版）

        Args:
            symbol: 股票代码
            end_date: 结束日期

        Returns:
            int: 连板数
        """
        try:
            # 这里需要查询历史数据，简化处理
            # 实际应该查询过去N天的涨停板数据
            # 现在先返回0，后续可以优化
            return 0
        except:
            return 0

    def save_limit_boards(self, df):
        """
        保存涨停板数据

        Args:
            df: 涨停板数据
        """
        if df is None or df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 删除旧数据
                    conn.execute(text(f"""
                        DELETE FROM {self.boards_table}
                        WHERE trade_date = '{df['trade_date'].iloc[0]}'
                    """))

                    # 插入新数据
                    df.to_sql(self.boards_table, conn, if_exists='append', index=False)

                    logger.info(f"✅ [{mode}] 保存 {len(df)} 条涨停板数据")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存涨停板数据失败: {e}")

    def calculate_stats(self, df):
        """
        计算连板统计

        Args:
            df: 涨停板数据

        Returns:
            DataFrame: 连板统计
        """
        if df is None or df.empty:
            return None

        try:
            # 统计每个连板高度有多少只股票
            stats = df['boards'].value_counts().reset_index()
            stats.columns = ['boards', 'stock_count']

            # 添加日期
            stats['trade_date'] = df['trade_date'].iloc[0]

            return stats

        except Exception as e:
            logger.error(f"❌ 计算连板统计失败: {e}")
            return None

    def save_stats(self, stats_df):
        """
        保存连板统计

        Args:
            stats_df: 连板统计数据
        """
        if stats_df is None or stats_df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 删除旧数据
                    conn.execute(text(f"""
                        DELETE FROM {self.stats_table}
                        WHERE trade_date = '{stats_df['trade_date'].iloc[0]}'
                    """))

                    # 插入新数据
                    stats_df.to_sql(self.stats_table, conn, if_exists='append', index=False)

                    logger.info(f"✅ [{mode}] 保存 {len(stats_df)} 条连板统计")

            except Exception as e:
                logger.error(f"❌ [{mode}] 保存连板统计失败: {e}")

    def run(self, days=5):
        """
        执行连板数据采集

        Args:
            days: 采集最近几天的数据
        """
        logger.info("🚀 开始采集连板数据...")

        # 初始化表
        self._init_tables()

        # 采集最近几天的数据
        for i in range(days):
            date = datetime.now() - timedelta(days=i)
            date_str = date.strftime('%Y%m%d')

            logger.info(f"📊 采集 {date_str} 的涨停板数据...")

            try:
                # 获取涨停板数据
                df = self.fetch_limit_boards(date_str)

                if df is not None:
                    # 保存涨停板数据
                    self.save_limit_boards(df)

                    # 计算连板统计
                    stats = self.calculate_stats(df)
                    if stats is not None:
                        self.save_stats(stats)

                # 避免请求过快
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"❌ {date_str} 采集失败: {e}")
                continue

        logger.success("🎉 连板数据采集完成")


if __name__ == "__main__":
    manager = LimitBoardManager()
    manager.run(days=5)
