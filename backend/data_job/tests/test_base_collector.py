"""
测试 BaseCollector 核心功能
"""
import sys
import unittest
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, '.')

from data_job.core.base_collector import BaseCollector
from data_job.common import setup_backend_path, setup_network_emergency_kit

setup_backend_path()
setup_network_emergency_kit()


class TestBaseCollector(unittest.TestCase):
    """测试 BaseCollector 基类功能"""

    def setUp(self):
        """测试前准备"""
        # 创建一个测试用的采集器
        class TestCollector(BaseCollector):
            def run(self):
                return True

        self.collector = TestCollector(
            collector_name="test",
            request_timeout=10,
            request_delay=0.1,
            max_retries=2
        )

    def test_initialization(self):
        """测试初始化"""
        self.assertEqual(self.collector.collector_name, "test")
        self.assertEqual(self.collector.request_timeout, 10)
        self.assertEqual(self.collector.request_delay, 0.1)
        self.assertEqual(self.collector.max_retries, 2)
        self.assertIsNotNone(self.collector.engine)

    def test_progress_tracking(self):
        """测试进度跟踪"""
        # 测试进度保存
        self.collector.update_progress(
            last_symbol="000001",
            processed_count=100
        )

        # 验证进度已更新
        self.assertEqual(self.collector.progress['last_symbol'], "000001")
        self.assertEqual(self.collector.progress['processed_count'], 100)

    def test_get_collection_statistics(self):
        """测试统计信息获取"""
        stats = self.collector.get_collection_statistics()

        self.assertIn('collector_name', stats)
        self.assertIn('last_update', stats)
        self.assertIn('collection_count', stats)

    def test_log_methods(self):
        """测试日志方法"""
        # 这些方法不应该抛出异常
        try:
            self.collector.log_collection_start()
            self.collector.log_collection_end(True, "测试完成")
            self.collector.log_collection_end(False, "测试失败")
        except Exception as e:
            self.fail(f"日志方法抛出异常: {e}")


class TestRetryCall(unittest.TestCase):
    """测试重试机制"""

    def setUp(self):
        """测试前准备"""
        class TestCollector(BaseCollector):
            def run(self):
                return True

        self.collector = TestCollector(
            collector_name="test_retry",
            max_retries=3
        )

    def test_successful_call(self):
        """测试成功的调用"""
        def success_func():
            return "success"

        result = self.collector._retry_call(success_func)
        self.assertEqual(result, "success")

    def test_retry_on_failure(self):
        """测试失败后的重试"""
        call_count = 0

        def fail_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("失败")
            return "success"

        result = self.collector._retry_call(fail_then_success, max_retries=3)
        self.assertEqual(result, "success")
        self.assertEqual(call_count, 2)

    def test_max_retries_exceeded(self):
        """测试超过最大重试次数"""
        def always_fail():
            raise Exception("总是失败")

        result = self.collector._retry_call(always_fail, max_retries=2)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
