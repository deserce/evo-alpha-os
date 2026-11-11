"""
测试 mootdx K线分页与前复权计算。
"""

import importlib.util
import json
import socket
import sqlite3
import struct
import sys
import unittest
from datetime import date
from pathlib import Path
from queue import Empty
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, ".")

from data_job.core.mootdx_adapter import MARKET_SH, MARKET_SZ, MootdxAdapter
from data_job.core.mootdx_qfq import (
    apply_mootdx_qfq,
    compute_turnover_rate,
    detect_volume_scale,
)

_script_path = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "standalone"
    / "collect_stock_kline_mootdx_qfq.py"
)
_spec = importlib.util.spec_from_file_location("collect_stock_kline_mootdx_qfq", _script_path)
_module = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(_module)
load_symbols = _module.load_symbols
seed_incremental_pct_chg = _module.seed_incremental_pct_chg
fetch_one_symbol = _module.fetch_one_symbol
init_target_table = _module.init_target_table
init_raw_target_table = _module.init_raw_target_table
save_dual_batch = _module.save_dual_batch
validate_target_tables = _module.validate_target_tables


@pytest.fixture(autouse=True)
def isolate_node_state_and_network(tmp_path, monkeypatch):
    """No test may touch the real health cache or open a network connection."""
    monkeypatch.setattr(MootdxAdapter, "SERVER_STATE_PATH", tmp_path / "nodes.json")

    def reject_network(*args, **kwargs):
        raise AssertionError("network access is forbidden in these offline tests")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    connect = sqlite3.connect

    def memory_database_only(database, *args, **kwargs):
        if database != ":memory:":
            raise AssertionError("only in-memory test databases are permitted")
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", memory_database_only)


@pytest.fixture
def isolated_minute_source(monkeypatch):
    nodes = [(f"192.0.2.{i}", 7709, f"minute{i}") for i in range(1, 4)]
    calls, clients, openings = [], [], []
    behavior = [lambda host, kind, category, market, symbol, offset, count: [
        {"datetime": "2026-11-02 15:00", "code": symbol, "close": 11.0}
    ] * count]

    class Client:
        last_transaction_failed = False

        def __init__(self, host):
            self.host = host
            self.closed = False
            self.client = SimpleNamespace(close=lambda: None)

        def disconnect(self):
            self.closed = True

        def get_security_bars(self, *args):
            return self.bars("stock", *args)

        def get_index_bars(self, *args):
            return self.bars("index", *args)

        def bars(self, kind, category, market, symbol, offset, count):
            calls.append((self.host, kind, category, market, symbol, offset, count))
            return behavior[0](self.host, kind, category, market, symbol, offset, count)

    def opening(self, server, backend, deadline):
        openings.append((server, backend, deadline))
        client = Client(server[0])
        clients.append(client)
        return SimpleNamespace(client=client) if backend == "mootdx" else client

    def forbidden(*args, **kwargs):
        pytest.fail("minute reads must not discover, probe, or persist state")

    monkeypatch.setattr(MootdxAdapter, "SERVERS", nodes)
    monkeypatch.setattr(MootdxAdapter, "_open_api", opening)
    for name in ("__init__", "_connect", "_validate_node", "_write_server_state"):
        monkeypatch.setattr(MootdxAdapter, name, forbidden)
    return SimpleNamespace(nodes=nodes, calls=calls, clients=clients,
                           openings=openings, behavior=behavior)


def read_minute_fixture(**changes):
    import time

    request = dict(kind="stock", symbol="000792", market=0, period="30min",
                   count=4, deadline=time.monotonic() + 10)
    request.update(changes)
    return MootdxAdapter.read_isolated_minute_bars(**request)


def test_isolated_minutes_prefer_cached_healthy_node_without_disk_writes(isolated_minute_source):
    import time

    source = isolated_minute_source
    state = {"host": source.nodes[1][0], "port": 7709, "backend": "mootdx",
             "nodes": {f"{source.nodes[0][0]}:7709": {
                 "failures": 1, "cooldown_until": time.time() + 600}}}
    path = MootdxAdapter.SERVER_STATE_PATH
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    rows = read_minute_fixture()
    assert rows[-1]["datetime"] == "2026-11-02 15:00"
    assert [item[0][0] for item in source.openings] == [source.nodes[1][0]]
    assert all(client.closed for client in source.clients)
    assert path.read_bytes() == before


def test_isolated_minutes_retry_broken_stream_once_and_remember_bad_node(isolated_minute_source):
    source = isolated_minute_source
    normal = source.behavior[0]

    def response(host, *args):
        if host == source.nodes[0][0]:
            raise ConnectionError("broken stream")
        return normal(host, *args)

    source.behavior[0] = response
    assert read_minute_fixture()
    assert read_minute_fixture()
    assert [item[0][0] for item in source.openings] == [
        source.nodes[0][0], source.nodes[1][0], source.nodes[1][0]]
    assert all(client.closed for client in source.clients)
    assert not MootdxAdapter.SERVER_STATE_PATH.exists()


def test_isolated_minute_health_does_not_change_daily_selection(isolated_minute_source):
    source = isolated_minute_source
    normal = source.behavior[0]

    def response(host, *args):
        if host == source.nodes[0][0]:
            raise ConnectionError("minute-only failure")
        return normal(host, *args)

    source.behavior[0] = response
    assert read_minute_fixture()
    daily = MootdxAdapter.__new__(MootdxAdapter)
    assert daily._ordered_servers()[0] == source.nodes[0]
    assert daily._health_state().get("success_epoch") is None


def test_isolated_minutes_observe_changed_daily_node_file(isolated_minute_source):
    source = isolated_minute_source
    path = MootdxAdapter.SERVER_STATE_PATH
    path.write_text(json.dumps({"host": source.nodes[0][0], "port": 7709}))
    assert read_minute_fixture()
    path.write_text(json.dumps({"host": source.nodes[2][0], "port": 7709,
                                "backend": "mootdx", "last_success_at": "new daily run"}))
    assert read_minute_fixture()
    assert [opening[0] for opening in source.openings] == [source.nodes[0], source.nodes[2]]


@pytest.mark.parametrize("disk_cooldown", [0, 2300.0])
def test_daily_node_file_refresh_preserves_active_minute_cooldown(
    isolated_minute_source, monkeypatch, disk_cooldown,
):
    source = isolated_minute_source
    normal = source.behavior[0]
    clock = [1000.0]
    monkeypatch.setattr("data_job.core.mootdx_adapter.time.time", lambda: clock[0])

    def response(host, *args):
        if host == source.nodes[0][0]:
            raise ConnectionError("minute-only failure")
        return normal(host, *args)

    source.behavior[0] = response
    assert read_minute_fixture()
    reader = MootdxAdapter.__new__(MootdxAdapter)
    reader._read_only_health = True
    key = f"{source.nodes[0][0]}:7709"
    original_cooldown = reader._health_state()["nodes"][key]["cooldown_until"]
    path = MootdxAdapter.SERVER_STATE_PATH
    disk_nodes = {key: {"failures": 2, "cooldown_until": disk_cooldown}} if disk_cooldown else {}
    path.write_text(json.dumps({"host": source.nodes[1][0], "port": 7709, "nodes": disk_nodes}))
    assert read_minute_fixture()
    assert source.nodes[0] not in reader._ordered_servers()
    merged_cooldown = max(original_cooldown, disk_cooldown)
    assert reader._health_state()["nodes"][key]["cooldown_until"] == merged_cooldown
    clock[0] = merged_cooldown + 1
    path.write_text(json.dumps({"host": source.nodes[1][0], "port": 7709,
                                "nodes": {}, "last_success_at": "later update"}))
    assert source.nodes[0] in reader._ordered_servers()


def test_isolated_minutes_second_page_failure_restarts_from_zero(isolated_minute_source):
    source = isolated_minute_source

    def response(host, kind, category, market, symbol, offset, count):
        if host == source.nodes[0][0] and offset:
            raise OSError("second page failed")
        return [{"code": symbol, "close": 10 if host == source.nodes[0][0] else 20}] * count

    source.behavior[0] = response
    rows = read_minute_fixture(period="60min", count=1040)
    assert len(rows) == 1040 and {row["close"] for row in rows} == {20}
    assert [(call[0], call[-2], call[-1]) for call in source.calls] == [
        (source.nodes[0][0], 0, 800), (source.nodes[0][0], 800, 240),
        (source.nodes[1][0], 0, 800), (source.nodes[1][0], 800, 240)]
    assert all(client.closed for client in source.clients)


