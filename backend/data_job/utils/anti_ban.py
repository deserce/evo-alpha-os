"""
AKShare防封策略类
应对IP封禁、请求限制等反爬虫机制
"""

import random
import time
import logging
from typing import Optional, List, Callable
from functools import wraps
from datetime import datetime
import requests
from fake_useragent import UserAgent

logger = logging.getLogger(__name__)


class AntiBanStrategy:
    """
    AKShare防封策略类

    策略包括：
    1. 代理池轮换
    2. 随机延迟
    3. User-Agent轮换
    4. 智能重试
    5. 请求限流
    6. Session复用
    7. 异常处理
    """

    def __init__(
        self,
        # 代理配置
        proxy_list: Optional[List[str]] = None,
        proxy_enabled: bool = False,

        # 延迟配置
        min_delay: float = 0.5,
        max_delay: float = 2.0,
        delay_on_error: float = 5.0,

        # 重试配置
        max_retries: int = 5,
        retry_backoff: float = 2.0,

        # 限流配置
        rate_limit: int = 10,  # 每秒最多请求数
        rate_limit_window: int = 60,  # 时间窗口（秒）

        # User-Agent配置
        rotate_ua: bool = True,

        # Session配置
        session_pool_size: int = 5,
    ):
        """
        初始化防封策略

        Args:
            proxy_list: 代理列表 ['http://proxy1:port', 'http://proxy2:port', ...]
            proxy_enabled: 是否启用代理
            min_delay: 最小延迟（秒）
            max_delay: 最大延迟（秒）
            delay_on_error: 错误时延迟（秒）
            max_retries: 最大重试次数
            retry_backoff: 重试退避倍数
            rate_limit: 限流请求数
            rate_limit_window: 限流时间窗口
            rotate_ua: 是否轮换User-Agent
            session_pool_size: Session池大小
        """
        self.proxy_list = proxy_list or []
        self.proxy_enabled = proxy_enabled
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.delay_on_error = delay_on_error
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.rate_limit = rate_limit
        self.rate_limit_window = rate_limit_window
        self.rotate_ua = rotate_ua

        # 当前索引
        self._current_proxy_index = 0
        self._current_session_index = 0

        # 请求历史记录（用于限流）
        self._request_history: List[float] = []

        # Session池大小
        self._session_pool_size = session_pool_size

        # Session池
        self._sessions: List[requests.Session] = []
        self._init_sessions()

        # User-Agent生成器
        self.ua_generator = UserAgent() if rotate_ua else None

        # 统计信息
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'retries': 0,
            'proxy_switches': 0,
        }

        logger.info("防封策略初始化完成")
        logger.info(f"代理池: {len(self.proxy_list)} 个代理")
        logger.info(f"延迟范围: {min_delay}-{max_delay}秒")
        logger.info(f"重试次数: {max_retries}")
        logger.info(f"限流: {rate_limit}次/{rate_limit_window}秒")

    def _init_sessions(self):
        """初始化Session池"""
        self._sessions = []
        for i in range(self._session_pool_size or 1):
            session = requests.Session()
            # 设置默认超时
            session.timeout = 30
            self._sessions.append(session)
        logger.info(f"Session池初始化完成: {len(self._sessions)} 个Session")

    def _get_session(self) -> requests.Session:
        """获取一个Session（轮换）"""
        if not self._sessions:
            return requests.Session()

        session = self._sessions[self._current_session_index]
        self._current_session_index = (self._current_session_index + 1) % len(self._sessions)
        return session

    def _get_proxy(self) -> Optional[str]:
        """获取一个代理（轮换）"""
        if not self.proxy_list or not self.proxy_enabled:
            return None

        proxy = self.proxy_list[self._current_proxy_index]
        self._current_proxy_index = (self._current_proxy_index + 1) % len(self.proxy_list)
        self.stats['proxy_switches'] += 1
        return proxy

    def _get_user_agent(self) -> str:
        """获取一个User-Agent"""
        if self.ua_generator:
            return self.ua_generator.random
        return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

    def _check_rate_limit(self):
        """检查并执行限流"""
        now = time.time()

        # 清理过期的请求记录
        self._request_history = [
            t for t in self._request_history
            if now - t < self.rate_limit_window
        ]

        # 如果超过限流阈值，等待
        if len(self._request_history) >= self.rate_limit:
            wait_time = self.rate_limit_window - (now - self._request_history[0])
            if wait_time > 0:
                logger.warning(f"达到限流阈值，等待 {wait_time:.1f} 秒")
                time.sleep(wait_time)
                self._request_history.clear()

        # 记录本次请求
        self._request_history.append(now)

    def _random_delay(self):
        """随机延迟"""
        delay = random.uniform(self.min_delay, self.max_delay)
        logger.debug(f"延迟 {delay:.2f} 秒")
        time.sleep(delay)

    def _error_delay(self):
        """错误延迟"""
        logger.debug(f"错误延迟 {self.delay_on_error} 秒")
        time.sleep(self.delay_on_error)

    def safe_request(
        self,
        func: Callable,
        *args,
        **kwargs
    ):
        """
        安全执行请求（带防封策略）

        Args:
            func: 要执行的函数（如 ak.stock_zh_a_daily）
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果
        """
        # 更新统计
        self.stats['total_requests'] += 1

        # 限流检查
        self._check_rate_limit()

        # 准备请求参数
        proxy = self._get_proxy()
        user_agent = self._get_user_agent()

        # 如果函数不支持这些参数，需要在函数内部处理
        # 这里我们通过环境变量或全局变量传递
        import os
        if proxy:
            os.environ['AKSHARE_PROXY'] = proxy
            logger.debug(f"使用代理: {proxy}")

        os.environ['AKSHARE_USER_AGENT'] = user_agent

        # 重试循环
        last_error = None
        for attempt in range(self.max_retries):
            try:
                # 随机延迟（除了第一次尝试）
                if attempt > 0:
                    delay = self.delay_on_error * (self.retry_backoff ** attempt)
                    logger.warning(f"第 {attempt + 1} 次重试，延迟 {delay:.1f} 秒")
                    time.sleep(delay)
                    self.stats['retries'] += 1

                # 执行请求
                result = func(*args, **kwargs)

                # 成功
                self.stats['successful_requests'] += 1

                # 请求后延迟
                self._random_delay()

                return result

            except Exception as e:
                last_error = e
                error_msg = str(e).lower()

                # 分析错误类型
                if any(keyword in error_msg for keyword in ['403', 'forbidden', '封禁', 'banned']):
                    logger.error(f"可能被封禁 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                    # 切换代理
                    if self.proxy_enabled and self.proxy_list:
                        logger.info("切换到下一个代理")
                        proxy = self._get_proxy()

                elif any(keyword in error_msg for keyword in ['timeout', 'timed out', '连接']):
                    logger.warning(f"连接超时 (尝试 {attempt + 1}/{self.max_retries}): {e}")

                elif any(keyword in error_msg for keyword in ['connection', 'network']):
                    logger.warning(f"网络错误 (尝试 {attempt + 1}/{self.max_retries}): {e}")

                else:
                    logger.error(f"未知错误 (尝试 {attempt + 1}/{self.max_retries}): {e}")

                # 最后一次尝试失败
                if attempt == self.max_retries - 1:
                    self.stats['failed_requests'] += 1
                    logger.error(f"请求失败，已达最大重试次数: {e}")
                    raise

        # 理论上不会到这里
        if last_error:
            raise last_error

    def batch_request(
        self,
        func: Callable,
        items: list,
        batch_size: int = 100,
        batch_rest: int = 10,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        批量请求（分批处理，避免触发限流）

        Args:
            func: 请求函数
            items: 要处理的项目列表（如股票代码列表）
            batch_size: 每批大小
            batch_rest: 批次间休息时间（秒）
            progress_callback: 进度回调函数

        Returns:
            dict {item: result}
        """
        results = {}
        total = len(items)

        logger.info(f"开始批量请求: {total} 个项目，批次大小: {batch_size}")

        for i in range(0, total, batch_size):
            batch = items[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total + batch_size - 1) // batch_size

            logger.info(f"处理批次 {batch_num}/{total_batches} ({len(batch)} 个项目)")

            for item in batch:
                try:
                    result = self.safe_request(func, item)
                    results[item] = result

                    if progress_callback:
                        progress_callback(item, result)

                except Exception as e:
                    logger.error(f"处理 {item} 失败: {e}")
                    results[item] = None

            # 批次间休息（除了最后一批）
            if i + batch_size < total:
                logger.info(f"批次间休息 {batch_rest} 秒...")
                time.sleep(batch_rest)

        logger.info(f"批量请求完成: 成功 {sum(1 for v in results.values() if v is not None)}/{total}")

        return results

    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = self.stats.copy()

        # 计算成功率
        if stats['total_requests'] > 0:
            stats['success_rate'] = stats['successful_requests'] / stats['total_requests']
        else:
            stats['success_rate'] = 0.0

        return stats

    def reset_stats(self):
        """重置统计信息"""
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'retries': 0,
            'proxy_switches': 0,
        }
        logger.info("统计信息已重置")

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        # 关闭所有Session
        for session in self._sessions:
            try:
                session.close()
            except:
                pass
        logger.info("防封策略已清理")


# 预定义的防封策略配置
ANTI_BAN_CONFIGS = {
    'conservative': {  # 保守策略（最不容易被封，但速度慢）
        'min_delay': 1.0,
        'max_delay': 3.0,
        'max_retries': 5,
        'rate_limit': 5,
        'rate_limit_window': 60,
    },
    'balanced': {  # 平衡策略（推荐）
        'min_delay': 0.5,
        'max_delay': 2.0,
        'max_retries': 5,
        'rate_limit': 10,
        'rate_limit_window': 60,
    },
    'aggressive': {  # 激进策略（速度快，但容易被封）
        'min_delay': 0.2,
        'max_delay': 1.0,
        'max_retries': 3,
        'rate_limit': 20,
        'rate_limit_window': 60,
    },
}


def create_anti_ban_strategy(
    mode: str = 'balanced',
    proxy_list: Optional[List[str]] = None,
    **kwargs
) -> AntiBanStrategy:
    """
    创建防封策略实例（便捷函数）

    Args:
        mode: 预设模式 ('conservative', 'balanced', 'aggressive')
        proxy_list: 代理列表
        **kwargs: 其他自定义参数

    Returns:
        AntiBanStrategy实例
    """
    config = ANTI_BAN_CONFIGS.get(mode, ANTI_BAN_CONFIGS['balanced'])
    config.update(kwargs)
    config['proxy_list'] = proxy_list

    return AntiBanStrategy(**config)


# 装饰器版本
def with_anti_ban(
    strategy: Optional[AntiBanStrategy] = None,
    **kwargs
):
    """
    防封装饰器

    用法:
    ```python
    @with_anti_ban(mode='balanced')
    def collect_data(symbol):
        return ak.stock_zh_a_daily(symbol=symbol)
    ```
    """
    if strategy is None:
        strategy = create_anti_ban_strategy(**kwargs)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs_inner):
            return strategy.safe_request(func, *args, **kwargs_inner)
        return wrapper

    return decorator
