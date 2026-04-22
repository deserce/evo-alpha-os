"""
路径工具 - data_job 模块
"""
import sys
from pathlib import Path


def setup_backend_path():
    """
    将 backend 目录添加到 Python 路径

    Returns:
        str: backend 目录的绝对路径
    """
    # data_job/common/ 在 backend/data_job/ 下，parent.parent 就是 backend/
    backend_dir = Path(__file__).parent.parent.parent.absolute()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return str(backend_dir)
