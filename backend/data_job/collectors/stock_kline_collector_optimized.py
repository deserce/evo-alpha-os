"""
EvoAlpha OS - 个股K线数据采集器（可配置优化版）
支持动态调整并发数、延迟、批次等参数，平衡效率和防封

使用示例：
    # 默认配置（安全模式）
    collector = StockKlineCollectorOptimized()

    # 激进模式（有代理时使用）
    collector = StockKlineCollectorOptimized(
        max_workers=5,
        min_delay=0.1,
        max_delay=0.2,
        batch_size=500
    )

    # 保守模式（无代理）
    collector = StockKlineCollectorOptimized(
        max_workers=2,
        min_delay=0.2,
        max_delay=0.4,
        batch_size=200
    )
"""

import time
import random
import datetime
import pandas as pd
import akshare as ak
from sqlalchemy import text, inspect
from datetime import timedelta
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

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


class StockKlineCollectorOptimized(BaseCollector):
    """
    个股K线数据采集器（可配置优化版）

    参数说明:
        max_workers: 并发线程数（建议1-5）
            - 1: 串行，最安全
            - 2-3: 保守并发，适合无代理
            - 3-5: 激进并发，适合有代理

        min_delay: 最小延迟（秒）（建议0.1-0.3）
        max_delay: 最大延迟（秒）（建议0.2-0.5）
            - 实际延迟会在 min_delay 和 max_delay 之间随机
            - 随机性有助于防止被封

        batch_size: 批次大小（建议200-1000）
            - 达到此数量后，保存数据并休息

        batch_rest_time: 批次间休息时间（秒）（建议3-10）
            - 每完成一批采集后休息的时间

        skip_uptodate: 是否跳过已更新的股票（默认True）
            - True: 只采集需要更新的股票
            - False: 强制全量更新
    """

    def __init__(
        self,
        max_workers=3,
        min_delay=0.15,
        max_delay=0.35,
        batch_size=500,
        batch_rest_time=5,
        skip_uptodate=True
    ):
        super().__init__(
            collector_name="stock_kline_optimized",
            request_timeout=30,
            request_delay=0,  # 不使用基类的延迟，自己控制
            max_retries=3
        )

        self.engine = get_engine()
        self.table_name = "stock_daily_prices"

        # 可配置参数
        self.max_workers = max_workers
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.batch_size = batch_size
        self.batch_rest_time = batch_rest_time
        self.skip_uptodate = skip_uptodate

        # 线程安全的数据收集
        self.data_lock = Lock()
        self.collected_data = []
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0

        # 性能统计
        self.request_times = []

        logger.info(f"⚡ 采集器配置: 并发={max_workers}, 延迟={min_delay}-{max_delay}s, "
                   f"批次={batch_size}, 批次休息={batch_rest_time}s")

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
        """获取股票名单"""
        logger.info("📋 正在获取待更新的股票名单...")
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text("SELECT symbol, name FROM stock_info"), conn)
            if not df.empty:
                logger.info(f"✅ 从 stock_info 获取到 {len(df)} 只股票")
                return df.to_dict('records')
        except Exception as e:
            logger.error(f"❌ 获取股票列表失败: {e}")
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
                final_df.to_sql(self.table_name, conn, if_exists='append', index=False,
                               method='multi', chunksize=10000)
            logger.debug(f"💾 批量保存 {len(final_df)} 条记录")
        except Exception as e:
            logger.error(f"❌ 批量写入失败: {e}")

    def _random_delay(self):
        """随机延迟（防封）"""
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)
        return delay

    def _collect_single_stock(self, stock, existing_records, today):
        """
        采集单只股票的K线数据（线程安全）

        Returns:
            dict: {status: 'success'|'skip'|'fail', data: DataFrame}
        """
        code = stock['symbol']
        name = stock['name']

        last_date = existing_records.get(code)
        if last_date and self.skip_uptodate:
            if last_date >= today:
                return {'status': 'skip', 'code': code, 'name': name}
            start_date_str = (last_date + timedelta(days=1)).strftime("%Y%m%d")
        else:
            start_date_str = "20230101"

        end_date_str = today.strftime("%Y%m%d")
        if start_date_str > end_date_str:
            return {'status': 'skip', 'code': code, 'name': name}

        # 随机延迟（防封）
        self._random_delay()

        try:
            start = time.time()
            df = self._retry_call(
                ak.stock_zh_a_hist,
                symbol=code, period="daily", start_date=start_date_str,
                end_date=end_date_str, adjust="qfq"
            )
            elapsed = time.time() - start

            # 记录请求时间
            with self.data_lock:
                self.request_times.append(elapsed)
                if len(self.request_times) > 100:
                    self.request_times.pop(0)

            if df is None or df.empty:
                return {'status': 'fail', 'code': code, 'name': name, 'error': '无数据'}

            # 数据处理
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
            save_df = df[['symbol', 'trade_date', 'open', 'close', 'high', 'low',
                          'volume', 'amount', 'pct_chg', 'turnover_rate']]

            return {'status': 'success', 'code': code, 'name': name, 'data': save_df, 'elapsed': elapsed}

        except Exception as e:
            return {'status': 'fail', 'code': code, 'name': name, 'error': str(e)}

    def run(self):
        """主执行入口（可配置优化版）"""
        self.log_collection_start()
        logger.info("🚀 [K线优化] 启动个股行情同步...")
        logger.info(f"⚙️  配置: 并发={self.max_workers}, 延迟={self.min_delay}-{self.max_delay}s, "
                   f"批次={self.batch_size}, 批次休息={self.batch_rest_time}s")
        self._init_table()

        try:
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
        today = datetime.date.today()
        total = len(stock_list)

        logger.info(f"📊 准备处理 {total} 只股票...")

        # 分批处理
        batch_count = (total + self.batch_size - 1) // self.batch_size
        logger.info(f"📦 分{batch_count}批处理，每批{self.batch_size}只")

        all_success = 0
        all_fail = 0
        all_skip = 0

        for batch_idx in range(batch_count):
            start_idx = batch_idx * self.batch_size
            end_idx = min(start_idx + self.batch_size, total)
            batch_stocks = stock_list[start_idx:end_idx]

            logger.info(f"\n{'='*60}")
            logger.info(f"📦 第{batch_idx+1}/{batch_count}批: 索引 {start_idx+1}-{end_idx}")
            logger.info(f"{'='*60}")

            # 重置数据缓存
            self.collected_data = []
            self.success_count = 0
            self.fail_count = 0
            self.skip_count = 0

            # 并发采集本批次
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_stock = {
                    executor.submit(self._collect_single_stock, stock, existing_records, today): stock
                    for stock in batch_stocks
                }

                with tqdm(total=len(batch_stocks), desc=f"  批次{batch_idx+1}进度", unit="只") as pbar:
                    for future in as_completed(future_to_stock):
                        stock = future_to_stock[future]
                        try:
                            result = future.result()

                            if result['status'] == 'success':
                                with self.data_lock:
                                    self.collected_data.append(result['data'])
                                    self.success_count += 1

                                    # 批量保存
                                    if len(self.collected_data) >= 100:
                                        self._bulk_save_kline(self.collected_data)
                                        self.collected_data = []

                            elif result['status'] == 'skip':
                                self.skip_count += 1

                            else:  # fail
                                self.fail_count += 1
                                if 'error' in result:
                                    logger.debug(f"  ❌ {result['code']} {result['name']}: {result['error'][:50]}")

                        except Exception as e:
                            logger.error(f"❌ 处理 {stock['symbol']} 时发生异常: {e}")
                            self.fail_count += 1

                        pbar.update(1)

            # 保存本批次剩余数据
            if self.collected_data:
                self._bulk_save_kline(self.collected_data)

            # 统计本批次
            all_success += self.success_count
            all_fail += self.fail_count
            all_skip += self.skip_count

            logger.info(f"✅ 第{batch_idx+1}批完成: 成功{self.success_count}, 失败{self.fail_count}, 跳过{self.skip_count}")

            # 显示性能统计
            if self.request_times:
                avg_time = sum(self.request_times) / len(self.request_times)
                logger.info(f"⚡ 平均请求时间: {avg_time:.2f}秒")

            # 批次间休息（防封）
            if batch_idx < batch_count - 1:  # 不是最后一批
                logger.info(f"😴 批次间休息 {self.batch_rest_time}秒（防封）...")
                time.sleep(self.batch_rest_time)

        logger.info(f"\n{'='*60}")
        logger.info(f"✅ 全部采集完成！")
        logger.info(f"📊 总计: 成功 {all_success}, 失败 {all_fail}, 跳过 {all_skip}, 总计 {total}")
        logger.info(f"{'='*60}")
        self.log_collection_end(True, f"成功 {all_success}/{total} 只股票")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='优化版K线采集器')
    parser.add_argument('--workers', type=int, default=3, help='并发线程数（1-5）')
    parser.add_argument('--min-delay', type=float, default=0.15, help='最小延迟（秒）')
    parser.add_argument('--max-delay', type=float, default=0.35, help='最大延迟（秒）')
    parser.add_argument('--batch-size', type=int, default=500, help='批次大小')
    parser.add_argument('--batch-rest', type=int, default=5, help='批次间休息时间（秒）')
    parser.add_argument('--mode', choices=['safe', 'normal', 'aggressive'], default='normal',
                       help='预设模式（safe=保守, normal=正常, aggressive=激进）')

    args = parser.parse_args()

    # 预设模式
    if args.mode == 'safe':
        config = {'max_workers': 2, 'min_delay': 0.2, 'max_delay': 0.4, 'batch_size': 200, 'batch_rest_time': 8}
        logger.info("🛡️ 使用安全模式")
    elif args.mode == 'aggressive':
        config = {'max_workers': 5, 'min_delay': 0.1, 'max_delay': 0.2, 'batch_size': 1000, 'batch_rest_time': 3}
        logger.info("⚡ 使用激进模式（建议有代理）")
    else:  # normal
        config = {'max_workers': 3, 'min_delay': 0.15, 'max_delay': 0.35, 'batch_size': 500, 'batch_rest_time': 5}
        logger.info("⚖️  使用正常模式")

    # 命令行参数覆盖预设
    if args.workers:
        config['max_workers'] = args.workers
    if args.min_delay:
        config['min_delay'] = args.min_delay
    if args.max_delay:
        config['max_delay'] = args.max_delay
    if args.batch_size:
        config['batch_size'] = args.batch_size
    if args.batch_rest:
        config['batch_rest_time'] = args.batch_rest

    collector = StockKlineCollectorOptimized(**config)
    collector.run()
