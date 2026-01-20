"""
日志工具 - 统一的日志配置
"""
import logging
import sys

def setup_logger(name: str, level=logging.INFO):
    """
    配置并返回logger

    Args:
        name: logger名称
        level: 日志级别

    Returns:
        logging.Logger: 配置好的logger对象
    """
    # 配置root logger（只配置一次）
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            stream=sys.stdout
        )

    return logging.getLogger(name)
