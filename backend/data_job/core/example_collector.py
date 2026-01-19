"""
示例：使用增强基类的数据采集器
展示如何使用 BaseCollector 和 BatchCollector
"""

import sys
import os
import pandas as pd
import akshare as ak
from datetime import datetime, timedelta

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# ================= 导入基类 =================
from data_job.base_collector import BaseCollector, BatchCollector
from sqlalchemy import text


# ================= 示例1: 使用 BaseCollector（简单采集）=================
class StockListCollector(BaseCollector):
    """
    股票列表采集器
    演示简单的单次数据采集
    """

    def __init__(self):
        super().__init__(
            collector_name="stock_list",
            request_timeout=30,
            request_delay=0.5,
            max_retries=3
        )

    def run(self):
        """执行采集"""
        self.log_collection_start()

        try:
            # 使用带重试的API调用
            self.logger.info("正在获取股票列表...")
            df = self._retry_call(
                lambda: ak.stock_zh_a_spot_em()
            )

            if df is None or df.empty:
                self.log_collection_end(False, "未获取到数据")
                return False

            # 数据清洗
            df = df.rename(columns={'代码': 'symbol', '名称': 'name'})
            df['symbol'] = df['symbol'].astype(str).str.zfill(6)
            df['updated_at'] = datetime.now()

            # 保存数据（自动去重）
            count = self.save_with_deduplication(
                df=df,
                table_name="stock_info",
                key_columns=["symbol"]
            )

            # 更新最后更新时间
            self.update_progress(last_update=datetime.now().isoformat())

            self.log_collection_end(True, f"采集了 {count} 条数据")
            return True

        except Exception as e:
            self.log_collection_end(False, str(e))
            return False


# ================= 示例2: 使用 BatchCollector（批量采集）=================
class StockKlineCollector(BatchCollector):
    """
    股票K线采集器
    演示批量采集和断点续传
    """

    def __init__(self, days: int = 365):
        super().__init__(
            collector_name="stock_kline",
            batch_size=100  # 每批处理100只股票
        )
        self.days = days  # 采集最近N天的数据

    def get_item_list(self) -> list:
        """获取要采集的股票列表"""
        try:
            # 从数据库获取股票列表
            df = pd.read_sql(
                "SELECT symbol FROM stock_info ORDER BY symbol",
                self.engine
            )
            symbols = df['symbol'].tolist()
            self.logger.info(f"找到 {len(symbols)} 只股票")
            return symbols
        except Exception as e:
            self.logger.error(f"获取股票列表失败: {e}")
            return []

    def process_item(self, item) -> pd.DataFrame:
        """处理单只股票的K线数据"""
        symbol = item

        # 计算日期范围（支持增量更新）
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.days)

        # 如果有历史数据，只采集增量
        try:
            last_date_query = text("""
                SELECT MAX(date) as last_date
                FROM stock_daily_prices
                WHERE symbol = :symbol
            """)
            result = self.engine.execute(last_date_query, {"symbol": symbol}).fetchone()

            if result and result[0]:
                # 从最后一天的下一天开始
                last_date = pd.to_datetime(result[0])
                start_date = last_date + timedelta(days=1)
                self.logger.debug(f"{symbol} 增量更新: {start_date.date()} 到 {end_date.date()}")
        except:
            pass

        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        # 使用带重试的API调用
        df = self._retry_call(
            lambda: ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_str,
                end_date=end_str,
                adjust=""
            )
        )

        if df is None or df.empty:
            return pd.DataFrame()

        # 数据清洗
        df = df.rename(columns={
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg'
        })

        df['symbol'] = symbol
        df['date'] = pd.to_datetime(df['date'])

        return df

    def save_item_data(self, item, df: pd.DataFrame):
        """保存单只股票的K线数据"""
        if df.empty:
            return

        # 保存数据（自动去重）
        count = self.save_with_deduplication(
            df=df,
            table_name="stock_daily_prices",
            key_columns=["symbol", "date"],
            date_column="date"
        )

        self.logger.debug(f"{item} 保存了 {count} 条K线数据")


