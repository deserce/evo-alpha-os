"""
EvoAlpha OS - 数据采集：K线数据
每日收盘后更新个股、板块、ETF的K线数据
"""

import akshare as ak
from loguru import logger
from datetime import datetime, timedelta
from typing import List


def update_stock_kline(symbols: List[str] = None):
    """
    更新个股K线数据

    数据源：AkShare
    更新频率：每个交易日收盘后（15:30）
    存储时长：近2年

    Args:
        symbols: 股票代码列表，如果为空则更新全部股票
    """
    logger.info("开始更新个股K线数据...")

    if symbols is None:
        # TODO: 从数据库获取全部股票代码
        symbols = ["000001", "600000"]  # 占位代码

    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")  # 2年

        for symbol in symbols[:3]:  # 限制测试数量
            logger.info(f"正在获取 {symbol} 的K线数据...")

            # 获取日线数据
            stock_zh_a_hist = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"  # 前复权
            )

            logger.info(f"获取到 {len(stock_zh_a_hist)} 条数据")

            # TODO: 写入数据库
            # 1. 本地 SQLite
            # 2. 云端 CockroachDB

        logger.success("个股K线数据更新完成")

    except Exception as e:
        logger.error(f"个股K线数据更新失败: {e}")
        raise


def update_sector_kline(sector_names: List[str] = None):
    """
    更新板块K线数据

    数据源：AkShare
    更新频率：每个交易日收盘后（15:30）
    存储时长：近2年
    """
    logger.info("开始更新板块K线数据...")

    if sector_names is None:
        # TODO: 从数据库获取全部板块名称
        sector_names = ["人工智能", "新能源"]  # 占位名称

    try:
        for sector_name in sector_names:
            logger.info(f"正在获取 {sector_name} 的K线数据...")

            # 获取板块日线数据
            sector_zh_a_hist = ak.stock_board_industry_name_em()

            # TODO: 写入数据库

        logger.success("板块K线数据更新完成")

    except Exception as e:
        logger.error(f"板块K线数据更新失败: {e}")
        raise


if __name__ == "__main__":
    update_stock_kline()
    update_sector_kline()
