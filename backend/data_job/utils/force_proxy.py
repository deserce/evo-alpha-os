"""
强制代理器 - 使用 monkey patching 确保 AKShare 所有请求都走代理
"""

import os
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# 保存原始的 request 方法
_original_session_request = requests.Session.request


def setup_force_proxy(proxy_host='127.0.0.1', proxy_port=7897):
    """
    强制所有 requests 请求走代理

    Args:
        proxy_host: 代理主机
        proxy_port: 代理端口
    """
    proxy_url = f'http://{proxy_host}:{proxy_port}'

    def patched_request(self, method, url, **kwargs):
        """注入代理的请求方法"""
        # 强制使用代理
        kwargs.setdefault('proxies', {
            'http': proxy_url,
            'https': proxy_url
        })

        # 设置超时
        kwargs.setdefault('timeout', 30)

        # 调用原始方法
        return _original_session_request(self, method, url, **kwargs)

    # 应用 monkey patch
    requests.Session.request = patched_request
    logger.info(f"✅ 已强制所有 requests 请求走代理: {proxy_url}")

    # 同时设置环境变量（双重保险）
    os.environ['HTTP_PROXY'] = proxy_url
    os.environ['HTTPS_PROXY'] = proxy_url


def restore_proxy():
    """恢复原始请求方法"""
    requests.Session.request = _original_session_request
    logger.info("✅ 已恢复原始请求方法")


def test_proxy(proxy_host='127.0.0.1', proxy_port=7897):
    """
    测试代理连接

    Returns:
        bool: 代理是否可用
    """
    proxy_url = f'http://{proxy_host}:{proxy_port}'
    proxies = {
        'http': proxy_url,
        'https': proxy_url
    }

    try:
        # 测试连接
        response = requests.get(
            'https://www.baidu.com',
            proxies=proxies,
            timeout=5
        )
        if response.status_code == 200:
            logger.info(f"✅ 代理 {proxy_url} 可用")
            return True
    except Exception as e:
        logger.warning(f"⚠️ 代理 {proxy_url} 不可用: {e}")

    return False


if __name__ == "__main__":
    import akshare as ak

    # 测试代理
    if test_proxy():
        # 强制使用代理
        setup_force_proxy()

        print("\n测试 AKShare 数据获取...")
        try:
            df = ak.stock_zh_a_hist(
                symbol="000001",
                period="daily",
                start_date="20240301",
                end_date="20240308",
                adjust="qfq"
            )

            if df is not None and not df.empty:
                print(f"✅ 成功获取 {len(df)} 行数据")
                print(df.head())
            else:
                print("⚠️ 返回空数据")

        except Exception as e:
            print(f"❌ 失败: {e}")

        # 恢复
        restore_proxy()
