"""
异常类定义 - 统一的异常处理
"""


class CollectorException(Exception):
    """采集器基础异常"""
    pass


class NetworkError(CollectorException):
    """网络连接错误"""
    pass


class DataSourceError(CollectorException):
    """数据源错误"""
    pass


class DataValidationError(CollectorException):
    """数据验证错误"""
    pass


class ConfigError(CollectorException):
    """配置错误"""
    pass
