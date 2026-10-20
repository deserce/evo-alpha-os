"""
网络工具 - data_job 模块
"""
import os
import logging

logger = logging.getLogger(__name__)


PROXY_ENV_KEYS = tuple(
    name for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'FTP_PROXY', 'AKSHARE_PROXY')
    for name in (key, key.lower())
)


def sanitize_direct_env(environment: dict[str, str]) -> dict[str, str]:
    """Return a project-local direct environment, preserving unrelated settings."""
    result = dict(environment)
    for key in PROXY_ENV_KEYS:
        result.pop(key, None)
    result.update(NO_PROXY='*', no_proxy='*')
    return result


def setup_network_emergency_kit():
    """Disable application proxies for this process; retain TLS verification."""
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(NO_PROXY='*', no_proxy='*')
