"""
代理管理器 - 使用代理IP池解决AKShare封IP问题
基于：https://github.com/jhao104/proxy_pool
"""

import requests
import time
import signal
from functools import wraps
from inspect import signature
import threading
import platform
import logging

# 禁用SSL警告
import urllib3
urllib3.disable_warnings()

logger = logging.getLogger(__name__)


# 超时异常类
class FunctionTimeoutError(Exception):
    pass


class ThreadTimeoutError(Exception):
    pass


def timeout_handler(signum, frame):
    """超时处理函数"""
    raise FunctionTimeoutError("函数执行超时")


def thread_timeout(func, args=(), kwargs=None, timeout_duration=10):
    """线程级别的超时处理（用于Windows平台）"""
    if kwargs is None:
        kwargs = {}

    result = [None]
    error = [None]

    def worker():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            error[0] = e

    thread = threading.Thread(target=worker)
    thread.daemon = True
    thread.start()
    thread.join(timeout_duration)

    if thread.is_alive():
        raise ThreadTimeoutError(f"函数执行超过{timeout_duration}秒")

    if error[0]:
        raise error[0]

    return result[0]


class ProxyManager:
    """代理管理器"""

    def __init__(self, proxy_pool_url: str = 'http://127.0.0.1:5010'):
        """
        初始化代理管理器

        Args:
            proxy_pool_url: 代理池服务地址
        """
        self.proxy_pool_url = proxy_pool_url

    def get_proxy(self, timeout: int = 3) -> dict:
        """
        从代理池获取代理

        Args:
            timeout: 超时时间

        Returns:
            {'http': 'http://ip:port', 'https': 'http://ip:port'}
        """
        try:
            url = f'{self.proxy_pool_url}/get/'
            response = requests.get(url, timeout=timeout)

            if response.status_code == 200:
                proxy_data = response.json()
                if proxy := proxy_data.get('proxy'):
                    logger.info(f"✅ 获取代理: {proxy}")
                    return {
                        'http': f'http://{proxy}',
                        'https': f'http://{proxy}'
                    }
            else:
                logger.warning(f"⚠️  代理池返回状态码: {response.status_code}")

        except Exception as e:
            logger.error(f"❌ 获取代理失败: {e}")

        return None

    def delete_proxy(self, proxy, timeout: int = 3):
        """
        删除无效代理

        Args:
            proxy: 代理字典或字符串
            timeout: 超时时间
        """
        if not proxy:
            return

        # 提取代理地址
        proxy_addr = None
        if isinstance(proxy, dict):
            proxy_addr = proxy.get('http', '').replace('http://', '')
        else:
            proxy_addr = proxy

        try:
            if proxy_addr:
                url = f'{self.proxy_pool_url}/delete/?proxy={proxy_addr}'
                requests.get(url, timeout=timeout)
                logger.info(f"🗑️  已删除无效代理: {proxy_addr}")
        except Exception as e:
            logger.warning(f"⚠️  删除代理失败: {e}")

    def get_proxies(self, count: int = 1) -> list:
        """
        批量获取代理

        Args:
            count: 获取数量

        Returns:
            代理列表
        """
        proxies = []
        for _ in range(count):
            proxy = self.get_proxy()
            if proxy:
                proxies.append(proxy)
            time.sleep(0.1)

        return proxies


# 保存原始请求方法
_original_request = requests.Session.request


def setup_session_with_proxy(proxy, timeout: int = 5):
    """
    设置全局请求使用代理（猴子补丁）

    Args:
        proxy: 代理配置
        timeout: 超时时间

    Returns:
        配置好的Session对象
    """
    def patched_request(self, method, url, **kwargs):
        # 注入代理和超时设置
        kwargs.setdefault('proxies', proxy)
        kwargs.setdefault('timeout', timeout)
        kwargs.setdefault('verify', False)  # 禁用SSL验证

        # 调用原始方法
        return _original_request(self, method, url, **kwargs)

    # 应用猴子补丁
    requests.Session.request = patched_request

    # 返回新Session
    return requests.Session()


def restore_session():
    """恢复原始请求方法"""
    requests.Session.request = _original_request


