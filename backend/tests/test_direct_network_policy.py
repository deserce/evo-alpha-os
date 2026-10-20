import logging
import ssl
from unittest.mock import Mock

from data_job.common import network_utils
from data_job.core.base_collector import BaseCollector
from data_job.core.direct_market_sources import EastmoneyDirectClient, CNInfoDirectClient


def test_environment_is_copied_and_all_proxy_variants_removed():
    original = {'HTTP_PROXY': 'http://127.0.0.1:1', 'all_proxy': 'socks5://localhost:1',
                'AKSHARE_PROXY': 'bad', 'PATH': '/usr/bin'}
    result = network_utils.sanitize_direct_env(original)
    assert result == {'PATH': '/usr/bin', 'NO_PROXY': '*', 'no_proxy': '*'}
    assert 'HTTP_PROXY' in original


def test_network_setup_preserves_tls_verification(monkeypatch):
    context = ssl._create_default_https_context
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    network_utils.setup_network_emergency_kit()
    assert ssl._create_default_https_context is context


def test_collector_session_ignores_explicit_and_environment_proxy(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    fake = Mock()
    fake.enable_anti_ban = True
    fake.logger = logging.getLogger('test')
    fake.request_timeout = 5
    fake.get_current_proxy.return_value = {'https': 'http://127.0.0.1:1'}
    fake.setup_request_headers.return_value = {'User-Agent': 'test'}
    with BaseCollector._create_session(fake) as session:
        assert session.trust_env is False
        assert session.proxies == {}


def test_direct_clients_ignore_bad_proxy(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    for client in (EastmoneyDirectClient(), CNInfoDirectClient()):
        try:
            assert client.session.trust_env is False
            assert client.session.proxies == {}
        finally:
            client.session.close()
