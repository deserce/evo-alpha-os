"""
EvoAlpha OS - 数据采集：股票名单
按季度更新股票和板块名单
"""

import akshare as ak
from loguru import logger
from datetime import datetime


def update_stock_list():
    """
    更新股票名单

    数据源：AkShare
    更新频率：每季度
    """
    logger.info("开始更新股票名单...")

    try:
        # 获取 A 股股票列表
        stock_list = ak.stock_info_a_code_name()

        logger.info(f"获取到 {len(stock_list)} 只股票")

        # TODO: 写入数据库
        # 1. 本地 SQLite
        # 2. 云端 CockroachDB

        logger.info("股票名单更新完成")
        return stock_list

    except Exception as e:
        logger.error(f"股票名单更新失败: {e}")
        raise


def update_sector_list():
    """
    更新板块名单

    数据源：AkShare
    更新频率：每季度
    """
    logger.info("开始更新板块名单...")

    try:
        # 获取行业板块列表
        sector_list = ak.stock_board_industry_name_em()

        logger.info(f"获取到 {len(sector_list)} 个板块")

        # TODO: 写入数据库

        logger.info("板块名单更新完成")
        return sector_list

    except Exception as e:
        logger.error(f"板块名单更新失败: {e}")
        raise


if __name__ == "__main__":
    update_stock_list()
    update_sector_list()
