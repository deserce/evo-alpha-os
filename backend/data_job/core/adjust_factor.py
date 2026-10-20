"""
前复权计算模块

通达信协议(pytdx/mootdx)不直接支持复权，需要通过除权数据计算复权因子。

前复权原理：
- 以当前股价为基准
- 将历史价格按照除权比例进行调整
- 公式：复权价 = 原价 × 复权因子
- 复权因子 = 除权前收盘价 / (除权前收盘价 - 每股送股 + 每股现金分红)
"""

import logging
from typing import Optional, Dict
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class AdjustFactorCalculator:
    """复权因子计算器"""

    def __init__(self):
        self.factor_cache = {}  # 缓存复权因子

    def get_dividend_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        获取除权数据

        Args:
            symbol: 股票代码

        Returns:
            DataFrame with columns: trade_date, bonus_ratio, dividend_ratio
        """
        try:
            import akshare as ak

            # 获取分红送股数据
            df = ak.stock_dividend_detail(symbol=symbol)

            if df is None or df.empty:
                return None

            # 处理列名
            df = df.rename(columns={
                '股权登记日': 'trade_date',
                '送转比例': 'bonus_ratio',
                '派现比例': 'dividend_ratio'
            })

            # 提取需要的列
            df = df[['trade_date', 'bonus_ratio', 'dividend_ratio']].copy()

            # 转换日期
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

            # 填充空值
            df['bonus_ratio'] = df['bonus_ratio'].fillna(0)
            df['dividend_ratio'] = df['dividend_ratio'].fillna(0)

            # 过滤掉没有送股和分红的记录
            df = df[(df['bonus_ratio'] > 0) | (df['dividend_ratio'] > 0)]

            return df.sort_values('trade_date')

        except Exception as e:
            logger.warning(f"获取{symbol}除权数据失败: {e}")
            return None

    def calculate_adjust_factor(
        self,
        symbol: str,
        dividend_data: Optional[pd.DataFrame] = None
    ) -> Dict[str, float]:
        """
        计算复权因子

        Args:
            symbol: 股票代码
            dividend_data: 除权数据

        Returns:
            {trade_date: adjust_factor} 字典
        """
        if dividend_data is None or dividend_data.empty:
            return {}

        factors = {}
        cumulative_factor = 1.0

        # 从最早的除权日开始计算
        dividend_data = dividend_data.sort_values('trade_date', ascending=True)

        for _, row in dividend_data.iterrows():
            trade_date = row['trade_date']
            bonus_ratio = row['bonus_ratio']  # 每10股送转股数
            dividend_ratio = row['dividend_ratio']  # 每10股派现数

            # 计算本次除权的复权因子
            # 注意：akshare的数据是每10股的，需要转换为每股
            if bonus_ratio > 0 or dividend_ratio > 0:
                # 前复权因子计算
                # factor = (前一日收盘价 + 每股分红) / (前一日收盘价 - 每股送股)
                # 这里简化处理，直接用比例
                bonus_per_share = bonus_ratio / 10.0  # 每股送转数
                dividend_per_share = dividend_ratio / 10.0  # 每股分红

                # 更新累积复权因子
                # 前复权：向后累乘
                cumulative_factor *= (1 + bonus_per_share)

                factors[trade_date] = cumulative_factor

        return factors

    def adjust_price_forward(
        self,
        df: pd.DataFrame,
        symbol: str,
        force_refresh: bool = False
    ) -> pd.DataFrame:
        """
        前复权处理

        Args:
            df: 原始K线数据
            symbol: 股票代码
            force_refresh: 是否强制刷新缓存

        Returns:
            前复权后的DataFrame
        """
        if df.empty:
            return df

        # 检查缓存
        cache_key = f"{symbol}_{df['trade_date'].min()}_{df['trade_date'].max()}"
        if not force_refresh and cache_key in self.factor_cache:
            factors = self.factor_cache[cache_key]
        else:
            # 获取除权数据
            dividend_data = self.get_dividend_data(symbol)
            factors = self.calculate_adjust_factor(symbol, dividend_data)
            self.factor_cache[cache_key] = factors

        if not factors:
            logger.debug(f"{symbol} 无除权数据，返回原始数据")
            return df.copy()

        # 对数据进行前复权
        df_adj = df.copy()

        # 获取最新的复权因子
        if factors:
            latest_factor = max(factors.values())

            # 按时间顺序处理
            df_adj = df_adj.sort_values('trade_date').reset_index(drop=True)

            # 找到除权日，应用复权因子
            current_factor = latest_factor

            for idx in range(len(df_adj) - 1, -1, -1):
                row_date = df_adj.loc[idx, 'trade_date']

                # 检查是否是除权日
                if isinstance(row_date, str):
                    row_date = pd.to_datetime(row_date).date()

                # 查找匹配的除权日
                for div_date, factor in factors.items():
                    if row_date <= div_date:
                        current_factor = factor
                        break

                # 应用复权因子
                if current_factor != 1.0:
                    df_adj.loc[idx, 'open'] /= current_factor
                    df_adj.loc[idx, 'high'] /= current_factor
                    df_adj.loc[idx, 'low'] /= current_factor
                    df_adj.loc[idx, 'close'] /= current_factor

            # 添加复权标记
            df_adj['adjust_type'] = 'qfq'

        return df_adj


# 单例模式
_adjust_calculator = None


def get_adjust_calculator() -> AdjustFactorCalculator:
    """获取复权因子计算器单例"""
    global _adjust_calculator

    if _adjust_calculator is None:
        _adjust_calculator = AdjustFactorCalculator()

    return _adjust_calculator


def forward_adjust(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    前复权数据处理（便捷函数）

    Args:
        df: K线数据
        symbol: 股票代码

    Returns:
        前复权后的数据
    """
    calculator = get_adjust_calculator()
    return calculator.adjust_price_forward(df, symbol)


# 使用示例
if __name__ == "__main__":
    # 测试前复权
    test_data = {
        'trade_date': [
            pd.to_datetime('2024-01-01').date(),
            pd.to_datetime('2024-01-02').date(),
            pd.to_datetime('2024-01-03').date(),
        ],
        'open': [10.0, 10.5, 11.0],
        'high': [10.5, 11.0, 11.5],
        'low': [9.5, 10.0, 10.5],
        'close': [10.0, 10.5, 11.0],
        'volume': [1000000, 1200000, 1500000],
        'amount': [10000000, 12600000, 16500000],
        'symbol': '600519'
    }

    df = pd.DataFrame(test_data)

    calculator = AdjustFactorCalculator()

    # 前复权处理
    df_adj = calculator.adjust_price_forward(df, '600519')

    print("原始数据:")
    print(df[['trade_date', 'close']])

    print("\n前复权数据:")
    print(df_adj[['trade_date', 'close']])
