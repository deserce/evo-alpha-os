"""
EvoAlpha OS - 防封IP采集器基类
提供通用的反爬虫、防封IP功能
"""

import random
import time
import logging
from typing import List, Dict, Callable, Any, Optional
from datetime import datetime
import threading

# User-Agent池
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


class RateLimiter:
    """速率限制器 - 控制请求频率"""

    def __init__(self, max_requests_per_minute: int = 30):
        """
        初始化速率限制器

        Args:
            max_requests_per_minute: 每分钟最大请求数
        """
        self.max_requests = max_requests_per_minute
        self.requests = []
        self.lock = threading.Lock()

    def wait_if_needed(self):
        """如果达到速率限制，等待到可以发送下一个请求"""
        with self.lock:
            now = time.time()

            # 移除1分钟前的请求记录
            self.requests = [t for t in self.requests if now - t < 60]

            # 如果达到速率限制，计算等待时间
            if len(self.requests) >= self.max_requests:
                oldest_request = min(self.requests)
                wait_time = 60 - (now - oldest_request)

                if wait_time > 0:
                    logging.info(f"⏱️  达到速率限制，等待 {wait_time:.1f} 秒...")
                    time.sleep(wait_time)
                    self.requests = []

            # 记录这次请求
            self.requests.append(now)


class ProxyRotator:
    """代理轮换器 - 支持多代理轮换"""

    def __init__(self, proxy_list: Optional[List[str]] = None):
        """
        初始化代理轮换器

        Args:
            proxy_list: 代理列表，格式 ['http://host:port', ...]
        """
        self.proxy_list = proxy_list or []
        self.current_index = 0
        self.failed_proxies = set()

    def get_next_proxy(self) -> Optional[Dict[str, str]]:
        """
        获取下一个可用代理

        Returns:
            代理字典 {'http': '...', 'https': '...'} 或 None
        """
        if not self.proxy_list:
            return None

        # 过滤掉失败的代理
        available_proxies = [p for p in self.proxy_list if p not in self.failed_proxies]

        if not available_proxies:
            # 如果所有代理都失败了，重置失败列表并重试
            logging.warning("⚠️  所有代理都失败，重置失败列表")
            self.failed_proxies.clear()
            available_proxies = self.proxy_list

        # 获取下一个代理
        proxy = available_proxies[self.current_index % len(available_proxies)]
        self.current_index += 1

        return {
            'http': proxy,
            'https': proxy
        }

    def mark_proxy_failed(self, proxy: str):
        """标记代理失败"""
        if proxy:
            proxy_url = proxy.get('http', proxy.get('https', ''))
            self.failed_proxies.add(proxy_url)
            logging.warning(f"⚠️  代理失败: {proxy_url}")


