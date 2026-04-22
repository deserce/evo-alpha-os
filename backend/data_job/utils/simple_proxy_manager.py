"""
简单代理管理器 - 使用免费代理 API
无需复杂的代理池服务
"""

import requests
import time
import random
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


# 免费代理 API 列表
PROXY_APIS = [
    'https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all',
    'https://api.openproxylist.xyz/http.txt',
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
    'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
]


class SimpleProxyManager:
    """简单代理管理器"""

    def __init__(self):
        self.proxies: List[str] = []
        self.current_index = 0
        self.last_update = 0
        self.update_interval = 300  # 5 分钟更新一次

    def fetch_proxies(self) -> List[str]:
        """从多个源获取代理"""
        all_proxies = []

        for api_url in PROXY_APIS:
            try:
                response = requests.get(api_url, timeout=10)
                if response.status_code == 200:
                    # 解析代理列表
                    lines = response.text.strip().split('\n')
                    for line in lines:
                        line = line.strip()
                        if ':' in line and not line.startswith('#'):
                            # 验证格式
                            parts = line.split(':')
                            if len(parts) == 2:
                                ip, port = parts
                                if ip.replace('.', '').isdigit() and port.isdigit():
                                    all_proxies.append(line)
            except Exception as e:
                logger.debug(f"获取代理失败 {api_url}: {e}")

        logger.info(f"获取到 {len(all_proxies)} 个代理")
        return all_proxies

    def get_proxy(self) -> Optional[Dict]:
        """获取一个代理"""
        current_time = time.time()

        # 如果代理列表为空或过期，更新
        if not self.proxies or (current_time - self.last_update) > self.update_interval:
            self.proxies = self.fetch_proxies()
            self.last_update = current_time
            self.current_index = 0

        if not self.proxies:
            return None

        # 轮换代理
        proxy = self.proxies[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.proxies)

        return {
            'http': f'http://{proxy}',
            'https': f'http://{proxy}'
        }

    def remove_proxy(self, proxy: Dict):
        """移除无效代理"""
        if proxy and 'http' in proxy:
            proxy_addr = proxy['http'].replace('http://', '')
            if proxy_addr in self.proxies:
                self.proxies.remove(proxy_addr)
                logger.info(f"移除无效代理：{proxy_addr}")


# 全局代理管理器
_proxy_manager = SimpleProxyManager()


def get_proxy() -> Optional[Dict]:
    """获取代理"""
    return _proxy_manager.get_proxy()


def with_proxy_retry(max_retries: int = 10, timeout: int = 10, function_timeout: int = 30):
    """
    代理重试装饰器

    Args:
        max_retries: 最大重试次数
        timeout: 请求超时
        function_timeout: 函数执行超时
    """
    def decorator(func):
        import functools

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    # 获取代理
                    proxy = get_proxy()

                    if proxy:
                        logger.info(f"尝试 {attempt + 1}/{max_retries}: 使用代理 {proxy['http']}")

                        # 设置代理
                        session = requests.Session()
                        session.proxies.update(proxy)
                        session.headers.update({
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                        })

                        # 临时替换 requests 的 Session
                        original_session = requests.Session
                        requests.Session = lambda: session

                        try:
                            result = func(*args, **kwargs)
                        finally:
                            requests.Session = original_session

                        logger.info(f"✅ 请求成功")
                        return result
                    else:
                        # 无代理，直接请求
                        logger.warning(f"尝试 {attempt + 1}/{max_retries}: 无可用代理，直接请求")
                        return func(*args, **kwargs)

                except Exception as e:
                    error_msg = str(e)
                    logger.warning(f"请求错误 {attempt + 1}/{max_retries}: {type(e).__name__}: {error_msg}")

                    # 移除无效代理
                    if proxy:
                        _proxy_manager.remove_proxy(proxy)

                    # 等待后重试
                    if attempt < max_retries - 1:
                        wait_time = min(2 ** attempt + random.uniform(0, 1), 10)
                        logger.info(f"等待 {wait_time:.1f}秒后重试...")
                        time.sleep(wait_time)

            raise RuntimeError(f"超过最大重试次数 {max_retries}")

        return wrapper
    return decorator


def get_akshare_data(func, *args, max_retries: int = 10, timeout: int = 10, function_timeout: int = 30, **kwargs):
    """
    通用的 AKShare 数据获取函数（带代理）

    Args:
        func: AKShare 函数
        *args: 函数参数
        max_retries: 最大重试次数
        timeout: 请求超时
        function_timeout: 函数执行超时
        **kwargs: 函数关键字参数

    Returns:
        函数执行结果
    """
    @with_proxy_retry(max_retries=max_retries, timeout=timeout, function_timeout=function_timeout)
    def fetcher():
        return func(*args, **kwargs)

    return fetcher()


# 使用示例
if __name__ == "__main__":
    import akshare as ak

    print("=" * 80)
    print("测试简单代理管理器")
    print("=" * 80)

    # 测试获取代理
    print("\n测试 1: 获取代理")
    proxy = get_proxy()
    if proxy:
        print(f"✅ 获取代理成功：{proxy['http']}")
    else:
        print("⚠️  无可用代理")

    # 测试获取股票数据
    print("\n测试 2: 获取股票数据")
    try:
        data = get_akshare_data(
            ak.stock_zh_a_hist,
            symbol="000001",
            period="daily",
            start_date="20260301",
            end_date="20260307",
            adjust="qfq",
            max_retries=5
        )
        print(f"✅ 成功获取数据：{len(data)}条")
        print(data.head())
    except Exception as e:
        print(f"❌ 获取数据失败：{e}")
