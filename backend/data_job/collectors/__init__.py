"""
数据采集器实现层 - 各种业务数据采集器
"""

# Batch 1: Simple Collectors
from .stock_valuation_collector import StockValuationCollector
from .macro_data_collector import MacroDataCollector
from .limit_boards_collector import LimitBoardsCollector

# Batch 2: K-line Collectors
from .stock_kline_collector import StockKlineCollector
from .sector_kline_collector import SectorKlineCollector
from .etf_kline_collector import ETFKlineCollector

# Batch 3: Complex Collectors
from .fund_holdings_collector import FundHoldingsCollector
from .northbound_holdings_collector import NorthboundHoldingsCollector
from .etf_info_collector import ETFInfoCollector
from .finance_summary_collector import FinanceSummaryCollector
from .news_collector import NewsCollector
from .stock_sector_list_collector import StockSectorListCollector

__all__ = [
    # Batch 1: Simple Collectors
    'StockValuationCollector',
    'MacroDataCollector',
    'LimitBoardsCollector',
    # Batch 2: K-line Collectors
    'StockKlineCollector',
    'SectorKlineCollector',
    'ETFKlineCollector',
    # Batch 3: Complex Collectors
    'FundHoldingsCollector',
    'NorthboundHoldingsCollector',
    'ETFInfoCollector',
    'FinanceSummaryCollector',
    'NewsCollector',
    'StockSectorListCollector',
]
