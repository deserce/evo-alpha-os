from data_core.tdx_exhq import ExhqServer, discover_exhq_servers, parse_max_servers, probe_bse_tdx


class FakeExhqApi:
    def __init__(self, *, connect_error=None, bars=None, markets=None):
        self.connect_error = connect_error
        self.bars = bars or []
        self.markets = markets if markets is not None else [{"market": 64, "name": "北交所"}]

    def connect(self, host, port, time_out=2.0):
        if self.connect_error:
            raise self.connect_error
        return True

    def get_markets(self):
        return self.markets

    def get_instrument_bars(self, category, market, code, start=0, count=2):
        return self.bars if code == "920002" else []

    def to_df(self, raw):
        import pandas as pd

        return pd.DataFrame(raw)

    def disconnect(self):
        return None


def test_parse_max_servers_accepts_all():
    assert parse_max_servers("all") is None
    assert parse_max_servers("2") == 2
    assert parse_max_servers(1) == 1


def test_discover_exhq_servers_env_first(monkeypatch):
    monkeypatch.setenv("TDX_EXHQ_SERVERS", "1.1.1.1:7727:test_one")

    servers = discover_exhq_servers(include_future_ip=False)

    assert servers[0] == ExhqServer("1.1.1.1", 7727, "test_one")


def test_probe_bse_tdx_records_successful_server_and_market():
    bars = [
        {
            "datetime": "2026-05-25 00:00",
            "open": 20.0,
            "high": 21.0,
            "low": 19.0,
            "close": 20.5,
            "trade": 1000,
            "amount": 20500.0,
        }
    ]
    result = probe_bse_tdx(
        samples=("920002",),
        timeout=0.1,
        max_servers=1,
        servers=(ExhqServer("127.0.0.1", 7727, "fake_exhq"),),
        api_factory=lambda: FakeExhqApi(bars=bars),
    )

    assert result["status"] == "PASS"
    assert result["success_count"] == 1
    assert result["success_rate"] == 1.0
    assert result["best_server"]["name"] == "fake_exhq"
    assert result["samples"]["920002"]["market"] == 64


def test_probe_bse_tdx_records_timeout_failure():
    result = probe_bse_tdx(
        samples=("920002",),
        timeout=0.1,
        max_servers=1,
        servers=(ExhqServer("127.0.0.1", 7727, "fake_timeout"),),
        api_factory=lambda: FakeExhqApi(connect_error=TimeoutError("timed out")),
    )

    assert result["status"] == "FAIL"
    assert result["success_count"] == 0
    assert result["fail_count"] == 1
    assert "TimeoutError" in result["error_message"]
