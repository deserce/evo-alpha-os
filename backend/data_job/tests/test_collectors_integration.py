"""
采集器集成测试 - 测试所有采集器的基本功能
"""
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, '.')

from data_job.common import setup_backend_path, setup_network_emergency_kit

setup_backend_path()
setup_network_emergency_kit()

# 导入所有采集器
from data_job.collectors import (
    StockValuationCollector,
    MacroDataCollector,
    LimitBoardsCollector,
)


class TestCollectorsIntegration(unittest.TestCase):
    """采集器集成测试"""

    def setUp(self):
        """测试前准备"""
        # 模拟网络请求，避免实际调用
        self.patcher = patch('akshare.ak.stock_zh_a_spot_em')
        self.mock_api = self.patcher.start()

    def tearDown(self):
        """测试后清理"""
        self.patcher.stop()

    def test_stock_valuation_collector_initialization(self):
        """测试 StockValuationCollector 初始化"""
        collector = StockValuationCollector()
        self.assertEqual(collector.collector_name, "stock_valuation")
        self.assertIsNotNone(collector.engine)

    def test_macro_data_collector_initialization(self):
        """测试 MacroDataCollector 初始化"""
        collector = MacroDataCollector()
        self.assertEqual(collector.collector_name, "macro_data")
        self.assertIsNotNone(collector.engines)

    def test_limit_boards_collector_initialization(self):
        """测试 LimitBoardsCollector 初始化"""
        collector = LimitBoardsCollector()
        self.assertEqual(collector.collector_name, "limit_boards")
        self.assertIsNotNone(collector.engines)

    def test_all_collectors_import(self):
        """测试所有采集器可以正确导入"""
        from data_job.collectors import (
            StockValuationCollector,
            MacroDataCollector,
            LimitBoardsCollector,
            StockKlineCollector,
            SectorKlineCollector,
            ETFKlineCollector,
            FundHoldingsCollector,
            NorthboundHoldingsCollector,
            ETFInfoCollector,
            FinanceSummaryCollector,
            NewsCollector,
            StockSectorListCollector,
        )

        from data_job.core.base_collector import BaseCollector

        collectors = [
            StockValuationCollector,
            MacroDataCollector,
            LimitBoardsCollector,
            StockKlineCollector,
            SectorKlineCollector,
            ETFKlineCollector,
            FundHoldingsCollector,
            NorthboundHoldingsCollector,
            ETFInfoCollector,
            FinanceSummaryCollector,
            NewsCollector,
            StockSectorListCollector,
        ]

        for collector_class in collectors:
            self.assertTrue(
                issubclass(collector_class, BaseCollector),
                f"{collector_class} does not inherit from BaseCollector"
            )


class TestDataQuality(unittest.TestCase):
    """数据质量测试"""

    def test_common_module_imports(self):
        """测试公共模块可以正确导入"""
        from data_job.common import (
            setup_network_emergency_kit,
            setup_backend_path,
            setup_logger,
            CollectorException,
            NetworkError,
            DataSourceError,
        )

        # 测试函数可调用
        setup_backend_path()
        setup_network_emergency_kit()
        logger = setup_logger("test")
        self.assertIsNotNone(logger)

    def test_config_module_imports(self):
        """测试配置模块可以正确导入"""
        from data_job.config import CollectorConfig

        # 测试配置存在
        self.assertTrue(hasattr(CollectorConfig, 'REQUEST_TIMEOUT'))
        self.assertTrue(hasattr(CollectorConfig, 'REQUEST_DELAY'))
        self.assertTrue(hasattr(CollectorConfig, 'MAX_RETRIES'))


if __name__ == '__main__':
    unittest.main()
