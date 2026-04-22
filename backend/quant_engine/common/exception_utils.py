"""
异常类定义 - quant_engine 模块
"""


class QuantEngineException(Exception):
    """量化引擎基础异常"""
    pass


class CalculationError(QuantEngineException):
    """计算错误"""
    pass


class DataSourceError(QuantEngineException):
    """数据源错误"""
    pass


class ValidationError(QuantEngineException):
    """数据验证错误"""
    pass


class ConfigurationError(QuantEngineException):
    """配置错误"""
    pass
