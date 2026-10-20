"""Minute-chart source limits, completed-session aggregation and isolation."""
from datetime import datetime
import json
import time

import pytest

from app.core import minute_chart as module
from app.core.minute_chart import (
    MinuteChartService, MinuteChartUnavailable, SHANGHAI,
    _normalize_bars, aggregate_120min,
)


def raw(stamp="2026-09-30 10:30", **changes):
    return {"bar_time": stamp, "open": 10, "high": 12, "low": 9, "close": 11,
            "volume": 100, "amount": 1000, **changes}


def now():
    return datetime(2026, 10, 3, 15, 1, tzinfo=SHANGHAI)


def test_120_aggregates_separate_complete_sessions():
    bars = _normalize_bars([raw(f"2026-09-30 {hour}") for hour in
                            ["10:30", "11:30", "14:00", "15:00"]], now(), None, 60)
    result = aggregate_120min(bars)
    assert [row["trade_date"] for row in result] == [
        "2026-09-30 11:30", "2026-09-30 15:00"]
    assert result[0]["volume"] == 200
    assert result[0]["amount"] == 2000
    assert result[0]["high"] == 12


def test_120_skips_missing_half_and_never_crosses_lunch_or_dates():
    bars = _normalize_bars([raw("2026-09-29 15:00"), raw("2026-09-30 10:30"),
                            raw("2026-09-30 14:00")], now(), None, 60)
    assert aggregate_120min(bars) == []


def test_only_completed_bars_with_publication_buffer():
    at = datetime(2026, 9, 30, 11, 30, 15, tzinfo=SHANGHAI)
    bars = _normalize_bars([raw("2026-09-30 10:30"), raw("2026-09-30 11:30"),
                            raw("2026-09-30 12:30")], at, None, 60)
    assert len(bars) == 1
    assert bars[0]["trade_date"].endswith("10:30")


@pytest.mark.parametrize("changes", [{"high": 8}, {"close": float("nan")},
                                     {"volume": -1}, {"bar_time": "NaT"},
                                     {"amount": None}, {"open": 0}])
def test_bad_source_data_blocks_chart(changes):
    with pytest.raises(MinuteChartUnavailable):
        _normalize_bars([raw(**changes)], now(), None, 60)


def test_input_is_bounded():
    with pytest.raises(MinuteChartUnavailable):
        _normalize_bars([raw()] * 1041, now(), None, 60)


def test_service_stock_source_cache_and_no_extra_requests(monkeypatch):
    calls = []
    def source(*args):
        calls.append(args)
        return [raw()], "tdx_stock", "tdx_native"
    monkeypatch.setattr(module, "_tdx_bars", source)
    service = MinuteChartService()
    first = service.load("stock", "600519", "30min", "2026-09-30")
    second = service.load("stock", "600519", "30min", "2026-09-30")
    assert first["ready"] and second["cache_hit"]
    assert first["bars"][0]["volume"] == 100
    assert first["volume_unit"] == "tdx_native"
    assert len(calls) == 1
    second["bars"][0]["close"] = 999
    assert service.load("stock", "600519", "30min", "2026-09-30")["bars"][0]["close"] == 11


def test_sector_volume_conversion_and_120_source_bounds(monkeypatch):
    calls = []
    def source(*args):
        calls.append(args)
        return [raw("2026-09-30 10:30"), raw("2026-09-30 11:30")], "eastmoney_bk", "lots"
    monkeypatch.setattr(module, "_eastmoney_bars", source)
    result = MinuteChartService().load("sector", "BK1575", "120min", "2026-09-30", 520,
                                       sector_type="Industry")
    assert result["ready"]
    assert result["bars"][0]["volume"] == 20000
    assert result["volume_unit"] == "shares"
    assert calls[0][1:3] == (60, 1040)


def test_explicit_missing_date_never_falls_back(monkeypatch):
    monkeypatch.setattr(module, "_tdx_bars", lambda *args: ([raw()], "tdx_stock", "tdx_native"))
    service = MinuteChartService()
    result = service.load("stock", "600519", "30min", "2026-09-29")
    assert not result["ready"] and result["bars"] == []
    assert service.resource_stats()["cache_entries"] == 0


def test_source_failure_and_bse_do_not_cache_or_guess(monkeypatch):
    calls = []
    def source(*args):
        calls.append(args)
        raise OSError("network down")
    monkeypatch.setattr(module, "_tdx_bars", source)
    service = MinuteChartService()
    assert not service.load("stock", "600519", "30min")["ready"]
    assert not service.load("stock", "920001", "30min")["ready"]
    assert not service.load("index", "899050", "30min", market=1)["ready"]
    assert len(calls) == 1 and service.resource_stats()["cache_entries"] == 0


@pytest.mark.parametrize("kwargs", [dict(kind="bad"), dict(symbol="../../x"),
                                     dict(period="1min"), dict(limit=521),
                                     dict(limit=True), dict(end_date="bad"),
                                     dict(kind="index", market=None)])
