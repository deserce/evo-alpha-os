"""
采集器配置类 - 集中管理配置参数
"""
import logging


class CollectorConfig:
    """采集器配置类 - 集中管理所有采集相关的配置参数"""

    # 网络配置
    REQUEST_TIMEOUT = 30  # 请求超时（秒）
    REQUEST_DELAY = 0.5  # 请求间隔（秒）
    MAX_RETRIES = 3  # 最大重试次数
    RETRY_DELAY = 1.0  # 重试延迟（秒）
    EXPONENTIAL_BACKOFF = True  # 指数退避

    # 批量配置
    BATCH_SIZE = 100  # 批量处理大小
    CHUNK_SIZE = 100  # SQLite批量插入大小
    PARALLEL_WORKERS = 4  # 并发工作进程数

    # 数据保留策略
    DATA_RETENTION_DAYS = 1095  # 数据保留3年
    CLEANUP_OLD_DATA = True  # 自动清理旧数据

    # 监控配置
    ENABLE_MONITORING = True
    LOG_LEVEL = logging.INFO
    SAVE_PROGRESS = True

    # 数据库配置
    USE_TRANSACTION = True