def test_isolated_minutes_index_uses_explicit_market_and_index_method(isolated_minute_source):
    source = isolated_minute_source
    read_minute_fixture(kind="index", symbol="000001", market=1, period="60min")
    assert source.calls[0][1:5] == ("index", 3, 1, "000001")


def test_isolated_minutes_validate_before_promoting_and_fail_over(isolated_minute_source):
    source = isolated_minute_source
    validated = []

    def validate(rows):
        validated.append(rows)
        if len(validated) == 1:
            raise ValueError("invalid minute OHLC")

    assert read_minute_fixture(validate=validate)
    assert len(validated) == len(source.openings) == 2
    assert all(client.closed for client in source.clients)


def test_isolated_empty_minutes_do_not_quarantine_or_scan_all_nodes(isolated_minute_source):
    source = isolated_minute_source
    source.behavior[0] = lambda *args: []
    with pytest.raises(ConnectionError):
        read_minute_fixture()
    assert len(source.openings) == 2
    adapter = MootdxAdapter.__new__(MootdxAdapter)
    assert len(adapter._ordered_servers()) == 3
    assert all(client.closed for client in source.clients)


def test_isolated_minutes_total_deadline_is_not_reset(isolated_minute_source, monkeypatch):
    source = isolated_minute_source
    clock = [10.0]
    monkeypatch.setattr("data_job.core.mootdx_adapter.time.monotonic", lambda: clock[0])
    normal = source.behavior[0]

    def slow(*args):
        clock[0] += 6
        return normal(*args)

    source.behavior[0] = slow
    with pytest.raises(TimeoutError):
        read_minute_fixture(deadline=20.0)
    assert len(source.openings) <= 2
    assert all(opening[2] <= 20 for opening in source.openings)
    assert source.openings[0][2] <= 15
    assert all(client.closed for client in source.clients)


@pytest.mark.parametrize("changes", [
    {"kind": "sector"}, {"period": "daily"}, {"count": 1041}, {"count": True},
    {"count": 0}, {"symbol": "../bad"}, {"market": 2}, {"market": True},
    {"deadline": float("nan")},
])
def test_isolated_minutes_reject_invalid_request_before_connecting(isolated_minute_source, changes):
    with pytest.raises(ValueError):
        read_minute_fixture(**changes)
    assert not isolated_minute_source.openings


