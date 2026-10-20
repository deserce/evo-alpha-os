"""低吸长期跟踪与时机提醒引擎。"""

__all__ = ["WatchEngine"]


def __getattr__(name: str):
    """延迟导入，确保 ``python -m quant_engine.watch.engine`` 无重复加载警告。"""
    if name == "WatchEngine":
        from .engine import WatchEngine

        return WatchEngine
    raise AttributeError(name)
