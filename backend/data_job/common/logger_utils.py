"""
日志工具 - data_job 模块
"""
import logging
import sys


def setup_logger(name=None, level=logging.INFO,
                 fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'):
    """
    配置并返回 logger

    Args:
        name: logger 名称（默认自动推断调用者模块名）
        level: 日志级别
        fmt: 日志格式

    Returns:
        logging.Logger: 配置好的 logger 对象
    """
    # 配置 root logger（只配置一次）
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format=fmt,
            stream=sys.stdout
        )

    # 如果没有指定名称，使用调用者的模块名
    if name is None:
        import inspect
        frame = inspect.currentframe().f_back
        name = frame.f_globals.get('__name__', '__main__')

    return logging.getLogger(name)
