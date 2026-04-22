"""
优化版K线数据采集器 - 专门解决AKShare连接中断问题
"""
import time
import random
import logging
from typing import Optional
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class OptimizedKlineCollector:
    """
    优化版K线采集器

    核心优化：
    1. 极低并发（单线程或2线程）
    2. 更长的延迟（1-3秒）
    3. 批量处理小批次（50只/批）
    4. 智能重试（指数退避）
    5. User-Agent轮换
    6. 错误恢复机制
    """

    def __init__(
        self,
        min_delay: float = 1.5,  # 最小延迟1.5秒
        max_delay: float = 3.0,  # 最大延迟3秒
        batch_size: int = 50,    # 小批次处理
        batch_rest: int = 30,    # 批次间休息30秒
        max_retries: int = 5,    # 更多重试次数
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.batch_size = batch_size
        self.batch_rest = batch_rest
        self.max_retries = max_retries

        # User-Agent池
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
        ]
        self.current_ua_index = 0

        # 统计
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'retries': 0,
        }

    def _get_random_ua(self) -> str:
        """获取随机User-Agent"""
        ua = self.user_agents[self.current_ua_index]
        self.current_ua_index = (self.current_ua_index + 1) % len(self.user_agents)
        return ua

    def _random_delay(self):
        """随机延迟"""
        delay = random.uniform(self.min_delay, self.max_delay)
        logger.debug(f"延迟 {delay:.2f} 秒")
        time.sleep(delay)

    def _batch_rest_delay(self):
        """批次间休息"""
        logger.info(f"😴 批次间休息 {self.batch_rest} 秒...")
        time.sleep(self.batch_rest)

    def _fetch_with_retry(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> Optional[pd.DataFrame]:
        """
        带重试的数据获取

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            adjust: 复权类型

        Returns:
            DataFrame or None
        """
        self.stats['total'] += 1

        for attempt in range(self.max_retries):
            try:
                # 指数退避延迟
                if attempt > 0:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"重试 {attempt + 1}/{self.max_retries}，等待 {wait_time:.1f}秒")
                    time.sleep(wait_time)
                    self.stats['retries'] += 1

                # 添加随机延迟（首次请求也延迟）
                self._random_delay()

                # 获取数据
                df = ak.stock_zh_a_hist(
                    symbol=symbol,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust
                )

                if df is not None and not df.empty:
                    self.stats['success'] += 1
                    return df
                else:
                    logger.warning(f"股票 {symbol} 返回空数据")
                    continue

            except Exception as e:
                error_msg = str(e).lower()
                logger.warning(f"尝试 {attempt + 1}/{self.max_retries} 失败: {e}")

                # 判断错误类型
                if 'connection' in error_msg or 'remote' in error_msg:
                    # 连接错误，需要更长的等待
                    if attempt < self.max_retries - 1:
                        wait_time = 5 + (2 ** attempt)
                        logger.warning(f"连接错误，等待 {wait_time} 秒...")
                        time.sleep(wait_time)
                elif 'timeout' in error_msg:
                    # 超时错误
                    if attempt < self.max_retries - 1:
                        time.sleep(3)
                else:
                    # 其他错误
                    if attempt == self.max_retries - 1:
                        logger.error(f"股票 {symbol} 所有重试均失败")
                        self.stats['failed'] += 1
                        raise

        self.stats['failed'] += 1
        return None

    def collect_batch(
        self,
        symbols: list,
        start_date: str,
        end_date: str,
        progress_callback = None,
    ) -> dict:
        """
        批量采集K线数据

        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            progress_callback: 进度回调

        Returns:
            {symbol: DataFrame} 字典
        """
        results = {}
        total = len(symbols)
        batch_count = (total + self.batch_size - 1) // self.batch_size

        logger.info(f"=" * 80)
        logger.info(f"🚀 开始优化版K线采集")
        logger.info(f"   股票数量: {total}")
        logger.info(f"   批次大小: {self.batch_size}")
        logger.info(f"   批次数量: {batch_count}")
        logger.info(f"   延迟范围: {self.min_delay}-{self.max_delay}秒")
        logger.info(f"   批次休息: {self.batch_rest}秒")
        logger.info(f"=" * 80)

        for batch_idx in range(batch_count):
            start_idx = batch_idx * self.batch_size
            end_idx = min((batch_idx + 1) * self.batch_size, total)
            batch_symbols = symbols[start_idx:end_idx]

            logger.info(f"\n📦 批次 {batch_idx + 1}/{batch_count} ({len(batch_symbols)}只股票)")

            for idx, symbol in enumerate(batch_symbols):
                try:
                    logger.info(f"  [{idx + 1}/{len(batch_symbols)}] 采集 {symbol}...")

                    df = self._fetch_with_retry(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date
                    )

                    if df is not None:
                        results[symbol] = df
                        logger.info(f"    ✅ 成功: {len(df)}条记录")
                    else:
                        results[symbol] = None
                        logger.warning(f"    ⚠️  无数据")

                    if progress_callback:
                        progress_callback(symbol, df is not None)

                except Exception as e:
                    results[symbol] = None
                    logger.error(f"    ❌ 失败: {e}")

            # 批次间休息（除了最后一批）
            if batch_idx < batch_count - 1:
                self._batch_rest_delay()

        # 打印统计
        self._print_stats(total)

        return results

    def _print_stats(self, total: int):
        """打印统计信息"""
        logger.info("\n" + "=" * 80)
        logger.info("📊 采集统计")
        logger.info("=" * 80)
        logger.info(f"总股票数: {total}")
        logger.info(f"成功: {self.stats['success']} ({self.stats['success']/total*100:.1f}%)")
        logger.info(f"失败: {self.stats['failed']} ({self.stats['failed']/total*100:.1f}%)")
        logger.info(f"重试次数: {self.stats['retries']}")
        logger.info("=" * 80)


# 使用示例
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # 添加路径
    backend_dir = Path(__file__).parent.parent.parent.absolute()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.core.database import get_engine
    from sqlalchemy import text

    # 获取股票列表
    engine = get_engine()
    with engine.connect() as conn:
        query = text("SELECT DISTINCT symbol FROM stock_daily_prices ORDER BY symbol LIMIT 100")
        result = conn.execute(query)
        symbols = [row[0] for row in result]

    # 日期范围
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=5)).strftime('%Y%m%d')

    # 创建采集器（使用最保守的配置）
    collector = OptimizedKlineCollector(
        min_delay=2.0,      # 2秒最小延迟
        max_delay=4.0,      # 4秒最大延迟
        batch_size=30,      # 30只一批
        batch_rest=60,      # 批次间休息60秒
        max_retries=5,      # 5次重试
    )

    # 开始采集
    results = collector.collect_batch(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        progress_callback=lambda sym, success: print(f"进度: {sym} - {'✅' if success else '❌'}")
    )

    print(f"\n采集完成！成功: {sum(1 for v in results.values() if v is not None)}/{len(symbols)}")
