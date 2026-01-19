"""
路径工具 - 自动处理backend路径适配
"""
import os
import sys
from pathlib import Path


def setup_backend_path():
    """
    自动将backend目录添加到Python路径

    Returns:
        str: backend目录的绝对路径
    """
    # 获取当前文件所在目录
    current_dir = Path(__file__).parent.absolute()
    # 向上一级到backend目录
    backend_dir = current_dir.parent.absolute()

    # 添加到sys.path
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    return str(backend_dir)