class TestMootdxNodeFailover(unittest.TestCase):
    def adapter(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = None
        adapter.backend = None
        adapter.current_server = None
        adapter.connected = False
        adapter.Quotes = object()
        adapter.timeout = 1
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None
        adapter.SERVERS = [
            (f"192.0.2.{i}", 7709, f"node{i}") for i in range(1, 10)
        ]
        return adapter

    @staticmethod
    def bars(price=10.0):
        today = pd.Timestamp.now().normalize()
        return pd.DataFrame({
            "datetime": [today - pd.Timedelta(days=1), today],
            "open": [price, price], "high": [price + 1, price + 1],
            "low": [price - 1, price - 1], "close": [price, price],
            "volume": [100.0, 110.0], "amount": [1000.0, 1100.0],
        })

    def test_failed_nodes_are_shared_and_cool_down_for_ten_minutes(self):
        first = self.adapter()
        second = self.adapter()
        node = first.SERVERS[0]
        with patch("data_job.core.mootdx_adapter.time.time", return_value=1000):
            first._mark_server_failure(node)
            self.assertNotIn(node, second._ordered_servers())
        with patch("data_job.core.mootdx_adapter.time.time", return_value=1599):
            self.assertNotIn(node, second._ordered_servers())
        with patch("data_job.core.mootdx_adapter.time.time", return_value=1601):
            self.assertIn(node, second._ordered_servers())
        payload = json.loads(first.SERVER_STATE_PATH.read_text())
        self.assertEqual(payload["nodes"]["192.0.2.1:7709"]["cooldown_until"], 1600)

    def test_pool_managed_disconnected_stream_never_discovers_from_worker(self):
        adapter = self.adapter()
        adapter._pool_managed = True
        with patch.object(adapter, "_connect", return_value=True) as connect:
            self.assertFalse(adapter._ensure_connected())
        connect.assert_not_called()

    def test_pool_managed_quarantined_stream_closes_without_discovery(self):
        adapter = self.adapter()
        adapter._pool_managed = True
        adapter.api, adapter.backend, adapter.connected = object(), "pytdx", True
        adapter._active_server = adapter.SERVERS[0]
        adapter._mark_server_failure(adapter.SERVERS[0])
        with patch.object(adapter, "_connect", return_value=True) as connect:
            self.assertFalse(adapter._ensure_connected())
        connect.assert_not_called()
        self.assertIsNone(adapter.api)

    def test_pool_managed_request_closes_failed_stream_without_nested_retry(self):
        adapter = self.adapter()
        adapter._pool_managed = True
        adapter._active_server = adapter.SERVERS[0]
        calls = []

        def operation():
            calls.append(1)
            adapter.last_failure_kind = "connectivity"
            return None

        with patch.object(adapter, "_discard_connection") as discard:
            self.assertIsNone(adapter._request_with_failover(operation))
        self.assertEqual(calls, [1])
        discard.assert_called_once_with()

    def test_round_visits_at_most_six_and_next_round_rotates(self):
        adapter = self.adapter()
        calls = []

        def fail(server, backend, deadline):
            calls.append(server)
            raise ConnectionError("offline")

        with patch.object(adapter, "_open_api", side_effect=fail):
            self.assertFalse(adapter._connect())
            self.assertEqual(len(calls), 6)
            self.assertFalse(adapter._connect())
        self.assertEqual(len(calls), 9)
        self.assertEqual(len(set(calls)), 9)

    def test_round_stops_at_thirty_second_deadline(self):
        adapter = self.adapter()
        clock = [0.0]

        def slow_failure(server, backend, deadline):
            self.assertEqual(deadline, 30.0)
            clock[0] += 10.0
            raise TimeoutError("deadline")

        with patch("data_job.core.mootdx_adapter.time.monotonic", side_effect=lambda: clock[0]):
            with patch.object(adapter, "_open_api", side_effect=slow_failure) as opening:
                self.assertFalse(adapter._connect())
        self.assertEqual(opening.call_count, 3)

    def test_probe_closes_and_business_connection_has_no_probe_or_success_cache(self):
        adapter = self.adapter()
        adapter.SERVERS = adapter.SERVERS[:1]
        owner = self

        class API:
            closed = False
            calls = 0

            def bars(self, **kwargs):
                self.calls += 1
                return owner.bars(price=10.0 + kwargs["market"])

            def close(self):
                self.closed = True

        probe, business = API(), API()
        with patch.object(adapter, "_open_api", side_effect=[probe, business]):
            self.assertTrue(adapter._connect())
        self.assertTrue(probe.closed)
        self.assertEqual(probe.calls, 3)
        self.assertEqual(business.calls, 0)
        self.assertNotIn("host", adapter._load_server_state())
        self.assertIsNotNone(adapter.get_stock_kline("600519", adjust="none", max_bars=2))
        self.assertEqual(adapter._load_server_state()["host"], "192.0.2.1")

    def test_validation_checks_dates_ohlc_and_all_markets(self):
        adapter = self.adapter()
        good = self.bars()
        for bad in (
            good.assign(datetime="1999-01-01"),
            good.assign(datetime="2099-01-01"),
            good.assign(high=1),
            good.assign(close=float("inf")),
            good.drop(columns=["low"]),
        ):
            with self.subTest(columns=list(bad.columns)):
                with self.assertRaises(ConnectionError):
                    adapter._validate_probe_bars(bad)

    def test_valid_empty_business_response_does_not_quarantine_node(self):
        adapter = self.adapter()
        adapter.api = FakeQuotesAPI({})
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter._active_server = adapter.SERVERS[0]
        with patch.object(adapter, "_mark_server_failure") as failed:
            self.assertIsNone(adapter.get_stock_kline("600519", adjust="none"))
        self.assertEqual(adapter.last_failure_kind, "no_data")
        failed.assert_not_called()
        self.assertTrue(adapter.connected)

    def test_protocol_failure_switches_node_once_without_partial_history(self):
        adapter = self.adapter()
        adapter.api = FakeQuotesAPI({})
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter._active_server = adapter.SERVERS[0]
        good = self.bars()

        def reconnect():
            adapter.api = FakeQuotesAPI({0: good})
            adapter.connected = True
            adapter.backend = "mootdx"
            adapter._active_server = adapter.SERVERS[1]
            return True

        with patch.object(adapter, "_fetch_bars_data", side_effect=[struct.error("short"), good]):
            with patch.object(adapter, "_connect", side_effect=reconnect) as connect:
                result = adapter.get_stock_kline("600519", adjust="none", max_bars=2)
        self.assertIsNotNone(result)
        self.assertEqual(connect.call_count, 1)
        self.assertNotIn(adapter.SERVERS[0], adapter._ordered_servers())
        self.assertEqual(adapter._load_server_state()["host"], "192.0.2.2")

    def test_two_protocol_failures_do_not_retry_a_third_time(self):
        adapter = self.adapter()
        adapter.api = object()
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter._active_server = adapter.SERVERS[0]

        def reconnect():
            adapter.api = object()
            adapter.connected = True
            adapter.backend = "mootdx"
            adapter._active_server = adapter.SERVERS[1]
            return True

        with patch.object(adapter, "_fetch_bars_data", side_effect=struct.error("short")) as fetch:
            with patch.object(adapter, "_connect", side_effect=reconnect) as connect:
                self.assertIsNone(adapter.get_stock_kline("600519", adjust="none"))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(connect.call_count, 1)

    def test_auto_entry_never_calls_factory_or_scans(self):
        adapter = self.adapter()
        with patch.object(adapter, "_connect_manual", return_value=False) as manual:
            self.assertFalse(adapter._connect_mootdx_auto())
        manual.assert_called_once_with()

    def test_pool_startup_failure_is_not_multiplied_by_slots_or_retries(self):
        class FailedAdapter:
            def __init__(self, **kwargs):
                pass

            def is_available(self):
                return False

            def disconnect(self):
                pass

        with patch.object(_module, "MootdxAdapter", side_effect=FailedAdapter) as constructor:
            pool = _module.AdapterPool(size=8, timeout=1, max_retries=9, retry_delay=10)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(pool.available, 0)
        pool.close()

    def test_pool_rejects_unbounded_size(self):
        with self.assertRaises(ValueError):
            _module.AdapterPool(size=0, timeout=1, max_retries=1, retry_delay=0)
        with self.assertRaises(ValueError):
            _module.AdapterPool(size=100000, timeout=1, max_retries=1, retry_delay=0)

    def test_cooldown_survives_a_new_process_registry(self):
        adapter = self.adapter()
        adapter._mark_server_failure(adapter.SERVERS[0])
        with patch.object(MootdxAdapter, "_health_states", {}):
            self.assertNotIn(adapter.SERVERS[0], self.adapter()._ordered_servers())

    def test_parallel_failure_reports_do_not_multiply_the_initial_cooldown(self):
        adapter = self.adapter()
        with patch("data_job.core.mootdx_adapter.time.time", return_value=1000):
            adapter._mark_server_failure(adapter.SERVERS[0])
        with patch("data_job.core.mootdx_adapter.time.time", return_value=1001):
            adapter._mark_server_failure(adapter.SERVERS[0])
        health = adapter._load_server_state()["nodes"]["192.0.2.1:7709"]
        self.assertEqual(health, {"failures": 1, "cooldown_until": 1600})

    def test_socket_is_closed_even_if_library_disconnect_raises(self):
        class Sock:
            closed = False

            def close(self):
                self.closed = True

        class Client:
            def __init__(self, sock):
                self.client = sock

            def disconnect(self):
                self.client = None
                raise OSError("shutdown on an already broken stream")

        sock = Sock()
        MootdxAdapter._close_api(Client(sock), "pytdx")
        self.assertTrue(sock.closed)

    def test_auto_endpoint_cannot_be_saved_as_business_success(self):
        adapter = self.adapter()
        adapter._save_server_state(host="auto", port=0, name="auto", backend="mootdx")
        self.assertFalse(adapter.SERVER_STATE_PATH.exists())

    def test_corrupt_and_oversized_health_cache_do_not_break_selection(self):
        adapter = self.adapter()
        for payload in ("{bad", '{"nodes": [], "cursor": "oops"}', " " * 65537):
            adapter.SERVER_STATE_PATH.write_text(payload)
            with patch.object(MootdxAdapter, "_health_states", {}):
                self.assertEqual(len(adapter._ordered_servers()), 9)
                adapter._mark_server_failure(adapter.SERVERS[0])
                self.assertEqual(len(adapter._ordered_servers()), 8)

    def test_shared_bad_node_is_not_reused_by_another_pool_stream(self):
        adapter = self.adapter()
        other = self.adapter()
        api = FakeQuotesAPI({})
        other.api, other.connected, other.backend = api, True, "mootdx"
        other._active_server = other.SERVERS[0]
        adapter._mark_server_failure(adapter.SERVERS[0])
        with patch.object(other, "_connect", return_value=False) as connect:
            self.assertFalse(other._ensure_connected())
        connect.assert_called_once_with()
        self.assertIsNone(other.api)

    def test_empty_capability_probe_is_closed_but_not_quarantined(self):
        adapter = self.adapter()
        adapter.SERVERS = adapter.SERVERS[:1]

        class EmptyAPI:
            closed = False

            def bars(self, **kwargs):
                return pd.DataFrame()

            def close(self):
                self.closed = True

        api = EmptyAPI()
        with patch.object(adapter, "_open_api", return_value=api):
            with patch.object(adapter, "_mark_server_failure") as failure:
                self.assertFalse(adapter._connect())
        self.assertTrue(api.closed)
        failure.assert_not_called()

    def test_qfq_nested_xdxr_failure_restarts_whole_request_only_once(self):
        adapter = self.adapter()
        good = self.bars()
        class API:
            closed = False
            bars_calls = 0
            xdxr_calls = 0

            def bars(self, **kwargs):
                self.bars_calls += 1
                return good.copy()

            def xdxr(self, **kwargs):
                self.xdxr_calls += 1
                raise struct.error("truncated action response")

            def close(self):
                self.closed = True

        first, second = API(), API()
        adapter.api, adapter.connected, adapter.backend = first, True, "mootdx"
        adapter._active_server = adapter.SERVERS[0]

        def reconnect():
            adapter.api, adapter.connected, adapter.backend = second, True, "mootdx"
            adapter._active_server = adapter.SERVERS[1]
            return True

        with patch.object(adapter, "_connect", side_effect=reconnect) as connect:
            self.assertIsNone(adapter.get_stock_kline("600519", adjust="qfq", max_bars=2))
        self.assertEqual(connect.call_count, 1)
        self.assertEqual((first.bars_calls, second.bars_calls), (1, 1))
        self.assertEqual((first.xdxr_calls, second.xdxr_calls), (1, 1))
        self.assertTrue(first.closed and second.closed)

    def test_index_second_page_failure_discards_partial_and_restarts_at_zero(self):
        adapter = self.adapter()
        first_page = pd.concat([self.bars()] * 400, ignore_index=True)
        final_page = self.bars(price=20.0)

        class API:
            closed = False

            def __init__(self, failing):
                self.failing = failing
                self.starts = []

            def index_bars(self, **kwargs):
                self.starts.append(kwargs["start"])
                if not self.failing:
                    return final_page
                if kwargs["start"] == 0:
                    return first_page
                raise struct.error("short index page")

            def close(self):
                self.closed = True

        first, second = API(True), API(False)
        adapter.api, adapter.connected, adapter.backend = first, True, "mootdx"
        adapter._active_server = adapter.SERVERS[0]

        def reconnect():
            adapter.api, adapter.connected, adapter.backend = second, True, "mootdx"
            adapter._active_server = adapter.SERVERS[1]
            return True

        with patch.object(adapter, "_connect", side_effect=reconnect):
            result = adapter.get_index_kline("000001", market=MARKET_SH, max_bars=1600)
        self.assertTrue(first.closed)
        self.assertEqual(first.starts, [0, 800])
        self.assertEqual(second.starts, [0])
        self.assertEqual(len(result), 2)
        self.assertTrue(result["close"].eq(20.0).all())

    def test_deadline_socket_checks_each_read_without_a_timer_thread(self):
        class Sock:
            reads = 0
            timeouts = []

            def settimeout(self, value):
                self.timeouts.append(value)

            def recv(self, count):
                self.reads += 1
                return b"ok"

        sock = Sock()
        wrapped = MootdxAdapter._DeadlineSocket(sock, timeout=10, deadline=30)
        with patch("data_job.core.mootdx_adapter.time.monotonic", return_value=29):
            self.assertEqual(wrapped.recv(2), b"ok")
        with patch("data_job.core.mootdx_adapter.time.monotonic", return_value=30):
            with self.assertRaises(TimeoutError):
                wrapped.recv(2)
        self.assertEqual(sock.timeouts, [1])
        self.assertEqual(sock.reads, 1)

    def test_pool_forks_without_rediscovery_and_never_blocks_on_duplicate_release(self):
        instances = []

        class Adapter:
            def __init__(self, **kwargs):
                self.closed = False
                instances.append(self)

            def is_available(self):
                return True

            def fork_connection(self, **kwargs):
                return Adapter()

            def disconnect(self):
                self.closed = True

        with patch.object(_module, "MootdxAdapter", side_effect=Adapter) as constructor:
            pool = _module.AdapterPool(size=3, timeout=1, max_retries=100, retry_delay=100)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(pool.available, 3)
        borrowed = [pool.acquire(timeout=0) for _ in range(3)]
        with self.assertRaises(Empty):
            pool.acquire(timeout=0)
        pool.release(borrowed[0])
        with self.assertRaises(ValueError):
            pool.release(borrowed[0])
        pool.close()
        with self.assertRaises(RuntimeError):
            pool.acquire(timeout=0)
        for adapter in borrowed[1:]:
            pool.release(adapter)
        self.assertTrue(all(adapter.closed for adapter in instances))
        self.assertFalse(pool._owned)

    def test_pool_close_wakes_a_waiting_acquire(self):
        class Adapter:
            def __init__(self, **kwargs):
                pass

            def is_available(self):
                return True

            def disconnect(self):
                pass

        with patch.object(_module, "MootdxAdapter", Adapter):
            pool = _module.AdapterPool(size=1, timeout=1, max_retries=1, retry_delay=0)
        borrowed = pool.acquire(timeout=0)
        ready, finished = Event(), Event()
        errors = []

        def waiter():
            ready.set()
            try:
                pool.acquire(timeout=30)
            except RuntimeError as exc:
                errors.append(str(exc))
            finally:
                finished.set()

        thread = Thread(target=waiter)
        thread.start()
        try:
            self.assertTrue(ready.wait(1))
            pool.close()
            self.assertTrue(finished.wait(1))
            self.assertEqual(errors, ["adapter pool is closed"])
        finally:
            pool.close()
            thread.join(timeout=1)
            pool.release(borrowed)
        self.assertFalse(thread.is_alive())


class FakeQuotesAPI:
    """用于测试分页行为的假 mootdx API。"""

    def __init__(self, pages):
        self.pages = pages
        self.starts = []

    def bars(self, symbol="000001", frequency=9, start=0, offset=800, **kwargs):
        self.starts.append(start)
        return self.pages.get(start, pd.DataFrame())


class TestMootdxQfq(unittest.TestCase):
    """测试前复权计算。"""

    def test_apply_mootdx_qfq_bonus_and_dividend(self):
        """送转和分红应当正确影响历史价格。"""
        raw_df = pd.DataFrame(
            {
                "symbol": ["000001", "000001", "000001"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "open": [10.0, 4.95, 5.50],
                "high": [10.0, 4.95, 5.50],
                "low": [10.0, 4.95, 5.50],
                "close": [10.0, 4.95, 5.50],
                "volume": [100.0, 100.0, 100.0],
                "amount": [1000.0, 495.0, 550.0],
            }
        )
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2024, 1, 2)],
                "category": [1],
                "fenhong": [1.0],
                "peigu": [0.0],
                "peigujia": [0.0],
                "songzhuangu": [10.0],
            }
        )

        result = apply_mootdx_qfq(raw_df, xdxr_df)

        self.assertEqual(result.loc[0, "trade_date"], date(2024, 1, 1))
        self.assertAlmostEqual(result.loc[0, "close"], 4.95, places=4)
        self.assertAlmostEqual(result.loc[1, "close"], 4.95, places=4)
        self.assertAlmostEqual(result.loc[2, "close"], 5.50, places=4)
        self.assertAlmostEqual(result.loc[1, "pct_chg"], 0.0, places=6)
        self.assertEqual(result.loc[0, "adjust_type"], "qfq")

    def test_apply_mootdx_qfq_without_actions_keeps_prices(self):
        """没有除权数据时，不应改写原始价格。"""
        raw_df = pd.DataFrame(
            {
                "symbol": ["000001", "000001"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "open": [10.0, 10.5],
                "high": [10.1, 10.6],
                "low": [9.9, 10.4],
                "close": [10.0, 10.5],
                "volume": [100.0, 120.0],
                "amount": [1000.0, 1260.0],
            }
        )

        result = apply_mootdx_qfq(raw_df, pd.DataFrame())

        self.assertAlmostEqual(result.loc[0, "close"], 10.0, places=6)
        self.assertAlmostEqual(result.loc[1, "close"], 10.5, places=6)
        self.assertAlmostEqual(result.loc[1, "pct_chg"], 5.0, places=6)
        self.assertEqual(result.loc[1, "adjust_type"], "qfq")

    def test_apply_mootdx_qfq_normalizes_tdx_zero_sentinels(self):
        raw_df = pd.DataFrame(
            {
                "trade_date": [date(2026, 7, 17)],
                "open": [34.0],
                "high": [34.0],
                "low": [34.0],
                "close": [34.0],
                "volume": [5.87747175411144e-39],
                "amount": [5.87747175411144e-39],
            }
        )

        result = apply_mootdx_qfq(raw_df, pd.DataFrame())

        self.assertEqual(result.loc[0, "volume"], 0.0)
        self.assertEqual(result.loc[0, "amount"], 0.0)

    def test_incremental_pct_chg_uses_last_stored_close_when_seed_bar_is_absent(self):
        data = pd.DataFrame(
            {
                "trade_date": [date(2026, 7, 17)],
                "close": [34.0],
                "pct_chg": [None],
            }
        )

        result = seed_incremental_pct_chg(data, previous_close=34.0)

        self.assertEqual(result.iloc[0]["pct_chg"], 0.0)


class TestTurnoverRate(unittest.TestCase):
    """测试换手率计算。"""

    def _kline(self, dates, close, volume, amount):
        return pd.DataFrame(
            {
                "symbol": ["000001"] * len(dates),
                "trade_date": dates,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": volume,
                "amount": amount,
            }
        )

    def test_detect_volume_scale_shares(self):
        """amount ≈ volume × close → volume 已是股，乘数 1.0。"""
        df = self._kline(
            [date(2024, 1, d) for d in (2, 3, 4, 5)],
            close=[10.0, 10.0, 10.0, 10.0],
            volume=[100.0, 200.0, 150.0, 180.0],
            amount=[1000.0, 2000.0, 1500.0, 1800.0],
        )
        self.assertEqual(detect_volume_scale(df), 1.0)

    def test_detect_volume_scale_hand(self):
        """amount ≈ volume × close × 100 → volume 是手，乘数 100.0。"""
        df = self._kline(
            [date(2024, 1, d) for d in (2, 3, 4, 5)],
            close=[10.0, 10.0, 10.0, 10.0],
            volume=[100.0, 200.0, 150.0, 180.0],
            amount=[100000.0, 200000.0, 150000.0, 180000.0],
        )
        self.assertEqual(detect_volume_scale(df), 100.0)

    def test_compute_turnover_rate_uses_xdxr_baseline(self):
        """首个 xdxr 事件之前，应使用 panqianliutong 作为历史基准。"""
        raw_df = self._kline(
            [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            close=[10.0, 10.0, 10.0],
            volume=[1_000_000.0, 2_000_000.0, 3_000_000.0],  # 股
            amount=[10_000_000.0, 20_000_000.0, 30_000_000.0],
        )
        # 2024-01-03 有一次送股事件：盘前 1 亿股流通，盘后 1.1 亿股
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2024, 1, 3)],
                "category": [1],
                "panqianliutong": [10000.0],  # 万股 → 1 亿股
                "panhouliutong": [11000.0],
                "fenhong": [0.0],
                "peigu": [0.0],
                "peigujia": [0.0],
                "songzhuangu": [0.0],
            }
        )

        rate = compute_turnover_rate(raw_df, xdxr_df)

        # 2024-01-02: 历史基准 = panqianliutong = 1 亿股 → 1M/100M*100 = 1.0
        # 2024-01-03: 事件日生效 panhouliutong = 1.1 亿股 → 2M/110M*100 ≈ 1.818
        # 2024-01-04: 继续用 panhouliutong → 3M/110M*100 ≈ 2.727
        self.assertAlmostEqual(rate.iloc[0], 1.0, places=4)
        self.assertAlmostEqual(rate.iloc[1], 200.0 / 110.0, places=4)
        self.assertAlmostEqual(rate.iloc[2], 300.0 / 110.0, places=4)

    def test_compute_turnover_rate_handles_volume_in_hand(self):
        """若 volume 单位是手，应自动识别并乘 100。"""
        raw_df = self._kline(
            [date(2024, 1, d) for d in (2, 3, 4)],
            close=[10.0, 10.0, 10.0],
            volume=[10_000.0, 20_000.0, 15_000.0],                # 手
            amount=[10_000_000.0, 20_000_000.0, 15_000_000.0],    # 元
        )
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2023, 6, 1)],
                "category": [1],
                "panqianliutong": [10000.0],
                "panhouliutong": [10000.0],  # 1 亿股
                "fenhong": [0.0],
                "peigu": [0.0],
                "peigujia": [0.0],
                "songzhuangu": [0.0],
            }
        )

        rate = compute_turnover_rate(raw_df, xdxr_df)

        # 10000 手 × 100 = 1M 股；1M/100M*100 = 1.0
        self.assertAlmostEqual(rate.iloc[0], 1.0, places=4)
        self.assertAlmostEqual(rate.iloc[1], 2.0, places=4)
        self.assertAlmostEqual(rate.iloc[2], 1.5, places=4)

    def test_compute_turnover_rate_without_xdxr_returns_nan(self):
        """无 xdxr 数据时，turnover_rate 全为 NaN，但不抛异常。"""
        raw_df = self._kline(
            [date(2024, 1, 2), date(2024, 1, 3)],
            close=[10.0, 10.5],
            volume=[100000.0, 120000.0],
            amount=[1_000_000.0, 1_260_000.0],
        )
        rate = compute_turnover_rate(raw_df, None)

        self.assertEqual(len(rate), 2)
        self.assertTrue(rate.isna().all())

    def test_compute_turnover_rate_ignores_same_day_bad_share_event(self):
        """同日多条股本事件时，应避开总股本为 0 的异常小股本。"""
        raw_df = self._kline(
            [date(2024, 10, 18), date(2024, 10, 21)],
            close=[7.75, 7.15],
            volume=[283_345_792.0, 298_567_488.0],
            amount=[2_116_446_720.0, 2_151_651_840.0],
        )
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2018, 12, 28), date(2018, 12, 28)],
                "category": [9, 15],
                "panqianliutong": [130000.0, 0.0],
                "panhouliutong": [197210.0, 5.17],
                "qianzongguben": [130000.0, 0.0],
                "houzongguben": [197210.0, 0.0],
            }
        )

        rate = compute_turnover_rate(raw_df, xdxr_df)

        self.assertAlmostEqual(rate.iloc[0], 14.3677, places=4)
        self.assertAlmostEqual(rate.iloc[1], 15.1396, places=4)

    def test_compute_turnover_rate_keeps_original_input_order(self):
        """输入乱序时，返回结果仍应与原始行顺序对齐。"""
        raw_df = self._kline(
            [date(2024, 1, 4), date(2024, 1, 2), date(2024, 1, 3)],
            close=[10.0, 10.0, 10.0],
            volume=[3_000_000.0, 1_000_000.0, 2_000_000.0],
            amount=[30_000_000.0, 10_000_000.0, 20_000_000.0],
        )
        xdxr_df = pd.DataFrame(
            {
                "trade_date": [date(2024, 1, 3)],
                "category": [5],
                "panqianliutong": [10000.0],
                "panhouliutong": [11000.0],
            }
        )

        rate = compute_turnover_rate(raw_df, xdxr_df)

        self.assertAlmostEqual(rate.iloc[0], 300.0 / 110.0, places=4)
        self.assertAlmostEqual(rate.iloc[1], 1.0, places=4)
        self.assertAlmostEqual(rate.iloc[2], 200.0 / 110.0, places=4)


