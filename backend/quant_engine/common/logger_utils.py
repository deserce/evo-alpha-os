"""
日志工具 - quant_engine 模块
"""
import logging
import sys


def setup_logger(name=None, level=logging.INFO,
                 fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'):
    """
    配置并返回 logger

    Args:
        name: logger 名称
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

    return logging.getLogger(name if name else __name__)
