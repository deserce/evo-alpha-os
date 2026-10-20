"""
复权因子计算模块

支持前复权和后复权计算，基于除权除息数据。

复权原理：
1. 前复权：以当前股价为基准，历史价格按除权比例调整
2. 后复权：以最早股价为基准，后续价格按除权比例调整

复权因子计算公式：
- 除权除息日复权因子 = (除权前收盘价 + 每股派息) / (除权前收盘价 - 每股送转股)
- 前复权：从最新日期往前逐日应用复权因子
- 后复权：从最早日期往后逐日累乘复权因子
"""

import logging
from typing import Optional, Dict, List
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class AdjustFactorCalculator:
    """
    复权因子计算器

    支持前复权（qfq）和后复权（hfq）
    """

    def __init__(self):
        self.dividend_cache = {}  # 缓存除权数据
        self.factor_cache = {}    # 缓存复权因子

    def get_dividend_data(
        self,
        symbol: str,
        use_cache: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        获取除权除息数据

        Args:
            symbol: 股票代码
            use_cache: 是否使用缓存

        Returns:
            DataFrame with columns:
                - ex_date: 除权除息日
                - bonus_ratio: 送股比例（每10股）
                - transfer_ratio: 转增比例（每10股）
                - dividend_ratio: 派息比例（每10股）
        """
        # 检查缓存
        if use_cache and symbol in self.dividend_cache:
            return self.dividend_cache[symbol]

        try:
            import akshare as ak

            # 获取分红送股数据
            df = ak.stock_history_dividend_detail(symbol=symbol)

            if df is None or df.empty:
                logger.debug(f"{symbol} 无除权数据")
                return None

            # 筛选已实施的记录
            df = df[df['进度'] == '实施'].copy()

            if df.empty:
                logger.debug(f"{symbol} 无已实施的除权记录")
                return None

            # 提取需要的列
            result = pd.DataFrame({
                'ex_date': pd.to_datetime(df['除权除息日']).dt.date,
                'bonus_ratio': df['送股'].fillna(0),      # 每10股送股数
                'transfer_ratio': df['转增'].fillna(0),   # 每10股转增数
                'dividend_ratio': df['派息'].fillna(0)    # 每10股派息数（元）
            })

            # 过滤掉没有除权的记录
            result = result[
                (result['bonus_ratio'] > 0) |
                (result['transfer_ratio'] > 0) |
                (result['dividend_ratio'] > 0)
            ].copy()

            if result.empty:
                return None

            # 按日期排序
            result = result.sort_values('ex_date').reset_index(drop=True)

            # 缓存
            if use_cache:
                self.dividend_cache[symbol] = result

            logger.info(f"✅ 获取 {symbol} 除权数据 {len(result)} 条")
            return result

        except Exception as e:
            logger.warning(f"获取 {symbol} 除权数据失败: {e}")
            return None

    def calculate_adjust_factors(
        self,
        symbol: str,
        kline_data: Optional[pd.DataFrame] = None,
        adjust_type: str = 'qfq'
    ) -> Dict[str, float]:
        """
        计算复权因子

        Args:
            symbol: 股票代码
            kline_data: K线数据（用于获取价格信息）
            adjust_type: 复权类型 ('qfq'-前复权, 'hfq'-后复权)

        Returns:
            {ex_date: factor} 字典，表示除权除息日及其对应的复权因子
        """
        # 获取除权数据
        dividend_data = self.get_dividend_data(symbol)
        if dividend_data is None or dividend_data.empty:
            return {}

        # 如果没有K线数据，只计算简单的复权因子
        if kline_data is None or kline_data.empty:
            return self._calculate_simple_factors(dividend_data, adjust_type)

        # 使用K线数据计算精确的复权因子
        return self._calculate_precise_factors(
            symbol, dividend_data, kline_data, adjust_type
        )

    def _calculate_simple_factors(
        self,
        dividend_data: pd.DataFrame,
        adjust_type: str
    ) -> Dict[str, float]:
        """
        计算简单复权因子（不依赖K线数据）

        公式：
        - 复权因子 = 1 / (1 + 送股比例 + 转增比例) + 派息比例 / 10

        注意：这是近似值，精确值需要除权前收盘价
        """
        factors = {}

        if adjust_type == 'hfq':
            # 后复权：从最早日期开始累乘
            cumulative_factor = 1.0
            for _, row in dividend_data.iterrows():
                ex_date = row['ex_date']
                bonus = row['bonus_ratio'] / 10.0      # 每股送转数
                transfer = row['transfer_ratio'] / 10.0  # 每股转增数
                dividend = row['dividend_ratio'] / 10.0  # 每股派息数

                # 计算本次除权的复权因子
                if bonus > 0 or transfer > 0:
                    # 送股/转增：股数增加，价格被稀释
                    factor = 1 + bonus + transfer
                    cumulative_factor *= factor
                else:
                    # 只有派息：价格减少
                    # 派息的影响相对较小，这里简化处理
                    factor = 1.0
                    cumulative_factor *= factor

                factors[ex_date] = cumulative_factor
        else:
            # 前复权：从最新日期往前计算
            # 需要反转顺序
            dividend_data_reversed = dividend_data.iloc[::-1]
            cumulative_factor = 1.0

            for _, row in dividend_data_reversed.iterrows():
                ex_date = row['ex_date']
                bonus = row['bonus_ratio'] / 10.0
                transfer = row['transfer_ratio'] / 10.0

                if bonus > 0 or transfer > 0:
                    factor = 1 + bonus + transfer
                    cumulative_factor *= factor

                factors[ex_date] = cumulative_factor

        return factors

    def _calculate_precise_factors(
        self,
        symbol: str,
        dividend_data: pd.DataFrame,
        kline_data: pd.DataFrame,
        adjust_type: str
    ) -> Dict[str, float]:
        """
        计算精确复权因子（使用除权前收盘价）

        公式：
        - 复权因子 = (除权前收盘价 + 每股派息) / (除权前收盘价 - 每股送转股)

        注意：除权前收盘价 = 股权登记日收盘价
        """
        factors = {}

        # 确保日期列是date类型
        if 'trade_date' in kline_data.columns:
            kline_data = kline_data.copy()
            kline_data['trade_date'] = pd.to_datetime(kline_data['trade_date']).dt.date

        for _, row in dividend_data.iterrows():
            ex_date = row['ex_date']

            # 找到除权前一个交易日的收盘价（股权登记日）
            # 股权登记日 = 除权除息日 - 1个交易日（简化处理）
            ex_date_dt = pd.to_datetime(ex_date)

            # 查找除权前最近的交易日收盘价
            before_data = kline_data[
                pd.to_datetime(kline_data['trade_date']) < ex_date_dt
            ]

            if before_data.empty:
                continue

            # 取除权前最后一个交易日的收盘价
            close_price = before_data.iloc[-1]['close']

            # 计算每股送转派息
            bonus_per_share = row['bonus_ratio'] / 10.0
            transfer_per_share = row['transfer_ratio'] / 10.0
            dividend_per_share = row['dividend_ratio'] / 10.0

            # 计算复权因子
            if close_price > 0:
                # 前复权因子：将历史价格调整到当前水平
                # factor = (收盘价 + 派息) / (收盘价 - 送转股)
                # 简化：factor = 收盘价 / (收盘价 - 送转股)
                denominator = close_price - bonus_per_share - transfer_per_share
                if denominator > 0:
                    factor = (close_price + dividend_per_share) / denominator
                else:
                    factor = 1.0
            else:
                factor = 1.0

            factors[ex_date] = factor

        return factors

    def adjust_kline_forward(
        self,
        df: pd.DataFrame,
        symbol: str,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        前复权处理

        Args:
            df: 原始K线数据
            symbol: 股票代码
            use_cache: 是否使用缓存

        Returns:
            前复权后的DataFrame
        """
        if df.empty:
            return df.copy()

        df = df.copy()

        # 确保日期列是date类型
        if 'trade_date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        # 获取除权数据
        dividend_data = self.get_dividend_data(symbol, use_cache=use_cache)
        if dividend_data is None or dividend_data.empty:
            logger.debug(f"{symbol} 无除权数据，返回原始数据")
            df['adjust_type'] = 'none'
            return df

        # 计算复权因子
        factors = self.calculate_adjust_factors(symbol, df, 'qfq')
        if not factors:
            logger.debug(f"{symbol} 无法计算复权因子，返回原始数据")
            df['adjust_type'] = 'none'
            return df

        # 应用前复权
        df = df.sort_values('trade_date').reset_index(drop=True)

        # 从最新日期往前应用复权因子
        cumulative_factor = 1.0

        for idx in range(len(df) - 1, -1, -1):
            row_date = df.loc[idx, 'trade_date']

            # 检查是否需要调整复权因子
            for ex_date in sorted(factors.keys(), reverse=True):
                if row_date <= ex_date:
                    cumulative_factor *= factors[ex_date]
                    break

            # 应用复权因子
            if cumulative_factor != 1.0:
                price_columns = ['open', 'high', 'low', 'close']
                for col in price_columns:
                    if col in df.columns:
                        df.loc[idx, col] = df.loc[idx, col] / cumulative_factor

        df['adjust_type'] = 'qfq'
        logger.info(f"✅ {symbol} 前复权完成")

        return df

    def adjust_kline_backward(
        self,
        df: pd.DataFrame,
        symbol: str,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        后复权处理

        Args:
            df: 原始K线数据
            symbol: 股票代码
            use_cache: 是否使用缓存

        Returns:
            后复权后的DataFrame
        """
        if df.empty:
            return df.copy()

        df = df.copy()

        # 确保日期列是date类型
        if 'trade_date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        # 获取除权数据
        dividend_data = self.get_dividend_data(symbol, use_cache=use_cache)
        if dividend_data is None or dividend_data.empty:
            logger.debug(f"{symbol} 无除权数据，返回原始数据")
            df['adjust_type'] = 'none'
            return df

        # 计算复权因子
        factors = self.calculate_adjust_factors(symbol, df, 'hfq')
        if not factors:
            logger.debug(f"{symbol} 无法计算复权因子，返回原始数据")
            df['adjust_type'] = 'none'
            return df

        # 应用后复权
        df = df.sort_values('trade_date').reset_index(drop=True)

        # 从最早日期往后应用复权因子
        cumulative_factor = 1.0

        for idx in range(len(df)):
            row_date = df.loc[idx, 'trade_date']

            # 检查是否需要调整复权因子
            for ex_date in sorted(factors.keys()):
                if row_date > ex_date:
                    cumulative_factor *= factors[ex_date]
                else:
                    break

            # 应用复权因子
            if cumulative_factor != 1.0:
                price_columns = ['open', 'high', 'low', 'close']
                for col in price_columns:
                    if col in df.columns:
                        df.loc[idx, col] = df.loc[idx, col] * cumulative_factor

        df['adjust_type'] = 'hfq'
        logger.info(f"✅ {symbol} 后复权完成")

        return df


# 全局单例
_calculator_instance: Optional[AdjustFactorCalculator] = None


def get_adjust_calculator() -> AdjustFactorCalculator:
    """获取复权计算器单例"""
    global _calculator_instance
    if _calculator_instance is None:
        _calculator_instance = AdjustFactorCalculator()
    return _calculator_instance


# 便捷函数
def forward_adjust(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """前复权（便捷函数）"""
    calculator = get_adjust_calculator()
    return calculator.adjust_kline_forward(df, symbol)


def backward_adjust(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """后复权（便捷函数）"""
    calculator = get_adjust_calculator()
    return calculator.adjust_kline_backward(df, symbol)


# 使用示例
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # 添加backend到路径
    backend_dir = Path(__file__).parent.parent.parent.absolute()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from data_job.core.tdx_adapter import TdxAdapter

    print("=" * 60)
    print("测试复权计算器")
    print("=" * 60)

    # 测试股票
    test_symbol = '600519'

    # 获取K线数据
    print(f"\n获取 {test_symbol} K线数据...")
    tdx = TdxAdapter()

    if tdx.is_available():
        from datetime import datetime, timedelta

        end = datetime.now()
        start = end - timedelta(days=90)

        df = tdx.get_stock_kline(
            symbol=test_symbol,
            start_date=start.strftime('%Y-%m-%d'),
            end_date=end.strftime('%Y-%m-%d')
        )

        if df is not None and not df.empty:
            print(f"✅ 获取原始K线数据 {len(df)} 条")
            print("\n原始数据（最新5天）:")
            print(df[['trade_date', 'close', 'volume']].tail())

            # 前复权
            print(f"\n计算前复权...")
            df_qfq = forward_adjust(df, test_symbol)
            print("\n前复权数据（最新5天）:")
            print(df_qfq[['trade_date', 'close', 'volume']].tail())

            # 对比
            print(f"\n复权前后收盘价对比（最新5天）:")
            comparison = pd.DataFrame({
                'trade_date': df['trade_date'].tail(),
                '原始收盘': df['close'].tail(),
                '前复权收盘': df_qfq['close'].tail()
            })
            print(comparison.to_string(index=False))
        else:
            print("❌ 获取K线数据失败")
    else:
        print("❌ pytdx连接失败")

    print("\n" + "=" * 60)