class TestMootdxAdapterPagination(unittest.TestCase):
    """测试 mootdx 分页逻辑。"""

    def test_ensure_connected_does_not_probe_healthy_pytdx_connection(self):
        class HealthyPytdxAPI:
            last_transaction_failed = False

            def get_security_bars(self, *args, **kwargs):
                raise AssertionError("healthy connections must not send a probe request")

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = HealthyPytdxAPI()
        adapter.connected = True
        adapter.backend = "pytdx"

        self.assertTrue(adapter._ensure_connected())

    def test_ensure_connected_rebuilds_poisoned_pytdx_connection(self):
        class PoisonedPytdxAPI:
            last_transaction_failed = True

            def __init__(self):
                self.disconnected = False

            def disconnect(self):
                self.disconnected = True

            def get_security_bars(self, *args, **kwargs):
                raise AssertionError("poisoned connection must not be probed or reused")

        api = PoisonedPytdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "pytdx"

        with patch.object(adapter, "_connect", return_value=True) as reconnect:
            self.assertTrue(adapter._ensure_connected())

        self.assertTrue(api.disconnected)
        reconnect.assert_called_once_with()

    def test_ensure_connected_rebuilds_poisoned_mootdx_client(self):
        class PoisonedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": True})()
                self.closed = False

            def close(self):
                self.closed = True

        api = PoisonedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"

        with patch.object(adapter, "_connect", return_value=True) as reconnect:
            self.assertTrue(adapter._ensure_connected())

        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)
        reconnect.assert_called_once_with()

    def test_failed_mootdx_bars_disconnects_before_next_request(self):
        class FailedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": False})()
                self.closed = False

            def bars(self, **kwargs):
                self.client.last_transaction_failed = True
                return pd.DataFrame()

            def close(self):
                self.closed = True

        api = FailedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        result = adapter.get_stock_kline("688022", adjust="none", max_bars=2)

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_failed_mootdx_transaction_rejects_nonempty_bars(self):
        class FailedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": False})()
                self.closed = False

            def bars(self, **kwargs):
                self.client.last_transaction_failed = True
                return pd.DataFrame({
                    "datetime": ["2026-09-22"],
                    "open": [114.68], "high": [125.85],
                    "low": [114.61], "close": [123.60],
                    "volume": [13012229], "amount": [1591593216],
                })

            def close(self):
                self.closed = True

        api = FailedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        result = adapter.get_stock_kline("688022", adjust="none", max_bars=2)

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_failed_mootdx_xdxr_disconnects_connection(self):
        class FailedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": False})()
                self.closed = False

            def xdxr(self, **kwargs):
                self.client.last_transaction_failed = True
                return pd.DataFrame()

            def close(self):
                self.closed = True

        api = FailedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.last_failure_kind = None

        result = adapter.get_xdxr_info("688022")

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_qfq_rejects_bars_after_xdxr_connection_failure(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = object()
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None
        bars = pd.DataFrame({
            "datetime": ["2026-09-22"],
            "open": [8.8], "high": [9.0],
            "low": [8.7], "close": [8.9],
            "volume": [1000], "amount": [8900],
        })

        def failed_xdxr(symbol):
            adapter.last_failure_kind = "connectivity"
            return None

        with patch.object(adapter, "_fetch_bars_data", return_value=bars):
            with patch.object(adapter, "get_xdxr_info", side_effect=failed_xdxr):
                result = adapter.get_stock_kline("688022", adjust="qfq")

        self.assertIsNone(result)
        self.assertEqual(adapter.fail_count, 1)
        self.assertEqual(adapter.success_count, 0)

    def test_mootdx_bars_exception_discards_connection(self):
        class FailedMootdxAPI:
            def __init__(self):
                self.client = type("Client", (), {"last_transaction_failed": False})()
                self.closed = False

            def bars(self, **kwargs):
                self.client.last_transaction_failed = True
                raise ConnectionError("broken transaction")

            def close(self):
                self.closed = True

        api = FailedMootdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        with patch.object(adapter, "_connect", return_value=False):
            result = adapter.get_stock_kline("688022", adjust="none", max_bars=2)

        self.assertIsNone(result)
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_failed_pytdx_transaction_is_classified_as_connectivity(self):
        class FailedPytdxAPI:
            last_transaction_failed = True

            def get_security_bars(self, *args, **kwargs):
                return None

            @staticmethod
            def to_df(raw):
                return pd.DataFrame(raw)

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = FailedPytdxAPI()
        adapter.connected = True
        adapter.backend = "pytdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        with patch.object(adapter, "_ensure_connected", return_value=True):
            result = adapter.get_stock_kline(
                "601696",
                start_date="2026-08-07",
                end_date="2026-08-07",
                adjust="none",
                max_bars=2,
            )

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")

    def test_symbol_market_parsing_covers_etf_prefixes(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = None

        self.assertEqual(adapter._parse_symbol("510300"), MARKET_SH)
        self.assertEqual(adapter._parse_symbol("588000"), MARKET_SH)
        self.assertEqual(adapter._parse_symbol("159915"), MARKET_SZ)
        self.assertEqual(adapter._parse_symbol("161725"), MARKET_SZ)

    def test_recent_successful_server_is_tried_first(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.SERVERS = [
            ("1.1.1.1", 7709, "first"),
            ("2.2.2.2", 7709, "cached"),
            ("3.3.3.3", 7709, "third"),
        ]

        with patch.object(
            adapter,
            "_load_server_state",
            return_value={"host": "2.2.2.2", "port": 7709},
        ):
            ordered = adapter._ordered_servers()

        self.assertEqual(ordered[0], ("2.2.2.2", 7709, "cached"))
        self.assertEqual(len(ordered), 3)

    def test_recent_verified_servers_precede_legacy_servers(self):
        self.assertEqual(
            MootdxAdapter.SERVERS[:7],
            [
                ("139.9.52.158", 7709, "国泰君安深圳"),
                ("103.251.85.94", 7709, "国泰君安上海"),
                ("59.36.5.11", 7709, "安信"),
                ("117.34.114.14", 7709, "国泰君安1"),
                ("117.34.114.15", 7709, "国泰君安2"),
                ("117.34.114.16", 7709, "国泰君安3"),
                ("117.34.114.17", 7709, "国泰君安4"),
            ],
        )

    def test_stale_cached_server_only_delays_verified_fallback_once(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)

        with patch.object(
            adapter,
            "_load_server_state",
            return_value={"host": "60.12.136.250", "port": 7709},
        ):
            ordered = adapter._ordered_servers()

        self.assertEqual(ordered[0][:2], ("60.12.136.250", 7709))
        self.assertEqual(ordered[1][:2], ("139.9.52.158", 7709))

    def test_connect_prefers_mootdx_manual_before_pytdx(self):
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.Quotes = object()

        with patch.object(adapter, "_connect_manual", return_value=True) as manual_connect:
            with patch.object(adapter, "_connect_pytdx", return_value=True) as pytdx_connect:
                with patch.object(
                    adapter, "_connect_mootdx_auto", return_value=True
                ) as auto_connect:
                    result = adapter._connect()

        self.assertTrue(result)
        manual_connect.assert_called_once_with()
        pytdx_connect.assert_not_called()
        auto_connect.assert_not_called()

    def test_connect_manual_uses_explicit_mootdx_stream_without_config_scans(self):
        calls = []
        clients = []

        class FakeSocket:
            def settimeout(self, value):
                self.timeout = value

        class FakeClient:
            def __init__(self, **kwargs):
                calls.append(kwargs)
                clients.append(self)
                self.client = FakeSocket()
                self.closed = False
                self.probes = []

            def connect(self, host, port, time_out):
                self.server = (host, port)
                return self

            def setup(self):
                pass

            def get_security_bars(self, frequency, market, symbol, start, count):
                self.probes.append((market, symbol))
                return TestMootdxNodeFailover.bars(price=10.0 + market).to_dict("records")

            def disconnect(self):
                self.closed = True

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.Quotes = object()
        adapter.SERVERS = [("59.36.5.11", 7709, "安信")]
        adapter.timeout = 5
        adapter.connected = False
        adapter.api = None
        adapter.backend = None
        adapter.current_server = None

        with patch("mootdx.quotes.TdxHq_API", FakeClient):
            with patch("mootdx.quotes.config.setup", side_effect=AssertionError("implicit scan")):
                with patch.object(adapter, "_save_server_state") as save_state:
                    result = adapter._connect_manual()

        self.assertTrue(result)
        self.assertEqual(clients[0].server, ("59.36.5.11", 7709))
        self.assertFalse(calls[0]["auto_retry"])
        self.assertTrue(calls[0]["raise_exception"])
        self.assertFalse(calls[0]["heartbeat"])
        self.assertTrue(clients[0].closed)
        self.assertEqual(len(clients[0].probes), 3)
        self.assertEqual(clients[1].probes, [])
        save_state.assert_not_called()

    def test_real_tdxpy_parser_failure_is_not_accepted_as_no_data(self):
        try:
            from tdxpy.base_socket_client import last_ack_time
        except ImportError:
            self.skipTest("tdxpy is not installed")

        class MalformedClient:
            auto_retry = False
            raise_exception = True
            client = object()

            @last_ack_time
            def get_security_bars(self, **kwargs):
                return struct.unpack("<I", b"\x20\x03")

        class MalformedAPI:
            def __init__(self):
                self.client = MalformedClient()
                self.closed = False

            def bars(self, **kwargs):
                return pd.DataFrame(self.client.get_security_bars(**kwargs))

            def close(self):
                self.closed = True

        api = MalformedAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        result = adapter.get_stock_kline("600519", adjust="none", max_bars=2)

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_valid_empty_bars_remain_no_data_without_discarding_stream(self):
        class EmptyAPI:
            def __init__(self):
                self.client = object()
                self.closed = False

            def bars(self, **kwargs):
                return pd.DataFrame()

            def close(self):
                self.closed = True

        api = EmptyAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        result = adapter.get_stock_kline("600519", adjust="none", max_bars=2)

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "no_data")
        self.assertFalse(api.closed)
        self.assertIs(adapter.api, api)

    def test_wrapped_header_and_body_failures_are_failed_transactions(self):
        try:
            from tdxpy.exceptions import TdxFunctionCallError
            from tdxpy.parser.base import ResponseHeaderRecvFails, ResponseRecvFails
        except ImportError:
            self.skipTest("tdxpy is not installed")

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = object()
        adapter.backend = "mootdx"
        for error_type in (ResponseHeaderRecvFails, ResponseRecvFails):
            with self.subTest(error_type=error_type.__name__):
                error = TdxFunctionCallError("calling function error")
                error.original_exception = error_type("truncated response")
                self.assertTrue(adapter._transaction_failed(error))

    def test_auto_probe_failure_closes_unusable_client(self):
        class FailedAPI:
            closed = False

            def bars(self, **kwargs):
                raise struct.error("truncated probe response")

            def close(self):
                self.closed = True

        api = FailedAPI()

        class FakeQuotes:
            @staticmethod
            def factory(**kwargs):
                return api

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.Quotes = FakeQuotes
        adapter.api = None
        adapter.backend = None
        adapter.connected = False
        adapter.timeout = 2
        adapter.SERVERS = [("192.0.2.1", 7709, "offline")]

        with patch.object(adapter, "_open_api", return_value=api):
            self.assertFalse(adapter._connect_mootdx_auto())
        self.assertTrue(api.closed)
        self.assertIsNone(adapter.api)

    def test_qfq_rejects_wrapped_xdxr_protocol_failure(self):
        try:
            from tdxpy.exceptions import TdxFunctionCallError
        except ImportError:
            self.skipTest("tdxpy is not installed")

        class FailedXdxrAPI:
            client = object()
            closed = False

            def xdxr(self, **kwargs):
                error = TdxFunctionCallError("calling function error")
                error.original_exception = struct.error("truncated xdxr response")
                raise error

            def close(self):
                self.closed = True

        api = FailedXdxrAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None
        bars = pd.DataFrame({
            "datetime": ["2026-10-22", "2026-10-23"],
            "open": [10.0, 10.1], "high": [10.2, 10.3],
            "low": [9.9, 10.0], "close": [10.1, 10.2],
            "volume": [100.0, 120.0], "amount": [1010.0, 1224.0],
        })
        with patch.object(adapter, "_fetch_bars_data", return_value=bars):
            result = adapter.get_stock_kline("600519", adjust="qfq", max_bars=2)

        self.assertIsNone(result)
        self.assertEqual(adapter.last_failure_kind, "connectivity")
        self.assertTrue(api.closed)

    def test_wrapped_second_page_failure_rejects_partial_history(self):
        try:
            from tdxpy.exceptions import TdxFunctionCallError
        except ImportError:
            self.skipTest("tdxpy is not installed")

        class FailedPageAPI:
            client = object()
            calls = 0
            closed = False

            def bars(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return pd.DataFrame({"datetime": ["2026-10-23"] * 800})
                error = TdxFunctionCallError("calling function error")
                error.original_exception = struct.error("truncated K-line response")
                raise error

            def close(self):
                self.closed = True

        api = FailedPageAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "mootdx"
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.last_failure_kind = None

        result = adapter.get_stock_kline("600519", adjust="none", max_bars=1600)

        self.assertIsNone(result)
        self.assertEqual(api.calls, 2)
        self.assertTrue(api.closed)
        self.assertEqual(adapter.last_failure_kind, "connectivity")

    def test_get_stock_kline_uses_start_offset_and_deduplicates(self):
        """分页应使用递增的 start，且结果不应重复。"""
        first_dates = pd.date_range("2024-01-01", periods=800, freq="D")
        second_dates = pd.to_datetime(["2026-03-10", "2026-03-10", "2026-03-11"])

        first_page = pd.DataFrame(
            {
                "datetime": first_dates,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.0,
                "volume": 100.0,
                "amount": 1000.0,
            }
        )
        second_page = pd.DataFrame(
            {
                "datetime": second_dates,
                "open": 12.0,
                "high": 13.0,
                "low": 11.0,
                "close": 12.0,
                "volume": 200.0,
                "amount": 2000.0,
            }
        )

        api = FakeQuotesAPI({0: first_page, 800: second_page})

        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.success_count = 0
        adapter.fail_count = 0
        adapter.current_server = "test"

        with patch.object(adapter, "_ensure_connected", return_value=True):
            result = adapter.get_stock_kline(
                "600519",
                start_date="2024-01-01",
                end_date="2026-03-11",
                adjust="none",
            )

        self.assertEqual(api.starts, [0, 800])
        self.assertEqual(result["trade_date"].nunique(), len(result))
        self.assertEqual(result["trade_date"].min(), date(2024, 1, 1))
        self.assertEqual(result["trade_date"].max(), date(2026, 3, 11))

    def test_get_index_kline_uses_explicit_market(self):
        """沪市 000xxx 指数应能显式传入 market=SH。"""
        page = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-06-03", "2026-06-04"]),
                "open": [10.0, 10.5],
                "high": [11.0, 11.5],
                "low": [9.0, 9.5],
                "close": [10.5, 11.0],
                "volume": [100.0, 120.0],
                "amount": [1000.0, 1320.0],
            }
        )

        class FakePytdxAPI:
            def __init__(self):
                self.market_args = []

            def get_index_bars(self, frequency, market, code, start, request_size):
                self.market_args.append((frequency, market, code, start, request_size))
                return page.to_dict("records") if start == 0 else []

            def to_df(self, raw):
                return pd.DataFrame(raw)

        api = FakePytdxAPI()
        adapter = MootdxAdapter.__new__(MootdxAdapter)
        adapter.api = api
        adapter.connected = True
        adapter.backend = "pytdx"
        adapter.success_count = 0
        adapter.fail_count = 0

        with patch.object(adapter, "_ensure_connected", return_value=True):
            result = adapter.get_index_kline(
                "000300",
                start_date="2026-06-03",
                end_date="2026-06-04",
                market=MARKET_SH,
                max_bars=5,
            )

        self.assertEqual(api.market_args[0][1], MARKET_SH)
        self.assertEqual(result["symbol"].iloc[0], "000300")
        self.assertEqual(result["trade_date"].max(), date(2026, 6, 4))


class TestCollectorHelpers(unittest.TestCase):
    """测试采集脚本的辅助逻辑。"""

    def test_load_symbols_deduplicates_mootdx_fallback(self):
        """mootdx 股票列表回退时应去重，避免重复采集。"""
        conn = None
        fallback_df = pd.DataFrame(
            {
                "code": ["000001", "000001", "600519", "600519", "688001", "159001"],
                "name": ["A", "A", "B", "B", "C", "ETF"],
            }
        )

        class FakeAdapter:
            def is_available(self):
                return True

            def get_stock_list(self):
                return fallback_df

            def disconnect(self):
                return None

        with patch.object(_module, "MootdxAdapter", return_value=FakeAdapter()):
            symbols = load_symbols(conn, symbol=None, symbols_file=None, limit=None)

        self.assertEqual(symbols, ["000001", "600519", "688001"])


class TestRawAndQfqDualWrite(unittest.TestCase):
    """同一请求应派生并原子保存 raw 与 qfq 两套日线。"""

    class FakeAdapter:
        def __init__(self):
            self.kline_calls = 0
            self.xdxr_calls = 0
            self.last_failure_kind = None

        def get_xdxr_info(self, symbol):
            self.xdxr_calls += 1
            return pd.DataFrame(
                {
                    "trade_date": [date(2024, 1, 2)],
                    "category": [1],
                    "panqianliutong": [10_000.0],
                    "panhouliutong": [20_000.0],
                    "fenhong": [1.0],
                    "peigu": [0.0],
                    "peigujia": [0.0],
                    "songzhuangu": [10.0],
                }
            )

        def get_stock_kline(self, **kwargs):
            self.kline_calls += 1
            return pd.DataFrame(
                {
                    "symbol": ["600519", "600519", "600519"],
                    "trade_date": [
                        date(2024, 1, 1),
                        date(2024, 1, 2),
                        date(2024, 1, 3),
                    ],
                    "open": [10.0, 4.95, 5.5],
                    "high": [10.0, 4.95, 5.5],
                    "low": [10.0, 4.95, 5.5],
                    "close": [10.0, 4.95, 5.5],
                    "volume": [1_000_000.0, 2_000_000.0, 3_000_000.0],
                    "amount": [10_000_000.0, 9_900_000.0, 16_500_000.0],
                }
            )

    class FakePool:
        def __init__(self, adapter):
            self.adapter = adapter
            self.release_calls = 0

        def acquire(self):
            return self.adapter

        def release(self, adapter):
            self.release_calls += 1

    def _fetch(self):
        adapter = self.FakeAdapter()
        pool = self.FakePool(adapter)
        result = fetch_one_symbol(
            symbol="600519",
            end_date=date(2024, 1, 3),
            default_start=date(2024, 1, 1),
            last_date=None,
            lookback_days=5,
            full_refresh=False,
            pool=pool,
            max_bars=160,
        )
        return result, adapter, pool

    def test_one_adapter_fetch_returns_distinct_raw_and_qfq_prices(self):
        result, adapter, pool = self._fetch()

        self.assertEqual(result.status, "success")
        self.assertIsNotNone(result.data)
        self.assertIsNotNone(result.raw_data)
        self.assertNotEqual(
            result.data.iloc[0]["close"],
            result.raw_data.iloc[0]["close"],
        )
        self.assertAlmostEqual(result.raw_data.iloc[0]["close"], 10.0)
        self.assertEqual(
            list(result.raw_data.columns),
            [
                "symbol",
                "trade_date",
                "close",
                "source",
                "quality_flag",
                "run_id",
                "updated_at",
                "adjustment_mode",
                "market",
            ],
        )
        self.assertEqual(set(result.raw_data["source"]), {"tdx_mootdx_raw"})
        self.assertEqual(set(result.raw_data["quality_flag"]), {"ok_unadjusted"})
        self.assertEqual(set(result.raw_data["adjustment_mode"]), {"none"})
        for omitted_column in (
            "open",
            "high",
            "low",
            "volume",
            "amount",
            "pct_chg",
            "turnover_rate",
        ):
            self.assertNotIn(omitted_column, result.raw_data.columns)
        self.assertEqual(adapter.kline_calls, 1)
        self.assertEqual(adapter.xdxr_calls, 1)
        self.assertEqual(pool.release_calls, 1)

    def test_bse_reuses_one_unadjusted_fetch_for_compact_raw_data(self):
        adapter = self.FakeAdapter()
        pool = self.FakePool(adapter)

        result = fetch_one_symbol(
            symbol="430047",
            end_date=date(2024, 1, 3),
            default_start=date(2024, 1, 1),
            last_date=None,
            lookback_days=5,
            full_refresh=False,
            pool=pool,
            max_bars=160,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(adapter.kline_calls, 1)
        self.assertEqual(adapter.xdxr_calls, 0)
        self.assertEqual(list(result.raw_data.columns), _module.RAW_SAVE_COLUMNS)
        self.assertEqual(set(result.raw_data["source"]), {"tdx_mootdx_raw"})
        self.assertEqual(set(result.raw_data["adjustment_mode"]), {"none"})
        self.assertEqual(
            result.data["close"].tolist(),
            result.raw_data["close"].tolist(),
        )

    def test_dual_batch_saves_both_tables(self):
        result, _, _ = self._fetch()
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_target_table(conn, "qfq_prices")
        init_raw_target_table(conn, "raw_prices")

        qfq_rows, raw_rows = save_dual_batch(
            conn,
            "qfq_prices",
            "raw_prices",
            [result],
        )

        self.assertEqual((qfq_rows, raw_rows), (3, 3))
        self.assertAlmostEqual(
            conn.execute(
                "SELECT close FROM qfq_prices WHERE trade_date = '2024-01-01'"
            ).fetchone()[0],
            4.95,
        )
        self.assertEqual(
            conn.execute(
                """
                SELECT close, source, quality_flag, adjustment_mode
                FROM raw_prices
                WHERE trade_date = '2024-01-01'
                """
            ).fetchone(),
            (10.0, "tdx_mootdx_raw", "ok_unadjusted", "none"),
        )
        raw_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(raw_prices)")
        ]
        self.assertEqual(raw_columns, _module.RAW_SAVE_COLUMNS)
        raw_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(raw_prices)")
        }
        self.assertIn("idx_raw_prices_date", raw_indexes)
        self.assertIn("idx_raw_prices_market_date", raw_indexes)
        self.assertNotIn("idx_raw_prices_symbol", raw_indexes)

    def test_dual_batch_rejects_source_history_from_another_symbol(self):
        result, _, _ = self._fetch()
        result.anchor_date = date(2023, 12, 29)
        result.anchor_raw_close = 112.30
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_target_table(conn, "qfq_prices")
        init_raw_target_table(conn, "raw_prices")
        conn.execute(
            "INSERT INTO raw_prices (symbol, trade_date, close) VALUES (?, ?, ?)",
            (result.symbol, "2023-12-29", 8.99),
        )

        with self.assertRaisesRegex(ValueError, "source anchor mismatch"):
            save_dual_batch(conn, "qfq_prices", "raw_prices", [result])

        self.assertEqual(conn.execute("SELECT COUNT(*) FROM qfq_prices").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_prices").fetchone()[0], 1)

    def test_dual_batch_accepts_matching_source_anchor(self):
        result, _, _ = self._fetch()
        result.anchor_date = date(2023, 12, 29)
        result.anchor_raw_close = 8.99
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_target_table(conn, "qfq_prices")
        init_raw_target_table(conn, "raw_prices")
        conn.execute(
            "INSERT INTO raw_prices (symbol, trade_date, close) VALUES (?, ?, ?)",
            (result.symbol, "2023-12-29", 8.99),
        )

        self.assertEqual(
            save_dual_batch(conn, "qfq_prices", "raw_prices", [result]),
            (3, 3),
        )

    def test_incremental_fetch_keeps_overlap_as_anchor(self):
        adapter = self.FakeAdapter()
        result = fetch_one_symbol(
            symbol="600519",
            end_date=date(2024, 1, 3),
            default_start=date(2023, 12, 30),
            last_date=date(2024, 1, 2),
            lookback_days=1,
            full_refresh=False,
            pool=self.FakePool(adapter),
            max_bars=160,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.anchor_date, date(2024, 1, 2))
        self.assertEqual(result.anchor_raw_close, 4.95)
        self.assertEqual(result.raw_data["trade_date"].tolist(), [date(2024, 1, 3)])

    def test_dual_batch_rolls_back_qfq_when_raw_write_fails(self):
        result, _, _ = self._fetch()
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_target_table(conn, "qfq_prices")
        init_raw_target_table(conn, "raw_prices")
        conn.execute(
            """
            CREATE TRIGGER reject_raw_insert
            BEFORE INSERT ON raw_prices
            BEGIN
                SELECT RAISE(ABORT, 'raw write failed');
            END
            """
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "raw write failed"):
            save_dual_batch(
                conn,
                "qfq_prices",
                "raw_prices",
                [result],
            )

        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM qfq_prices").fetchone()[0],
            0,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM raw_prices").fetchone()[0],
            0,
        )

    def test_raw_table_name_must_be_valid_and_distinct(self):
        for invalid_name in ("raw-prices", "1raw_prices", "raw prices"):
            with self.subTest(invalid_name=invalid_name):
                with self.assertRaises(ValueError):
                    validate_target_tables("stock_daily_prices", invalid_name)

        with self.assertRaisesRegex(ValueError, "must be different"):
            validate_target_tables("stock_daily_prices", "stock_daily_prices")
        with self.assertRaisesRegex(ValueError, "must be different"):
            validate_target_tables("Stock_Daily_Prices", "stock_daily_prices")

        parser = _module.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.raw_table, "stock_daily_prices_raw")


