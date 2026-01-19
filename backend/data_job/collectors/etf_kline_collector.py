"""
EvoAlpha OS - ETF K线数据采集器
获取 ETF 基金的日级行情数据
"""

import time
import pandas as pd
import akshare as ak
from sqlalchemy import text
from datetime import datetime, timedelta, date

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


class ETFKlineCollector(BaseCollector):
    """ETF K线数据采集器"""

    def __init__(self):
        super().__init__(
            collector_name="etf_kline",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )
        self.engines = get_active_engines()
        self.table_name = "etf_daily_prices"

    def _init_table(self):
        """初始化 ETF K线表"""
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
                        try:
                            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_etf_kline_symbol ON {self.table_name} (symbol);"))
                        except Exception:
                            pass
                        try:
                            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_etf_kline_date ON {self.table_name} (trade_date);"))
                        except Exception:
                            pass
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

    def get_last_dates(self):
        """获取增量更新进度 - 每个ETF的最后日期"""
        last_dates = {}
        for mode, engine in self.engines:
            try:
                with engine.connect() as conn:
                    query = text(f"SELECT symbol, MAX(trade_date) as last_date FROM {self.table_name} GROUP BY symbol")
                    df = pd.read_sql(query, conn)
                    if not df.empty:
                        last_dates = dict(zip(df['symbol'], pd.to_datetime(df['last_date']).dt.date))
                        logger.info(f"✅ [{mode}] 获取到 {len(last_dates)} 只 ETF 的最后日期")
                        break
            except Exception as e:
                logger.warning(f"⚠️  [{mode}] 获取最后日期失败: {e}")
                continue

        return last_dates

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
                start_date = datetime.now() - timedelta(days=1095)

            start_str = start_date.strftime('%Y%m%d')
            end_str = end_date.strftime('%Y%m%d')

            # 使用基类的重试机制
            df = self._retry_call(
                ak.fund_etf_hist_em,
                symbol=symbol, period="daily", start_date=start_str, end_date=end_str
            )

            if df.empty:
                logger.warning(f"⚠️  ETF {symbol} 无K线数据")
                return None

            # 数据清洗：中文字段映射
            df = df.rename(columns={
                '日期': 'trade_date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '涨跌幅': 'pct_chg'
            })

            # 转换日期
            df['trade_date'] = pd.to_datetime(df['trade_date'])

            # 添加 symbol 列
            df['symbol'] = symbol

            # 选择需要的列
            df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']]

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
                        WHERE symbol = :symbol
                    """), {"symbol": symbol})

                    # 插入新数据（使用 chunksize 避免 SQLite 变量限制）
                    df.to_sql(self.table_name, conn, if_exists='append', index=False,
                             method='multi', chunksize=100)

                logger.debug(f"✅ [{mode}] {symbol} 保存 {len(df)} 条K线")
            except Exception as e:
                logger.error(f"❌ [{mode}] 保存 {symbol} K线失败: {e}")

    def run(self, symbols=None, days=1095):
        """
        执行 ETF K线采集

        Args:
            symbols: ETF 代码列表，如果为None则从数据库获取
            days: 采集天数（默认1095天=3年）
        """
        self.log_collection_start()
        logger.info("🚀 开始采集 ETF K线数据...")

        try:
            # 健康检查
            self._health_check()
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
            self.log_collection_end(False, str(e))
            return

        # 初始化表
        self._init_table()

        # 获取 ETF 列表
        if symbols is None:
            symbols = self.get_etf_list()

        if not symbols:
            logger.error("❌ 未找到 ETF 列表，请先运行 etf_info_collector.py")
            self.log_collection_end(False, "无ETF列表")
            return

        # 获取增量更新进度
        last_dates = self.get_last_dates()
        today = date.today()

        # 采集每个 ETF 的K线
        success_count = 0
        skipped_count = 0
        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[{i}/{len(symbols)}] 采集 {symbol}...")

            try:
                # 增量更新：检查最后日期
                last_date = last_dates.get(symbol)
                start_date = None

                if last_date:
                    # 如果已有数据，检查是否需要更新
                    if last_date >= today:
                        logger.info(f"  ⏭️  {symbol} 数据已是最新 (最后日期: {last_date})")
                        skipped_count += 1
                        continue
                    else:
                        # 从最后日期+1天开始采集
                        start_date = last_date + timedelta(days=1)
                        logger.info(f"  📅 增量更新: {start_date} 至今")
                else:
                    # 首次采集，采集最近3年数据
                    start_date = today - timedelta(days=1095)
                    logger.info(f"  🆕 首次采集: 从 {start_date} 至今")

                df = self.fetch_etf_kline(symbol, start_date=start_date, end_date=datetime.now())
                if df is not None and not df.empty:
                    self.save_etf_kline(symbol, df)
                    success_count += 1
                    logger.info(f"  ✅ {symbol} 采集成功: {len(df)} 条记录")
                elif df is not None and df.empty:
                    logger.info(f"  ℹ️  {symbol} 无新数据")
                    skipped_count += 1

                # 避免请求过快
                time.sleep(self.request_delay)

            except Exception as e:
                logger.error(f"❌ {symbol} 采集失败: {e}")
                continue

        logger.info(f"🎉 ETF K线采集完成，成功 {success_count}/{len(symbols)}，跳过 {skipped_count}")
        self.log_collection_end(True, f"成功 {success_count}/{len(symbols)}，跳过 {skipped_count}")


if __name__ == "__main__":
    collector = ETFKlineCollector()
    collector.run()
