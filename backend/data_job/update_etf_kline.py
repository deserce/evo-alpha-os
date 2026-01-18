"""
EvoAlpha OS - ETF K线数据采集
获取 ETF 基金的日级行情数据
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


class ETFKlineManager:
    def __init__(self):
        self.engines = get_active_engines()
        self.table_name = "etf_daily_prices"

    def _init_table(self):
        """初始化 ETF K线表"""
        for mode, engine in self.engines:
            logger.info(f"🛠️  [{mode}] 创建表 {self.table_name}...")
            try:
                with engine.begin() as conn:
                    # SQLite 检查表是否存在
                    inspector_result = conn.execute(text(f"""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='{self.table_name}'
                    """))
                    exists = inspector_result.fetchone() is not None

                    if not exists:
                        conn.execute(text(f"""
                            CREATE TABLE {self.table_name} (
                                symbol VARCHAR(20),
                                trade_date DATE,
                                open FLOAT,
                                high FLOAT,
                                low FLOAT,
                                close FLOAT,
                                volume FLOAT,
                                amount FLOAT,
                                pct_chg FLOAT,
                                PRIMARY KEY (symbol, trade_date)
                            );
                        """))
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_etf_kline_symbol ON {self.table_name} (symbol);"))
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_etf_kline_date ON {self.table_name} (trade_date);"))
                        logger.info(f"✅ [{mode}] 表 {self.table_name} 创建成功")
                    else:
                        logger.info(f"ℹ️  [{mode}] 表 {self.table_name} 已存在")
            except Exception as e:
                logger.error(f"❌ [{mode}] 创建表失败: {e}")

    def get_etf_list(self):
        """从数据库获取 ETF 列表"""
        etf_list = []
        for mode, engine in self.engines:
            try:
                with engine.connect() as conn:
                    df = pd.read_sql(text("SELECT symbol FROM etf_info"), conn)
                    if not df.empty:
                        etf_list = df['symbol'].tolist()
                        logger.info(f"✅ [{mode}] 从 etf_info 获取到 {len(etf_list)} 只 ETF")
                        break
            except Exception as e:
                logger.warning(f"⚠️  [{mode}] 获取 ETF 列表失败: {e}")
                continue

        return etf_list

    def get_last_date(self, symbol):
        """获取某个 ETF 的最后更新日期"""
        for mode, engine in self.engines:
            try:
                with engine.connect() as conn:
                    result = conn.execute(text(f"""
                        SELECT MAX(trade_date) as last_date
                        FROM {self.table_name}
                        WHERE symbol = '{symbol}'
                    """))
                    last_date = result.fetchone()[0]
                    if last_date:
                        return pd.to_datetime(last_date)
            except Exception as e:
                logger.debug(f"获取 {symbol} 最后日期失败: {e}")
                continue

        # 默认从2年前开始
        return datetime.now() - timedelta(days=730)

    def fetch_etf_kline(self, symbol, start_date=None, end_date=None):
        """
        获取单个 ETF 的K线数据

        Args:
            symbol: ETF 代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame: K线数据
        """
        try:
            if end_date is None:
                end_date = datetime.now()

            if start_date is None:
                start_date = datetime.now() - timedelta(days=730)  # 默认2年

            start_str = start_date.strftime('%Y%m%d')
            end_str = end_date.strftime('%Y%m%d')

            # 使用 AkShare 获取 ETF K线
            df = ak.fund_etf_hist_sina(symbol=symbol)

            if df.empty:
                logger.warning(f"⚠️  ETF {symbol} 无K线数据")
                return None

            # 数据清洗
            df = df.reset_index()
            df['date'] = pd.to_datetime(df['date'])
            df = df.rename(columns={
                'date': 'trade_date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume',
            })

            # 计算涨跌幅
            df['pct_chg'] = df['close'].pct_change() * 100

            # 筛选日期范围
            df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]

            # 添加 symbol 列
            df['symbol'] = symbol

            # 选择需要的列
            df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'pct_chg']]

            logger.info(f"  ✅ {symbol}: {len(df)} 条K线数据")
            return df

        except Exception as e:
            logger.error(f"❌ 获取 ETF {symbol} K线失败: {e}")
            return None

    def save_etf_kline(self, symbol, df):
        """
        保存 ETF K线数据

        Args:
            symbol: ETF 代码
            df: K线数据
        """
        if df is None or df.empty:
            return

        for mode, engine in self.engines:
            try:
                with engine.begin() as conn:
                    # 删除旧数据
                    conn.execute(text(f"""
                        DELETE FROM {self.table_name}
                        WHERE symbol = '{symbol}'
                        AND trade_date >= '{df['trade_date'].min()}'
                        AND trade_date <= '{df['trade_date'].max()}'
                    """))

                    # 插入新数据
                    df.to_sql(self.table_name, conn, if_exists='append', index=False)

                logger.debug(f"✅ [{mode}] {symbol} 保存 {len(df)} 条K线")
            except Exception as e:
                logger.error(f"❌ [{mode}] 保存 {symbol} K线失败: {e}")

    def run(self, symbols=None, days=730):
        """
        执行 ETF K线采集

        Args:
            symbols: ETF 代码列表，如果为None则从数据库获取
            days: 采集天数（默认730天=2年）
        """
        logger.info("🚀 开始采集 ETF K线数据...")

        # 初始化表
        self._init_table()

        # 获取 ETF 列表
        if symbols is None:
            symbols = self.get_etf_list()

        if not symbols:
            logger.error("❌ 未找到 ETF 列表，请先运行 update_etf_info.py")
            return

        # 采集每个 ETF 的K线
        success_count = 0
        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[{i}/{len(symbols)}] 采集 {symbol}...")

            try:
                df = self.fetch_etf_kline(symbol)
                if df is not None:
                    self.save_etf_kline(symbol, df)
                    success_count += 1

                # 避免请求过快
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"❌ {symbol} 采集失败: {e}")
                continue

        logger.info(f"🎉 ETF K线采集完成，成功 {success_count}/{len(symbols)}")


if __name__ == "__main__":
    manager = ETFKlineManager()
    manager.run()
