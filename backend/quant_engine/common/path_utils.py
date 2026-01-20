"""
路径工具 - 自动处理quant_engine路径适配
"""
import sys
from pathlib import Path

def setup_quant_path():
    """
    自动将backend目录添加到Python路径

    Returns:
        str: backend目录的绝对路径
    """
    # 获取quant_engine目录的父目录（backend）
    quant_engine_dir = Path(__file__).parent.absolute()
    backend_dir = quant_engine_dir.parent.absolute()

    # 添加到sys.path
    backend_str = str(backend_dir)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)

    return backend_str