@pytest.mark.parametrize('symbol', ['600519', '430047'])
@pytest.mark.parametrize('has_history', [False, True])
def test_exact_day_force_window_preserves_recent_request_bound(symbol, has_history):
    """An exact-day repair must not silently download all history per stock."""
    from types import SimpleNamespace

    target = date.today()
    requested = []

    class Adapter:
        last_failure_kind = None

        def get_xdxr_info(self, code):
            return None

        def get_stock_kline(self, **kwargs):
            requested.append(kwargs)
            return None

    adapter = Adapter()
    pool = SimpleNamespace(acquire=lambda: adapter, release=lambda value: None)
    fetch_one_symbol(symbol, target, target,
                     target - _module.timedelta(days=3) if has_history else None,
                     1, False, pool, 40, force_window=True)
    assert requested[0]['max_bars'] == 40


@pytest.mark.parametrize('age,full_refresh,force_window,cap,expected', [
    (7, False, True, 40, 40),
    (38, False, True, 40, 40),
    (39, False, True, 40, None),
    (400, False, True, 40, None),
    (-1, False, True, 40, None),
    (0, True, True, 40, None),
    (0, False, False, 40, None),
    (0, False, True, None, None),
])
def test_exact_day_request_bound_keeps_historical_and_refresh_semantics(
        age, full_refresh, force_window, cap, expected):
    target = date.today() - _module.timedelta(days=age)
    assert _module.choose_request_max_bars(
        target, target, target, None, full_refresh, force_window, cap) == expected


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize("existing_target", [False, True])
def test_single_day_repair_seeds_missing_pct_chg(existing_target):
    """Exact-day repair needs the stored previous close for new and existing rows."""
    with sqlite3.connect(":memory:") as conn:
        init_target_table(conn, "prices")
        conn.execute("INSERT INTO prices (symbol,trade_date,close) VALUES (?,?,?)",
                     ("001326", "2026-10-23", 90.0))
        row = dict(symbol="001326", trade_date=date(2026, 10, 26), open=90.99,
                   close=91.0, high=96.66, low=88.49, volume=2055982.0,
                   amount=192274480.0, pct_chg=None, turnover_rate=8.56,
                   source=_module.SOURCE_NAME, quality_flag="ok", run_id="test",
                   updated_at="2026-10-26 16:00:00", adjustment_mode="qfq", market="SZ")
        if existing_target:
            columns = list(row)
            values = [v.isoformat() if isinstance(v, date) else v for v in row.values()]
            conn.execute(f"INSERT INTO prices ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         values)
        result = _module.FetchResult(symbol="001326", status="success", market="SZ",
                                     data=pd.DataFrame([row]), start_date=row["trade_date"],
                                     end_date=row["trade_date"], write_policy="repair")
        assert _module.save_batch(conn, "prices", [result]) == 1
        actual = conn.execute("SELECT pct_chg FROM prices WHERE trade_date='2026-10-26'").fetchone()[0]
        assert actual == pytest.approx(100 / 90)
        assert _module.save_batch(conn, "prices", [result]) == 0


@pytest.mark.parametrize("previous_close", [None, 0, -1, "invalid"])
def test_pct_chg_seed_without_valid_previous_close_remains_unknown(previous_close):
    data = pd.DataFrame({"trade_date": [date(2026, 10, 26)], "close": [91.0],
                         "pct_chg": [None]})
    assert pd.isna(seed_incremental_pct_chg(data, previous_close).iloc[0]["pct_chg"])


def test_pct_chg_seed_preserves_existing_multi_day_values():
    data = pd.DataFrame({"trade_date": [date(2026, 10, 26), date(2026, 10, 27)],
                         "close": [91.0, 85.69], "pct_chg": [1.11, -5.835164835]})
    result = seed_incremental_pct_chg(data, 80.0)
    assert result["pct_chg"].tolist() == data["pct_chg"].tolist()
    missing_first = data.copy()
    missing_first.loc[0, "pct_chg"] = None
    result = seed_incremental_pct_chg(missing_first, 90.0)
    assert result.iloc[0]["pct_chg"] == pytest.approx(100 / 90)
    assert result.iloc[1]["pct_chg"] == data.iloc[1]["pct_chg"]
