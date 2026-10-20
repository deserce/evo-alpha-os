"""Bounded intraday transport recovery without live network calls."""
from data_job.core import tdx_adapter as module


class Api:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.closed = False
        self.requests = 0
        self.probes = 0

    def disconnect(self):
        self.closed = True

    def get_security_quotes(self, symbols):
        self.probes += 1
        return None

    def get_security_bars(self, *args):
        self.requests += 1
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def adapter(api):
    obj = module.TdxAdapter.__new__(module.TdxAdapter)
    obj.api = api
    obj.connected = True
    obj.current_server = ('bad', 7709)
    obj.success_count = obj.fail_count = 0
    return obj


def test_healthy_connection_has_no_extra_probe():
    api = Api([])
    obj = adapter(api)
    assert obj._ensure_connected()
    assert api.probes == 0


def test_second_empty_response_closes_connection():
    api = Api([[], []])
    obj = adapter(api)
    obj._connect = lambda: True
    assert obj.get_recent_stock_bars('000792') is None
    assert not obj.connected
    assert api.closed
    assert api.requests == 2


def test_exception_retries_once_on_fresh_connection():
    old = Api([RuntimeError('broken stream')])
    fresh = Api([[{'datetime': '2026-09-22 15:00', 'close': 20}]])
    obj = adapter(old)
    def reconnect():
        old.disconnect()
        obj.api = fresh
        obj.connected = True
        return True
    obj._connect = reconnect
    assert obj.get_recent_stock_bars('000792') is not None
    assert old.closed
    assert old.requests == fresh.requests == 1


def test_reconnect_rotates_server_and_bounds_timeout(monkeypatch):
    monkeypatch.setattr(module, 'TDX_SERVERS', [('bad', 7709), ('good', 7709)])
    calls = []
    class Client:
        def __init__(self, **kwargs):
            pass
        def connect(self, host, port, **kwargs):
            calls.append((host, kwargs))
            return True
        def disconnect(self):
            pass
    obj = adapter(Api([]))
    obj.TdxHq_API = Client
    assert obj._connect()
    assert calls == [('good', {'time_out': 2.0})]


def test_failed_connections_close_and_stop_after_three_attempts(monkeypatch):
    monkeypatch.setattr(module, 'TDX_SERVERS', [(str(i), 7709) for i in range(8)])
    clients = []
    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
        def connect(self, host, port, **kwargs):
            raise TimeoutError('offline')
        def disconnect(self):
            self.closed = True
    obj = adapter(Api([]))
    obj.TdxHq_API = Client
    assert not obj._connect()
    assert len(clients) == 3
    assert all(client.closed for client in clients)
    assert not obj.connected
