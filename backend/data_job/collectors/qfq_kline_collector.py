"""
支持前复权的K线采集器

优先使用支持复权的数据源：
1. AKShare - 支持前复权参数
2. EFinance - 支持复权数据
3. pytdx/mootdx + 手动复权计算
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

# 添加backend到路径
backend_dir = Path(__file__).parent.parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

logger = logging.getLogger(__name__)


class QfqKlineCollector:
    """
    前复权K线采集器

    使用AKShare获取前复权数据，这是最简单可靠的方式
    """

    def __init__(self):
        try:
            import akshare as ak
            self.ak = ak
            logger.info("✅ AKShare初始化成功")
        except Exception as e:
            logger.error(f"❌ AKShare初始化失败: {e}")
            self.ak = None

    def get_stock_kline(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        days: int = 30
    ) -> Optional[pd.DataFrame]:
        """
        获取前复权K线数据

        Args:
            symbol: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            days: 获取天数（默认30天）

        Returns:
            DataFrame or None
        """
        if self.ak is None:
            return None

        try:
            # 计算日期范围
            if end_date is None:
                end_date = datetime.now().strftime('%Y%m%d')
            else:
                end_date = end_date.replace('-', '')

            if start_date is None:
                start = datetime.now() - timedelta(days=days)
                start_date = start.strftime('%Y%m%d')
            else:
                start_date = start_date.replace('-', '')

            logger.info(f"获取 {symbol} 前复权数据: {start_date} - {end_date}")

            # 使用AKShare获取前复权数据
            df = self.ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"  # 前复权
            )

            if df is not None and not df.empty:
                # 标准化列名
                column_map = {
                    '日期': 'trade_date',
                    '开盘': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '收盘': 'close',
                    '成交量': 'volume',
                    '成交额': 'amount',
                    '涨跌幅': 'pct_chg',
                    '换手率': 'turnover_rate'
                }

                df = df.rename(columns=column_map)

                # 添加symbol列
                df['symbol'] = symbol

                # 转换日期格式
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

                # 选择需要的列
                columns = [
                    'symbol', 'trade_date', 'open', 'high', 'low', 'close',
                    'volume', 'amount', 'pct_chg', 'turnover_rate'
                ]
                existing_columns = [c for c in columns if c in df.columns]
                df = df[existing_columns]

                # 按日期排序
                df = df.sort_values('trade_date').reset_index(drop=True)

                logger.info(f"✅ 成功获取 {symbol} 前复权数据 {len(df)} 条")
                return df
            else:
                logger.warning(f"⚠️  {symbol} 返回空数据")
                return None

        except Exception as e:
            logger.error(f"❌ 获取 {symbol} 前复权数据失败: {e}")
            return None

    def get_batch_kline(
        self,
        symbols: list,
        start_date: str = None,
        end_date: str = None,
        days: int = 30
    ) -> dict:
        """
        批量获取前复权K线数据

        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            days: 获取天数

        Returns:
            {symbol: DataFrame} 字典
        """
        results = {}
        total = len(symbols)

        for idx, symbol in enumerate(symbols):
            logger.info(f"[{idx + 1}/{total}] 获取 {symbol}...")

            df = self.get_stock_kline(symbol, start_date, end_date, days)
            if df is not None:
                results[symbol] = df

            # 添加延迟避免请求过快
            import time
            time.sleep(0.5)

        logger.info(f"✅ 批量获取完成，成功 {len(results)}/{total}")
        return results


# 便捷函数
def get_qfq_kline(symbol: str, days: int = 30) -> Optional[pd.DataFrame]:
    """
    获取前复权K线数据（便捷函数）

    Args:
        symbol: 股票代码
        days: 获取天数

    Returns:
        DataFrame or None
    """
    collector = QfqKlineCollector()
    return collector.get_stock_kline(symbol, days=days)


if __name__ == "__main__":
    # 测试获取前复权数据
    print("测试前复权K线采集器\n")

    collector = QfqKlineCollector()

    # 测试获取贵州茅台前复权数据
    test_symbol = '600519'
    df = collector.get_stock_kline(test_symbol, days=10)

    if df is not None:
        print(f"✅ 成功获取 {test_symbol} 前复权数据")
        print(f"\n数据列: {list(df.columns)}")
        print("\n最新5条数据:")
        print(df[['trade_date', 'open', 'high', 'low', 'close', 'volume']].tail())
    else:
        print("❌ 获取失败")