def test_request_validation(kwargs):
    request = dict(kind="stock", symbol="600519", period="30min")
    request.update(kwargs)
    with pytest.raises(ValueError):
        MinuteChartService().load(**request)


def test_busy_service_does_not_queue_network_calls(monkeypatch):
    def forbidden(*args):
        pytest.fail("busy request must not touch the source")
    monkeypatch.setattr(module, "_tdx_bars", forbidden)
    service = MinuteChartService()
    service._slots.acquire()
    service._slots.acquire()
    try:
        assert "繁忙" in service.load("stock", "600519", "30min")["message"]
    finally:
        service._slots.release()
        service._slots.release()


def test_lru_cache_entries_and_memory_are_bounded(monkeypatch):
    monkeypatch.setattr(module, "_tdx_bars", lambda *args: ([raw()], "tdx_stock", "tdx_native"))
    service = MinuteChartService()
    for number in range(40):
        assert service.load("stock", f"600{number:03}", "30min", "2026-09-30")["ready"]
    stats = service.resource_stats()
    assert stats["cache_entries"] == 32
    assert stats["cache_bytes"] <= 8 * 1024 * 1024
    assert stats["max_concurrent_requests"] == 2


def test_tdx_retries_new_isolated_stream_and_two_pages(monkeypatch):
    from pytdx import hq
    clients = []
    class Socket:
        def settimeout(self, timeout):
            assert 0 < timeout <= 2
    class Api:
        def __init__(self, **kwargs):
            self.client = Socket()
            self.index = len(clients)
            self.calls = []
            self.closed = False
            clients.append(self)
        def connect(self, *args, **kwargs):
            return True
        def disconnect(self):
            self.closed = True
        def get_security_bars(self, category, market, symbol, offset, count):
            self.calls.append((offset, count))
            if self.index == 0:
                raise OSError("broken stream")
            return [dict(datetime="2026-09-30 10:30", open=10, high=12, low=9,
                         close=11, vol=100, amount=1000)] * count
    monkeypatch.setattr(hq, "TdxHq_API", Api)
    rows, _, _ = module._tdx_bars("stock", "600519", 1, 60, 1040, time.monotonic()+12)
    assert len(rows) == 1040
    assert len(clients) == 2 and all(client.closed for client in clients)
    assert clients[1].calls == [(0, 800), (800, 240)]


def test_eastmoney_checks_returned_code_and_closes(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield json.dumps({"data": {"code": "BK0000", "klines": []}}).encode()
    class Session:
        def __init__(self):
            self.proxies = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            assert kwargs["params"]["klt"] == "30"
            assert kwargs["timeout"] <= 2
            return Response()
    monkeypatch.setattr(module.requests, "Session", Session)
    with pytest.raises(MinuteChartUnavailable, match="代码不一致"):
        module._eastmoney_bars("BK1575", 30, 260, time.monotonic()+12)


def test_official_holiday_weekday_is_not_a_completed_trading_bar():
    # Mid-Autumn closure is Friday, so a weekday-only guard is insufficient.
    bars = _normalize_bars([raw("2026-09-25 10:30"), raw("2026-09-30 10:30")],
                           now(), None, 60)
    assert [row["trade_date"] for row in bars] == ["2026-09-30 10:30"]


def test_shared_service_cold_start_constructs_once_under_concurrency(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    constructed = []
    start = threading.Barrier(8)
    original = module.MinuteChartService

    class SlowService(original):
        _shared_instance = None
        _shared_lock = threading.Lock()

        def __init__(self):
            time.sleep(0.02)
            constructed.append(self)
            super().__init__()

    monkeypatch.setattr(module, "MinuteChartService", SlowService)

    def obtain():
        start.wait(timeout=2)
        return module._service()

    with ThreadPoolExecutor(max_workers=8) as pool:
        instances = list(pool.map(lambda _: obtain(), range(8)))
    assert len(constructed) == 1
    assert len({id(instance) for instance in instances}) == 1


def test_eastmoney_minute_session_ignores_unrelated_environment_proxy(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield json.dumps({"data": {"code": "BK1575", "klines": [
                "2026-09-30 10:30,10,11,12,9,100,1000"]}}).encode()

    class Session:
        trust_env = True
        def __init__(self):
            self.proxies = {"https": "http://unrelated-local-proxy"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            assert self.trust_env is False
            assert self.proxies == {}
            return Response()

    monkeypatch.setattr(module.requests, "Session", Session)
    rows, _, _ = module._eastmoney_bars("BK1575", 60, 522, time.monotonic()+10)
    assert rows[0]["bar_time"] == "2026-09-30 10:30"


def test_source_connection_failure_has_specific_safe_message(monkeypatch):
    def source(*args):
        raise module.requests.ConnectionError("remote connection refused")
    monkeypatch.setattr(module, "_eastmoney_bars", source)
    result = MinuteChartService().load("sector", "BK1575", "120min")
    assert not result["ready"]
    assert result["message"] == "分钟行情源连接中断，当前不可用"
    assert result["bars"] == []