class AntiBanMixin:
    """
    防封IP混入类

    提供：
    1. 随机User-Agent
    2. 随机延迟
    3. 指数退避重试
    4. 速率限制
    5. 代理轮换
    """

    def __init__(self):
        """初始化防封功能"""
        # 防封配置
        self.min_delay = 1.0  # 最小延迟（秒）
        self.max_delay = 3.0  # 最大延迟（秒）
        self.max_retries = 3  # 最大重试次数

        # 速率限制器
        self.rate_limiter = RateLimiter(max_requests_per_minute=30)

        # 代理轮换器
        self.proxy_rotator = ProxyRotator()

        # 统计信息
        self.total_requests = 0
        self.failed_requests = 0
        self.retry_count = 0

    def get_random_user_agent(self) -> str:
        """获取随机User-Agent"""
        return random.choice(USER_AGENTS)

    def get_random_delay(self) -> float:
        """获取随机延迟时间"""
        return random.uniform(self.min_delay, self.max_delay)

    def anti_ban_delay(self):
        """执行防封延迟（带进度提示）"""
        delay = self.get_random_delay()
        if delay > 2.0:
            # 只在延迟较长时显示
            logging.debug(f"⏳ 防封延迟: {delay:.2f}秒")
        time.sleep(delay)

    def retry_with_backoff(self, func: Callable, *args, **kwargs) -> Any:
        """
        指数退避重试

        Args:
            func: 要执行的函数
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数执行结果

        Raises:
            Exception: 重试次数用尽后抛出原始异常
        """
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                # 速率限制检查
                self.rate_limiter.wait_if_needed()

                # 执行函数
                result = func(*args, **kwargs)

                # 成功执行
                self.total_requests += 1
                return result

            except Exception as e:
                last_exception = e
                self.retry_count += 1

                if attempt < self.max_retries - 1:
                    # 指数退避：2^attempt + 随机(0-1)
                    wait_time = (2 ** attempt) + random.uniform(0, 1)

                    logging.warning(
                        f"⚠️  请求失败 (尝试 {attempt + 1}/{self.max_retries}): {str(e)[:50]}"
                    )
                    logging.info(f"⏳ 等待 {wait_time:.1f} 秒后重试...")

                    time.sleep(wait_time)
                else:
                    # 最后一次尝试失败
                    self.failed_requests += 1
                    logging.error(f"❌ 请求失败，已达最大重试次数: {str(e)[:100]}")

        # 重试次数用尽，抛出异常
        raise last_exception

    def setup_request_headers(self, session=None) -> Dict[str, str]:
        """
        设置请求头（包含随机User-Agent）

        Args:
            session: 可选的Session对象

        Returns:
            请求头字典
        """
        headers = {
            'User-Agent': self.get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Referer': 'https://www.eastmoney.com/',  # 模拟从东方财富访问
        }

        # 如果提供了session，直接设置
        if session and hasattr(session, 'headers'):
            session.headers.update(headers)

        return headers

    def configure_proxy(self, proxy_list: List[str] = None):
        """
        配置代理列表

        Args:
            proxy_list: 代理列表，格式 ['http://host:port', ...]
        """
        if proxy_list:
            self.proxy_rotator = ProxyRotator(proxy_list)
            logging.info(f"✅ 已配置 {len(proxy_list)} 个代理")
        else:
            # 使用默认代理
            import os
            proxy_host = os.environ.get('PROXY_HOST', '127.0.0.1')
            proxy_port = os.environ.get('PROXY_PORT', '7897')
            proxy_url = f'http://{proxy_host}:{proxy_port}'
            self.proxy_rotator = ProxyRotator([proxy_url])
            logging.info(f"✅ 使用默认代理: {proxy_url}")

    def get_current_proxy(self) -> Optional[Dict[str, str]]:
        """获取当前代理"""
        return self.proxy_rotator.get_next_proxy()

    def mark_proxy_failed(self, proxy: Dict[str, str]):
        """标记当前代理失败"""
        self.proxy_rotator.mark_proxy_failed(proxy)

    def process_in_batches(self, items: List[Any], batch_size: int = 50,
                          wait_between_batches: int = 60) -> List[List[Any]]:
        """
        将大量数据分批处理

        Args:
            items: 要处理的数据列表
            batch_size: 每批大小
            wait_between_batches: 批次之间等待时间（秒）

        Returns:
            分批后的数据列表
        """
        total = len(items)
        batches = [items[i:i+batch_size] for i in range(0, total, batch_size)]

        logging.info(f"📦 将 {total} 项数据分为 {len(batches)} 批处理")
        logging.info(f"   每批 {batch_size} 项，批次间等待 {wait_between_batches} 秒")

        return batches

    def log_statistics(self):
        """记录统计信息"""
        if self.total_requests > 0:
            success_rate = (self.total_requests - self.failed_requests) / self.total_requests * 100
            logging.info(f"📊 请求统计: 总计={self.total_requests}, "
                        f"失败={self.failed_requests}, "
                        f"重试={self.retry_count}, "
                        f"成功率={success_rate:.1f}%")
