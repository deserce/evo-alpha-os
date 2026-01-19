"""
日志工具 - 统一的日志配置
"""
import logging
import sys


def setup_logger(name: str = None, level=logging.INFO,
                 format='%(asctime)s - %(levelname)s - %(message)s'):
    """
    配置并返回logger

    Args:
        name: logger名称（默认使用调用模块的__name__）
        level: 日志级别
        format: 日志格式

    Returns:
        logging.Logger: 配置好的logger对象
    """
    # 配置root logger（只配置一次）
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format=format,
            stream=sys.stdout
        )

    # 如果没有指定名称，使用调用者的模块名
    if name is None:
        # 获取调用者的栈帧
        import inspect
        frame = inspect.currentframe().f_back
        name = frame.f_globals.get('__name__', '__main__')

    return logging.getLogger(name)
