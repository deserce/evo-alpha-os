"""
核心框架层 - 提供采集器基类和核心功能
"""
from .base_collector import BaseCollector, BatchCollector, NetworkError, ConnectionTimeout

__all__ = [
    'BaseCollector',
    'BatchCollector',
    'NetworkError',
    'ConnectionTimeout'
]