def with_proxy_retry(max_retries: int = 10, timeout: int = 5, function_timeout: int = 10):
    """
    代理重试装饰器

    Args:
        max_retries: 最大重试次数
        timeout: 请求超时
        function_timeout: 函数执行超时

    Returns:
        装饰器函数
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            proxy_manager = ProxyManager()

            for attempt in range(max_retries):
                proxy = None
                session_patched = False

                try:
                    # 获取代理
                    proxy = proxy_manager.get_proxy(timeout=timeout)
                    if not proxy:
                        logger.warning(f"尝试 {attempt+1}/{max_retries}: 无可用代理")
                        time.sleep(0.5)
                        continue

                    logger.info(f"尝试 {attempt+1}/{max_retries}: 使用代理 {proxy['http']}")

                    # 应用猴子补丁
                    session = setup_session_with_proxy(proxy, timeout)
                    session_patched = True

                    # 设置函数参数
                    call_kwargs = kwargs.copy()

                    # 使用超时控制执行函数
                    start_time = time.time()

                    # 根据操作系统选择超时处理方式
                    if platform.system() != 'Windows':
                        # Unix系统使用信号处理
                        signal.signal(signal.SIGALRM, timeout_handler)
                        signal.alarm(function_timeout)
                        try:
                            result = func(*args, **call_kwargs)
                            signal.alarm(0)  # 取消闹钟
                        except FunctionTimeoutError:
                            raise TimeoutError(f"函数执行超过{function_timeout}秒")
                    else:
                        # Windows系统使用线程处理
                        result = thread_timeout(
                            func=func,
                            args=args,
                            kwargs=call_kwargs,
                            timeout_duration=function_timeout
                        )

                    elapsed = time.time() - start_time

                    # 验证结果
                    if hasattr(result, 'empty') and result.empty:
                        logger.warning(f"返回数据为空，尝试下一个代理")
                        proxy_manager.delete_proxy(proxy, timeout=timeout)
                        continue

                    logger.info(f"✅ 请求成功，耗时 {elapsed:.2f}s")
                    return result

                except (
                    requests.exceptions.Timeout,
                    requests.exceptions.ProxyError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.ReadTimeout,
                    requests.exceptions.ChunkedEncodingError,
                    TimeoutError,
                    ThreadTimeoutError,
                    FunctionTimeoutError
                ) as e:
                    logger.warning(f"请求错误 {attempt+1}/{max_retries} [{type(e).__name__}]: {str(e)}")
                    if proxy:
                        proxy_manager.delete_proxy(proxy, timeout=timeout)

                except Exception as e:
                    logger.error(f"发生其它错误 {attempt+1}/{max_retries}: {type(e).__name__}: {str(e)}")
                    if proxy and ("连接" in str(e) or "timeout" in str(e).lower()):
                        proxy_manager.delete_proxy(proxy, timeout=timeout)

                finally:
                    # 确保恢复原始请求方法
                    if session_patched:
                        restore_session()

                    # 确保信号处理被重置（仅适用于Unix系统）
                    if platform.system() != 'Windows':
                        signal.alarm(0)

                    time.sleep(0.5)

            raise RuntimeError(f"超过最大重试次数 {max_retries}")

        return wrapper
    return decorator


def get_akshare_data(func, *args, max_retries: int = 10, timeout: int = 5, function_timeout: int = 10, **kwargs):
    """
    通用的AKShare数据获取函数（带代理）

    Args:
        func: AKShare函数
        *args: 函数参数
        max_retries: 最大重试次数
        timeout: 请求超时
        function_timeout: 函数执行超时
        **kwargs: 函数关键字参数

    Returns:
        函数执行结果
    """
    @with_proxy_retry(max_retries=max_retries, timeout=timeout, function_timeout=function_timeout)
    def fetcher(**kw):
        return func(*args, **kwargs)

    return fetcher()


# 使用示例
if __name__ == "__main__":
    import akshare as ak

    print("=" * 80)
    print("测试代理管理器")
    print("=" * 80)

    try:
        # 测试获取股票信息
        print("\n测试1: 获取股票列表")
        data = get_akshare_data(
            ak.stock_info_a_code_name,
            max_retries=3,
            timeout=5,
            function_timeout=10
        )
        print(f"✅ 成功获取股票列表: {len(data)}只")

        # 测试获取K线数据
        print("\n测试2: 获取K线数据")
        data = get_akshare_data(
            ak.stock_zh_a_hist,
            symbol="000001",
            period="daily",
            start_date="20260301",
            end_date="20260307",
            adjust="qfq",
            max_retries=3,
            timeout=5,
            function_timeout=10
        )
        print(f"✅ 成功获取K线数据: {len(data)}条")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
