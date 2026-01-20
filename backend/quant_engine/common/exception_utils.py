"""
量化引擎异常类定义
统一的异常处理和错误报告
"""

class QuantEngineException(Exception):
    """量化引擎基础异常"""
    pass

class CalculationError(QuantEngineException):
    """计算错误"""
    pass

class DataSourceError(QuantEngineException):
    """数据源错误（如数据库表不存在）"""
    pass

class ValidationError(QuantEngineException):
    """数据验证错误（如数据为空、格式不对）"""
    pass

class ConfigurationError(QuantEngineException):
    """配置错误"""
    pass
