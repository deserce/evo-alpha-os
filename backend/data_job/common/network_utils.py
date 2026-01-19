"""
网络工具 - 网络急救包、SSL证书处理
"""
import os
import ssl
import logging

logger = logging.getLogger(__name__)


def setup_network_emergency_kit():
    """
    网络急救包 - 解决VPN和SSL证书问题
    - 清除代理设置
    - 忽略SSL证书验证
    """
    # 清除系统代理
    proxy_vars = ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']
    cleared = []
    for var in proxy_vars:
        if var in os.environ:
            del os.environ[var]
            cleared.append(var)

    if cleared:
        logger.info(f"🚑 网络急救包: 已清除代理设置 {cleared}")

    # 忽略SSL证书验证
    ssl._create_default_https_context = ssl._create_unverified_context
    logger.debug("✅ SSL证书验证已禁用")
