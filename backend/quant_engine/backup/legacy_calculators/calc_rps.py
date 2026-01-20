"""
EvoAlpha OS - 量化引擎：RPS因子计算
RPS (Relative Price Strength) - 相对价格强度
"""

import pandas as pd
import numpy as np
from loguru import logger
from typing import List


def calculate_rps(prices: pd.Series, period: int = 20) -> pd.Series:
    """
    计算RPS相对强度

    RPS = (当前价格在N日内的排名 / N日总数) × 100

    Args:
        prices: 价格序列
        period: 计算周期（默认20日）

    Returns:
        RPS值序列（0-100）
    """
    rps_values = []

    for i in range(len(prices)):
        if i < period - 1:
            # 数据不足，返回NaN
            rps_values.append(np.nan)
        else:
            # 获取过去N日的价格
            window_prices = prices.iloc[i - period + 1 : i + 1]

            # 计算当前价格的排名
            current_price = prices.iloc[i]
            rank = (window_prices < current_price).sum() + 1

            # 计算RPS
            rps = (rank / period) * 100
            rps_values.append(rps)

    return pd.Series(rps_values, index=prices.index)


def calculate_multi_period_rps(
    df: pd.DataFrame, price_col: str = "close"
) -> pd.DataFrame:
    """
    计算多周期RPS

    Args:
        df: 包含价格数据的DataFrame
        price_col: 价格列名

    Returns:
        添加了RPS列的DataFrame
    """
    periods = [5, 10, 20, 50, 120, 250]

    for period in periods:
        rps_col = f"rps_{period}"
        df[rps_col] = calculate_rps(df[price_col], period)

    logger.info(f"计算了 {len(periods)} 个周期的RPS")
    return df


def update_stock_rps(symbol: str):
    """
    更新单只股票的RPS数据

    Args:
        symbol: 股票代码
    """
    logger.info(f"开始计算 {symbol} 的RPS...")

    try:
        # TODO: 从数据库读取K线数据
        # df = read_stock_kline_from_db(symbol)

        # 占位数据
        df = pd.DataFrame({"close": [10, 11, 12, 13, 14, 15]})

        # 计算RPS
        df = calculate_multi_period_rps(df)

        logger.info(f"RPS计算完成: {df.tail(1)}")

        # TODO: 写入数据库

        logger.success(f"{symbol} 的RPS更新完成")

    except Exception as e:
        logger.error(f"{symbol} 的RPS计算失败: {e}")
        raise


def batch_update_rps(symbols: List[str]):
    """
    批量更新股票RPS

    Args:
        symbols: 股票代码列表
    """
    logger.info(f"开始批量更新 {len(symbols)} 只股票的RPS...")

    for symbol in symbols:
        try:
            update_stock_rps(symbol)
        except Exception as e:
            logger.error(f"股票 {symbol} RPS更新失败: {e}")
            continue

    logger.success("批量RPS更新完成")


if __name__ == "__main__":
    # 测试RPS计算
    test_symbols = ["000001", "600000"]
    batch_update_rps(test_symbols)