# ================= 示例3: 带降级策略的采集器 =================
class ETFKlineCollectorWithFallback(BaseCollector):
    """
    ETF K线采集器（带降级策略）
    演示主备接口切换
    """

    def __init__(self):
        super().__init__(
            collector_name="etf_kline",
            request_timeout=30,
            request_delay=0.5,
            max_retries=2  # 减少重试次数，快速切换到备用接口
        )

    def _fetch_from_em(self, symbol: str):
        """主接口: 东方财富"""
        try:
            start_date = (datetime.now() - timedelta(days=730)).strftime('%Y%m%d')
            end_date = datetime.now().strftime('%Y%m%d')

            df = ak.fund_etf_hist_em(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date
            )

            # 数据清洗
            df = df.rename(columns={
                '日期': 'date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '涨跌幅': 'pct_chg'
            })

            df['symbol'] = symbol
            df['date'] = pd.to_datetime(df['date'])

            return df

        except Exception as e:
            self.logger.warning(f"东方财富接口失败: {e}")
            raise

    def _fetch_from_sina(self, symbol: str):
        """备用接口: 新浪"""
        try:
            df = ak.fund_etf_hist_sina(symbol=f"sh{symbol}")

            if df.empty:
                return pd.DataFrame()

            df['symbol'] = symbol
            df['date'] = pd.to_datetime(df['date'])

            return df

        except Exception as e:
            self.logger.warning(f"新浪接口失败: {e}")
            raise

    def run(self):
        """执行采集（带降级策略）"""
        self.log_collection_start()

        try:
            # 获取ETF列表
            df_etf = pd.read_sql("SELECT symbol FROM etf_info", self.engine)
            symbols = df_etf['symbol'].tolist()

            success_count = 0

            for symbol in symbols:
                self.logger.info(f"采集 {symbol}...")

                try:
                    # 使用降级策略：主接口失败尝试备用接口
                    df = self._retry_with_fallback(
                        primary_func=lambda s=symbol: self._fetch_from_em(s),
                        fallback_func=lambda s=symbol: self._fetch_from_sina(s)
                    )

                    if df is not None and not df.empty:
                        # 保存数据
                        self.save_with_deduplication(
                            df=df,
                            table_name="etf_daily_prices",
                            key_columns=["symbol", "date"],
                            date_column="date"
                        )
                        success_count += 1

                except Exception as e:
                    self.logger.error(f"{symbol} 采集失败: {e}")

            self.log_collection_end(True, f"成功: {success_count}/{len(symbols)}")
            return True

        except Exception as e:
            self.log_collection_end(False, str(e))
            return False


# ================= 使用示例 ==================
if __name__ == "__main__":
    import logging

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 60)
    print("数据采集示例")
    print("=" * 60)

    # 示例1: 简单采集
    print("\n【示例1】采集股票列表...")
    collector1 = StockListCollector()
    collector1.run()

    # 示例2: 批量采集（支持断点续传）
    print("\n【示例2】批量采集K线（支持断点续传）...")
    collector2 = StockKlineCollector(days=90)
    # resume=True 从断点继续，resume=False 从头开始
    collector2.run(resume=True)

    # 示例3: 带降级策略
    print("\n【示例3】ETF K线采集（主备接口切换）...")
    collector3 = ETFKlineCollectorWithFallback()
    collector3.run()

    # 查看统计信息
    print("\n【统计信息】")
    for collector in [collector1, collector2, collector3]:
        stats = collector.get_collection_statistics()
        print(f"\n{stats['collector_name']}:")
        print(f"  总采集: {stats['collection_count']} 条")
        print(f"  最后更新: {stats['last_update']}")
        print(f"  失败项: {stats['failed_items_count']} 个")
