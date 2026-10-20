"""
支持前复权的多源K线采集器

数据策略：
1. 优先使用pytdx/mootdx获取原始K线（稳定）
2. 获取除权数据计算复权因子
3. 对原始K线应用前复权处理

优点：
- 数据源稳定（pytdx使用通达信协议，不易被封）
- 支持前复权/后复权/不复权
- 自动处理除权除息
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List
import pandas as pd

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from data_job.core.base_collector import BaseCollector
from data_job.core.multi_source_adapter import MultiSourceAdapter
from data_job.core.adjust_calculator import AdjustFactorCalculator

logger = logging.getLogger(__name__)


class AdjustableKlineCollector(BaseCollector):
    """
    支持复权的K线采集器

    支持的复权类型：
    - qfq: 前复权（默认）
    - hfq: 后复权
    - none: 不复权

    数据源优先级：
    1. pytdx (通达信协议，最稳定)
    2. mootdx (pytdx增强版)
    3. EFinance
    4. AKShare
    """

    def __init__(
        self,
        adjust_type: str = 'qfq',
        batch_size: int = 100,
        request_delay: float = 0.5
    ):
        """
        初始化采集器

        Args:
            adjust_type: 复权类型 ('qfq'-前复权, 'hfq'-后复权, 'none'-不复权)
            batch_size: 批量处理大小
            request_delay: 请求延迟（秒）
        """
        super().__init__(
            collector_name=f"adjustable_kline_{adjust_type}",
            request_delay=request_delay
        )

        self.adjust_type = adjust_type
        self.batch_size = batch_size

        # 初始化多源适配器
        self.source_adapter = MultiSourceAdapter(data_type='kline')

        # 初始化复权计算器
        self.adjust_calculator = AdjustFactorCalculator()

        logger.info(f"✅ 支持复权的K线采集器初始化完成，复权类型: {adjust_type}")

    def collect_single_stock(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        days: int = 400
    ) -> Optional[pd.DataFrame]:
        """
        采集单只股票K线数据

        Args:
            symbol: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            days: 获取天数（当start_date未指定时）

        Returns:
            DataFrame or None
        """
        try:
            # 计算日期范围
            if end_date is None:
                end_date = datetime.now().strftime('%Y-%m-%d')

            if start_date is None:
                start = datetime.now() - timedelta(days=days)
                start_date = start.strftime('%Y-%m-%d')

            logger.info(f"采集 {symbol} K线数据: {start_date} 至 {end_date}")

            # 1. 使用多源适配器获取原始K线数据
            df = self.source_adapter.get_stock_kline(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust='none'  # 获取原始数据
            )

            if df is None or df.empty:
                logger.warning(f"{symbol} 无法获取K线数据")
                return None

            # 2. 应用复权
            if self.adjust_type == 'qfq':
                df = self.adjust_calculator.adjust_kline_forward(df, symbol)
            elif self.adjust_type == 'hfq':
                df = self.adjust_calculator.adjust_kline_backward(df, symbol)
            # else: 不复权，直接使用原始数据

            if df is not None and not df.empty:
                logger.info(f"✅ {symbol} 成功获取 {len(df)} 条K线数据（{self.adjust_type}）")
                return df
            else:
                return None

        except Exception as e:
            logger.error(f"采集 {symbol} K线数据失败: {e}")
            return None

    def collect_batch(
        self,
        symbols: List[str],
        start_date: str = None,
        end_date: str = None,
        days: int = 400,
        save_to_db: bool = True,
        table_name: str = None
    ) -> dict:
        """
        批量采集K线数据

        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            days: 获取天数
            save_to_db: 是否保存到数据库
            table_name: 数据库表名

        Returns:
            {symbol: DataFrame} 字典
        """
        if table_name is None:
            table_name = f"stock_kline_{self.adjust_type}"

        results = {}
        total = len(symbols)
        success_count = 0
        fail_count = 0

        logger.info(f"=" * 80)
        logger.info(f"🚀 开始批量采集K线数据（{self.adjust_type}）")
        logger.info(f"   股票数量: {total}")
        logger.info(f"   日期范围: {start_date or f'最近{days}天'} 至 {end_date or '今天'}")
        logger.info(f"=" * 80)

        for idx, symbol in enumerate(symbols):
            try:
                logger.info(f"[{idx + 1}/{total}] 采集 {symbol}...")

                df = self.collect_single_stock(symbol, start_date, end_date, days)

                if df is not None and not df.empty:
                    results[symbol] = df

                    # 保存到数据库
                    if save_to_db:
                        self._save_to_db(df, table_name)

                    success_count += 1
                else:
                    fail_count += 1

                # 延迟避免请求过快
                if idx < total - 1:  # 最后一个不需要延迟
                    import time
                    time.sleep(self.request_delay)

            except Exception as e:
                logger.error(f"{symbol} 采集失败: {e}")
                fail_count += 1
                continue

        logger.info(f"=" * 80)
        logger.info(f"✅ 批量采集完成")
        logger.info(f"   成功: {success_count}/{total}")
        logger.info(f"   失败: {fail_count}/{total}")
        logger.info(f"=" * 80)

        return results

    def run(self, test_mode: bool = False):
        """
        运行采集器

        Args:
            test_mode: 测试模式，只采集少量数据
        """
        logger.info("🚀 支持复权的K线采集器开始运行...")

        try:
            # 获取股票列表
            from data_job.core.multi_source_adapter import TdxAdapter

            tdx = TdxAdapter()
            stock_list = tdx.get_stock_list()

            if stock_list is None or stock_list.empty:
                logger.error("无法获取股票列表")
                return

            logger.info(f"获取到 {len(stock_list)} 只股票")

            # 测试模式：只采集前10只
            if test_mode:
                stock_list = stock_list.head(10)
                logger.warning(f"⚠️  测试模式：只采集前 {len(stock_list)} 只股票")

            symbols = stock_list['code'].tolist()

            # 批量采集
            table_name = f"stock_kline_{self.adjust_type}"
            results = self.collect_batch(
                symbols=symbols,
                days=400,  # 采集最近400天
                save_to_db=True,
                table_name=table_name
            )

            logger.info(f"✅ 采集完成，共 {len(results)} 只股票")

        except Exception as e:
            logger.error(f"采集器运行失败: {e}")
            raise


# 便捷函数
def collect_qfq_kline(
    symbols: List[str],
    start_date: str = None,
    end_date: str = None,
    days: int = 400
) -> dict:
    """
    采集前复权K线数据（便捷函数）

    Args:
        symbols: 股票代码列表
        start_date: 开始日期
        end_date: 结束日期
        days: 获取天数

    Returns:
        {symbol: DataFrame} 字典
    """
    collector = AdjustableKlineCollector(adjust_type='qfq')
    return collector.collect_batch(symbols, start_date, end_date, days)


def collect_hfq_kline(
    symbols: List[str],
    start_date: str = None,
    end_date: str = None,
    days: int = 400
) -> dict:
    """
    采集后复权K线数据（便捷函数）

    Args:
        symbols: 股票代码列表
        start_date: 开始日期
        end_date: 结束日期
        days: 获取天数

    Returns:
        {symbol: DataFrame} 字典
    """
    collector = AdjustableKlineCollector(adjust_type='hfq')
    return collector.collect_batch(symbols, start_date, end_date, days)


# 使用示例
if __name__ == "__main__":
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 80)
    print("测试支持前复权的K线采集器")
    print("=" * 80)

    # 创建采集器
    collector = AdjustableKlineCollector(adjust_type='qfq')

    # 测试单只股票
    test_symbol = '600519'
    print(f"\n测试采集 {test_symbol} 前复权数据...")

    df = collector.collect_single_stock(
        symbol=test_symbol,
        days=90  # 最近90天
    )

    if df is not None:
        print(f"✅ 成功获取 {len(df)} 条数据")
        print("\n最新5条数据:")
        print(df[['trade_date', 'open', 'high', 'low', 'close', 'volume']].tail())
    else:
        print("❌ 采集失败")
