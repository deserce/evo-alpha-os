"""
EvoAlpha OS - 个股RPS因子计算器
计算股票的相对价格强度(RPS)因子
"""

import sys
import os
import pandas as pd
from datetime import datetime

# ================= 环境路径适配 =================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../"))
if project_root not in sys.path:
    sys.path.append(project_root)

# ================= 公共工具导入 =================
from quant_engine.core.base_feature_calculator import BaseFeatureCalculator
from quant_engine.common import setup_logger

# ================= Logger配置 =================
logger = setup_logger(__name__)


class StockRPSCalculator(BaseFeatureCalculator):
    """
    个股RPS计算器

    功能：
    - 计算股票的相对价格强度
    - 支持多周期RPS（5/10/20/50/120/250日）
    - 增量更新模式

    数据表：
    - 源表: stock_daily_prices
    - 目标表: quant_feature_stock_rps
    """

    def get_source_table(self) -> str:
        """返回源表名"""
        return "stock_daily_prices"

    def get_target_table(self) -> str:
        """返回目标表名"""
        return "quant_feature_stock_rps"

    def get_entity_column(self) -> str:
        """返回标的列名"""
        return "symbol"

    def get_periods(self) -> list:
        """返回计算周期"""
        return [5, 10, 20, 50, 120, 250]

    def should_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        数据过滤逻辑

        个股不过滤，直接返回
        """
        return df


if __name__ == "__main__":
    import argparse

    calculator = StockRPSCalculator()

    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='daily', help='init or daily')
    args = parser.parse_args()

    if args.mode == 'init':
        calculator.run_init()
    else:
        calculator.run_daily()
