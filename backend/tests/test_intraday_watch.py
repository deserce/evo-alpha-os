import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data_job.core import tdx_adapter as tdx_adapter_module
from data_job.core.tdx_adapter import TdxAdapter
from quant_engine.watch import intraday as intraday_module
from quant_engine.watch.intraday import IntradayWatchMonitor


SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeApi:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_security_bars(self, category, market, code, offset, count):
        self.calls.append((category, market, code, offset, count))
        return self.rows


class SequencedApi:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_security_bars(self, category, market, code, offset, count):
        self.calls.append((category, market, code, offset, count))
        return self.responses.pop(0)


class FakeAdapter:
    def __init__(self, bars_by_symbol=None, error=None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.error = error
        self.calls = []

    def get_recent_stock_bars(self, symbol, period="5min", count=3):
        self.calls.append((symbol, period, count))
        if self.error:
            raise self.error
        return self.bars_by_symbol.get(symbol)

    def disconnect(self):
        return None


class FakePortfolioExitEngine:
    def __init__(self, created_event_count=0):
        self.created_event_count = created_event_count
        self.calls = []

    def run_intraday_bars(
        self,
        bar_time,
        prices_micros_by_symbol,
        *,
        observed_at,
    ):
        self.calls.append(
            {
                "bar_time": bar_time,
                "prices": dict(prices_micros_by_symbol),
                "observed_at": observed_at,
            }
        )
        return SimpleNamespace(created_event_count=self.created_event_count)


class RecoverableSingletonAdapter:
    def __init__(self, *, reconnects: bool):
        self.connected = False
        self.reconnects = reconnects
        self.reconnect_calls = 0

    def is_available(self):
        return self.connected

    def _ensure_connected(self):
        self.reconnect_calls += 1
        self.connected = self.reconnects
        return self.connected


def test_cached_unavailable_tdx_adapter_reconnects_on_next_poll(monkeypatch):
    adapter = RecoverableSingletonAdapter(reconnects=True)
    monkeypatch.setattr(tdx_adapter_module, "_tdx_instance", adapter)

    recovered = tdx_adapter_module.get_tdx_adapter()

    assert recovered is adapter
    assert adapter.reconnect_calls == 1


def test_cached_unavailable_tdx_adapter_stays_blocked_when_reconnect_fails(monkeypatch):
    adapter = RecoverableSingletonAdapter(reconnects=False)
    monkeypatch.setattr(tdx_adapter_module, "_tdx_instance", adapter)

    recovered = tdx_adapter_module.get_tdx_adapter()

    assert recovered is None
    assert adapter.reconnect_calls == 1


def test_healthy_cached_tdx_adapter_does_not_reconnect(monkeypatch):
    adapter = RecoverableSingletonAdapter(reconnects=True)
    adapter.connected = True
    monkeypatch.setattr(tdx_adapter_module, "_tdx_instance", adapter)

    recovered = tdx_adapter_module.get_tdx_adapter()

    assert recovered is adapter
    assert adapter.reconnect_calls == 0


def _create_db(path: Path, snapshots: list[tuple] | None = None) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE watch_daily_snapshot (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                watch_state TEXT NOT NULL,
                trigger_price REAL,
                invalidation_price REAL,
                setup_id TEXT,
                current_price REAL,
                universe_source TEXT,
                name TEXT,
                pool_name TEXT,
                PRIMARY KEY (trade_date, symbol)
            );
            CREATE TABLE watch_events (
                event_id TEXT PRIMARY KEY,
                dedupe_key TEXT UNIQUE,
                trade_date TEXT NOT NULL,
                symbol TEXT,
                setup_id TEXT,
                event_type TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                severity TEXT,
                title TEXT,
                message TEXT,
                status TEXT,
                payload TEXT,
                resolved_at TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO watch_daily_snapshot (
                trade_date, symbol, watch_state, trigger_price,
                invalidation_price, setup_id, current_price, universe_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            snapshots
            or [
                ("2026-07-10", "600519", "IN_ZONE", 100.0, 92.0, "setup-1", 98.0, "manual")
            ],
        )


def _bar(at: str, close: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "600519",
                "bar_time": pd.Timestamp(at),
                "open": close - 1,
                "high": close + 0.5,
                "low": close - 1.5,
                "close": close,
                "volume": 1000,
                "amount": 100000,
            }
        ]
    )


def _add_portfolio_targets(path: Path, symbols: list[str]) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                account_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                position_revision INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (account_id, symbol)
            );
            CREATE TABLE IF NOT EXISTS portfolio_exit_plans (
                account_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                position_revision INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                intraday_enabled INTEGER NOT NULL,
                PRIMARY KEY (account_id, symbol, position_revision)
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO portfolio_positions (
                account_id, symbol, position_revision, quantity, status
            ) VALUES (1, ?, 1, 100, 'OPEN')
            """,
            [(symbol,) for symbol in symbols],
        )
        conn.executemany(
            """
            INSERT INTO portfolio_exit_plans (
                account_id, symbol, position_revision, enabled, intraday_enabled
            ) VALUES (1, ?, 1, 1, 1)
            """,
            [(symbol,) for symbol in symbols],
        )


def test_watch_and_portfolio_same_symbol_fetches_once_and_batches_micros(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    _add_portfolio_targets(db_path, ["600519"])
    adapter = FakeAdapter({"600519": _bar("2026-07-13 10:00", 101.123456)})
    exit_engine = FakePortfolioExitEngine()

    result = IntradayWatchMonitor(
        db_path=db_path,
        adapter=adapter,
        portfolio_exit_engine=exit_engine,
    ).run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    assert adapter.calls == [("600519", "5min", 3)]
    assert exit_engine.calls == [
        {
            "bar_time": "2026-07-13T10:00:00+08:00",
            "prices": {"600519": 101_123_456},
            "observed_at": "2026-07-13T10:01:00+08:00",
        }
    ]
    assert result["monitored"] == 1
    assert result["ready_targets"] == 1
    assert result["portfolio_targets"] == 1


def test_portfolio_only_symbol_is_monitored_without_ready_snapshot(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM watch_daily_snapshot")
    _add_portfolio_targets(db_path, ["000001"])
    adapter = FakeAdapter({"000001": _bar("2026-07-13 10:00", 12.34)})
    exit_engine = FakePortfolioExitEngine(created_event_count=1)

    result = IntradayWatchMonitor(
        db_path=db_path,
        adapter=adapter,
        portfolio_exit_engine=exit_engine,
    ).run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    assert adapter.calls == [("000001", "5min", 3)]
    assert exit_engine.calls[0]["prices"] == {"000001": 12_340_000}
    assert result["status"] == "completed"
    assert result["portfolio_triggered"] == 1
    assert result["triggered"] == 0


def test_union_limit_is_stable_and_reports_each_omitted_count(tmp_path):
    db_path = tmp_path / "watch.db"
    ready_symbols = [f"{value:06d}" for value in range(1, 103, 2)]
    portfolio_symbols = [f"{value:06d}" for value in range(2, 103, 2)]
    snapshots = [
        (
            "2026-07-10",
            symbol,
            "IN_ZONE",
            100.0,
            92.0,
            f"setup-{symbol}",
            98.0,
            "manual",
        )
        for symbol in ready_symbols
    ]
    _create_db(db_path, snapshots)
    _add_portfolio_targets(db_path, portfolio_symbols)
    adapter = FakeAdapter(
        {
            symbol: _bar("2026-07-13 10:00", 99.0)
            for symbol in ready_symbols + portfolio_symbols
        }
    )
    exit_engine = FakePortfolioExitEngine()

    result = IntradayWatchMonitor(
        db_path=db_path,
        adapter=adapter,
        portfolio_exit_engine=exit_engine,
    ).run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    assert [call[0] for call in adapter.calls] == [
        f"{value:06d}" for value in range(1, 51)
    ]
    assert result["monitored"] == 50
    assert result["ready_omitted"] == 26
    assert result["portfolio_omitted"] == 26
    assert sorted(exit_engine.calls[0]["prices"]) == [
        f"{value:06d}" for value in range(2, 51, 2)
    ]


def test_portfolio_old_or_missing_bar_never_reaches_exit_engine(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM watch_daily_snapshot")
    _add_portfolio_targets(db_path, ["000001", "000002"])
    adapter = FakeAdapter(
        {
            "000001": _bar("2026-07-13 09:55", 12.0),
            "000002": None,
        }
    )
    exit_engine = FakePortfolioExitEngine()

    result = IntradayWatchMonitor(
        db_path=db_path,
        adapter=adapter,
        portfolio_exit_engine=exit_engine,
    ).run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    assert exit_engine.calls == []
    assert result["portfolio_triggered"] == 0


def test_portfolio_event_invokes_shared_notifier_after_engine_commit(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM watch_daily_snapshot")
    _add_portfolio_targets(db_path, ["000001"])
    adapter = FakeAdapter({"000001": _bar("2026-07-13 10:00", 12.0)})
    exit_engine = FakePortfolioExitEngine(created_event_count=1)
    notified = []

    result = IntradayWatchMonitor(
        db_path=db_path,
        adapter=adapter,
        portfolio_exit_engine=exit_engine,
        notifier=notified.append,
    ).run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    assert result["portfolio_triggered"] == 1
    assert notified == ["2026-07-13"]


def test_combined_notifier_calls_both_channels_when_first_mail_fails():
    calls = []

    class FakeDispatcher:
        def dispatch_immediate(self, trade_date):
            calls.append(("watch", trade_date))
            raise RuntimeError("smtp unavailable")

        def dispatch_portfolio_exit_immediate(self, trade_date):
            calls.append(("portfolio", trade_date))
            return {"sent": 1, "failed": 0, "skipped": 0}

    intraday_module.dispatch_intraday_notifications(
        FakeDispatcher(),
        "2026-07-13",
    )

    assert calls == [
        ("watch", "2026-07-13"),
        ("portfolio", "2026-07-13"),
    ]


def test_missing_portfolio_schema_is_backward_compatible(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM watch_daily_snapshot")
    adapter = FakeAdapter()
    exit_engine = FakePortfolioExitEngine()

    result = IntradayWatchMonitor(
        db_path=db_path,
        adapter=adapter,
        portfolio_exit_engine=exit_engine,
    ).run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    assert result["status"] == "no_ready_symbols"
    assert adapter.calls == []
    assert exit_engine.calls == []


def test_tdx_recent_bars_uses_one_bounded_request_and_preserves_intraday_time():
    adapter = TdxAdapter.__new__(TdxAdapter)
    adapter.api = FakeApi(
        [
            {
                "datetime": "2026-07-13 09:55",
                "open": 99,
                "high": 101,
                "low": 98,
                "close": 100,
                "vol": 10,
                "amount": 1000,
            },
            {
                "datetime": "2026-07-13 10:00",
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "vol": 20,
                "amount": 2000,
            },
        ]
    )
    adapter.success_count = 0
    adapter.fail_count = 0
    adapter._ensure_connected = lambda: True

    bars = adapter.get_recent_stock_bars("600519", period="5min", count=2)

    assert adapter.api.calls == [(0, 1, "600519", 0, 2)]
    assert bars["bar_time"].dt.strftime("%Y-%m-%d %H:%M").tolist() == [
        "2026-07-13 09:55",
        "2026-07-13 10:00",
    ]


def test_tdx_recent_bars_reconnects_and_retries_once_after_empty_response():
    rows = [
        {
            "datetime": "2026-07-24 09:45",
            "open": 99,
            "high": 101,
            "low": 98,
            "close": 100,
            "vol": 10,
            "amount": 1000,
        }
    ]
    adapter = TdxAdapter.__new__(TdxAdapter)
    adapter.api = SequencedApi([[], rows])
    adapter.success_count = 0
    adapter.fail_count = 0
    adapter._ensure_connected = lambda: True
    reconnects = []
    adapter._connect = lambda: reconnects.append(True) or True

    bars = adapter.get_recent_stock_bars("002384", period="5min", count=3)

    assert bars is not None
    assert len(adapter.api.calls) == 2
    assert reconnects == [True]
    assert adapter.fail_count == 1
    assert adapter.success_count == 1


def test_tdx_recent_bars_bounds_repeated_empty_response_to_two_requests():
    adapter = TdxAdapter.__new__(TdxAdapter)
    adapter.api = SequencedApi([[], []])
    adapter.success_count = 0
    adapter.fail_count = 0
    adapter._ensure_connected = lambda: True
    reconnects = []
    adapter._connect = lambda: reconnects.append(True) or True

    bars = adapter.get_recent_stock_bars("002384", period="5min", count=3)

    assert bars is None
    assert len(adapter.api.calls) == 2
    assert reconnects == [True]
    assert adapter.fail_count == 2


def test_tdx_recent_bars_does_not_retry_when_empty_response_reconnect_fails():
    adapter = TdxAdapter.__new__(TdxAdapter)
    adapter.api = SequencedApi([[]])
    adapter.success_count = 0
    adapter.fail_count = 0
    adapter._ensure_connected = lambda: True
    reconnects = []
    adapter._connect = lambda: reconnects.append(True) or False

    bars = adapter.get_recent_stock_bars("002384", period="5min", count=3)

    assert bars is None
    assert len(adapter.api.calls) == 1
    assert reconnects == [True]
    assert adapter.fail_count == 1


def test_completed_bar_breakout_creates_one_triggered_event(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    adapter = FakeAdapter({"600519": _bar("2026-07-13 10:00", 101.0)})
    monitor = IntradayWatchMonitor(db_path=db_path, adapter=adapter)

    first = monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))
    second = monitor.run_once(datetime(2026, 7, 13, 10, 2, tzinfo=SHANGHAI))

    assert first["triggered"] == 1
    assert second["triggered"] == 0
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT event_type, symbol, setup_id, to_state, dedupe_key FROM watch_events"
        ).fetchone()
        assert row == (
            "TRIGGERED",
            "600519",
            "setup-1",
            "TRIGGERED",
            "state:600519:setup-1:TRIGGERED",
        )


def test_run_once_explicitly_closes_sqlite_connection(tmp_path, monkeypatch):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    real_connect = sqlite3.connect
    tracked = []

    class TrackingConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.closed = False

        def close(self):
            self.closed = True
            super().close()

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = real_connect(*args, **kwargs)
        tracked.append(connection)
        return connection

    monkeypatch.setattr(intraday_module.sqlite3, "connect", tracked_connect)
    adapter = FakeAdapter({"600519": _bar("2026-07-13 10:00", 99.0)})

    IntradayWatchMonitor(db_path=db_path, adapter=adapter).run_once(
        datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI)
    )

    assert tracked
    assert all(connection.closed for connection in tracked)


def test_monitor_uses_expected_completed_bar_when_a_forming_bar_is_newer(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    bars = pd.concat(
        [
            _bar("2026-07-13 10:00", 101.0),
            _bar("2026-07-13 10:05", 105.0),
        ],
        ignore_index=True,
    )
    adapter = FakeAdapter({"600519": bars})

    result = IntradayWatchMonitor(db_path=db_path, adapter=adapter).run_once(
        datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI)
    )

    assert result["triggered"] == 1
    assert result["blocked"] == 0
    with sqlite3.connect(db_path) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM watch_events WHERE event_type='TRIGGERED'"
            ).fetchone()[0]
        )
    assert payload["bar_time"].startswith("2026-07-13T10:00:00")


def test_trigger_event_includes_stock_name_group_and_current_observation_list(
    tmp_path,
):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE watch_daily_snapshot SET name='贵州茅台', pool_name='自选股' "
            "WHERE symbol='600519'"
        )
    adapter = FakeAdapter({"600519": _bar("2026-07-13 10:00", 101.0)})

    result = IntradayWatchMonitor(db_path=db_path, adapter=adapter).run_once(
        datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI)
    )

    assert result["triggered"] == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT message, payload FROM watch_events WHERE event_type='TRIGGERED'"
        ).fetchone()
    payload = json.loads(row[1])
    assert "贵州茅台" in row[0]
    assert payload["name"] == "贵州茅台"
    assert payload["pool_name"] == "自选股"
    assert payload["observations"] == [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "pool_name": "自选股",
            "bar_time": "2026-07-13T10:00:00+08:00",
            "current_price": 101.0,
            "trigger_price": 100.0,
            "invalidation_price": 92.0,
            "distance_to_trigger_pct": -1.0,
            "status": "TRIGGERED",
        }
    ]


def test_intraday_monitor_prefers_dedicated_reference_trigger(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE watch_daily_snapshot ADD COLUMN intraday_trigger_price REAL")
        conn.execute(
            "UPDATE watch_daily_snapshot SET trigger_price=90, intraday_trigger_price=100"
        )
        conn.commit()
    adapter = FakeAdapter({"600519": _bar("2026-07-13 10:00", 101.0)})

    result = IntradayWatchMonitor(db_path=db_path, adapter=adapter).run_once(
        datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI)
    )

    assert result["triggered"] == 1


def test_new_urgent_event_invokes_notification_dispatcher(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    adapter = FakeAdapter({"600519": _bar("2026-07-13 10:00", 101.0)})
    notified = []
    monitor = IntradayWatchMonitor(
        db_path=db_path,
        adapter=adapter,
        notifier=lambda trade_date: notified.append(trade_date),
    )

    monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    assert notified == ["2026-07-13"]


def test_chasing_price_above_three_percent_does_not_trigger(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    adapter = FakeAdapter({"600519": _bar("2026-07-13 10:00", 103.01)})

    result = IntradayWatchMonitor(db_path=db_path, adapter=adapter).run_once(
        datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI)
    )

    assert result["triggered"] == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM watch_events").fetchone()[0] == 0


def test_transient_missing_bar_recovers_before_alerting(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    adapter = FakeAdapter({"600519": _bar("2026-07-13 09:55", 101.0)})
    monitor = IntradayWatchMonitor(db_path=db_path, adapter=adapter)

    first = monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))
    adapter.bars_by_symbol["600519"] = _bar("2026-07-13 10:05", 99.0)
    second = monitor.run_once(datetime(2026, 7, 13, 10, 6, tzinfo=SHANGHAI))

    assert first["blocked"] == 0
    assert second["blocked"] == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM watch_events").fetchone()[0] == 0


def test_consecutive_missing_bars_create_one_data_blocked_event(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    adapter = FakeAdapter({"600519": _bar("2026-07-13 09:55", 101.0)})
    monitor = IntradayWatchMonitor(db_path=db_path, adapter=adapter)

    first = monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))
    second = monitor.run_once(datetime(2026, 7, 13, 10, 6, tzinfo=SHANGHAI))
    third = monitor.run_once(datetime(2026, 7, 13, 10, 11, tzinfo=SHANGHAI))

    assert first["blocked"] == 0
    assert second["blocked"] == 1
    assert third["blocked"] == 0
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT event_type, dedupe_key FROM watch_events ORDER BY created_at"
        ).fetchall()
    assert rows == [
        ("DATA_BLOCKED", "intraday:data:2026-07-13:no_new_completed_bar")
    ]


def test_missing_bar_recovery_tracks_only_symbols_that_actually_failed(tmp_path):
    db_path = tmp_path / "watch.db"
    snapshots = [
        ("2026-07-10", "600002", "IN_ZONE", 100.0, 92.0, "setup-2", 98.0, "manual"),
        ("2026-07-10", "600519", "IN_ZONE", 100.0, 92.0, "setup-1", 98.0, "manual"),
    ]
    _create_db(db_path, snapshots)
    adapter = FakeAdapter(
        {
            "600002": _bar("2026-07-13 09:55", 99.0),
            "600519": _bar("2026-07-13 09:55", 99.0),
        }
    )
    monitor = IntradayWatchMonitor(db_path=db_path, adapter=adapter)
    monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))
    monitor.run_once(datetime(2026, 7, 13, 10, 6, tzinfo=SHANGHAI))

    adapter.bars_by_symbol["600519"] = _bar("2026-07-13 10:10", 99.0)
    monitor.run_once(datetime(2026, 7, 13, 10, 11, tzinfo=SHANGHAI))

    with sqlite3.connect(db_path) as conn:
        unresolved = conn.execute(
            "SELECT resolved_at, payload FROM watch_events WHERE event_type='DATA_BLOCKED'"
        ).fetchone()
    assert unresolved[0] is None
    assert json.loads(unresolved[1])["failed_symbols"] == ["600002"]

    adapter.bars_by_symbol["600002"] = _bar("2026-07-13 10:15", 99.0)
    adapter.bars_by_symbol["600519"] = _bar("2026-07-13 10:15", 99.0)
    monitor.run_once(datetime(2026, 7, 13, 10, 16, tzinfo=SHANGHAI))

    with sqlite3.connect(db_path) as conn:
        resolved = conn.execute(
            "SELECT resolved_at FROM watch_events WHERE event_type='DATA_BLOCKED'"
        ).fetchone()[0]
        recovered = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='DATA_RECOVERED'"
        ).fetchone()[0]
    assert resolved
    assert recovered == 1


@pytest.mark.parametrize("reason", ["tdx_unavailable", "tdx_request_failed"])
def test_connection_blocks_recover_only_after_each_failed_symbol_has_valid_bar(
    tmp_path, monkeypatch, reason
):
    db_path = tmp_path / "watch.db"
    snapshots = [
        ("2026-07-10", "600002", "IN_ZONE", 100.0, 92.0, "setup-2", 98.0, "manual"),
        ("2026-07-10", "600519", "IN_ZONE", 100.0, 92.0, "setup-1", 98.0, "manual"),
    ]
    _create_db(db_path, snapshots)
    monkeypatch.setattr(intraday_module, "get_tdx_adapter", lambda: None)
    adapter = FakeAdapter(error=ConnectionError("offline"))
    monitor = IntradayWatchMonitor(
        db_path=db_path, adapter=adapter if reason == "tdx_request_failed" else None
    )
    monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    with sqlite3.connect(db_path) as conn:
        payload = conn.execute(
            "SELECT payload FROM watch_events WHERE event_type='DATA_BLOCKED'"
        ).fetchone()[0]
    assert json.loads(payload)["failed_symbols"] == ["600002", "600519"]

    adapter.error = None
    monitor.adapter = adapter
    adapter.bars_by_symbol = {
        "600002": _bar("2026-07-13 10:05", 99.0),
        "600519": _bar("2026-07-13 10:05", -1.0),
    }
    monitor.run_once(datetime(2026, 7, 13, 10, 6, tzinfo=SHANGHAI))
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT resolved_at, payload FROM watch_events WHERE dedupe_key=?",
            (f"intraday:data:2026-07-13:{reason}",),
        ).fetchone()
    assert row[0] is None
    assert json.loads(row[1])["failed_symbols"] == ["600519"]

    adapter.bars_by_symbol = {
        "600002": _bar("2026-07-13 10:10", 99.0),
        "600519": _bar("2026-07-13 10:10", 99.0),
    }
    monitor.run_once(datetime(2026, 7, 13, 10, 11, tzinfo=SHANGHAI))
    with sqlite3.connect(db_path) as conn:
        resolved = conn.execute(
            "SELECT resolved_at FROM watch_events WHERE dedupe_key=?",
            (f"intraday:data:2026-07-13:{reason}",),
        ).fetchone()[0]
        recovered = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='DATA_RECOVERED' "
            "AND json_extract(payload, '$.reason')=?", (reason,),
        ).fetchone()[0]
    assert resolved
    assert recovered == 1


@pytest.mark.parametrize("reason", ["tdx_unavailable", "tdx_request_failed"])
def test_legacy_connection_block_sample_symbol_recovers_once(tmp_path, reason):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    monitor = IntradayWatchMonitor(
        db_path=db_path,
        adapter=FakeAdapter({"600519": _bar("2026-07-13 10:00", 99.0)}),
    )
    with sqlite3.connect(db_path) as conn:
        monitor._data_blocked(
            conn, current_date="2026-07-13", reason=reason, symbol="600519"
        )
        conn.execute(
            "UPDATE watch_events SET payload=? WHERE event_type='DATA_BLOCKED'",
            (json.dumps({"reason": reason, "sample_symbol": "600519"}),),
        )
    monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))
    monitor.adapter.bars_by_symbol["600519"] = _bar("2026-07-13 10:05", 99.0)
    monitor.run_once(datetime(2026, 7, 13, 10, 6, tzinfo=SHANGHAI))
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT resolved_at FROM watch_events WHERE event_type='DATA_BLOCKED'"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='DATA_RECOVERED'"
        ).fetchone()[0] == 1


def test_bar_datetime_rejects_none_and_nat():
    assert IntradayWatchMonitor._bar_datetime(None) is None
    assert IntradayWatchMonitor._bar_datetime(pd.NaT) is None


def test_successful_bar_resolves_only_matching_intraday_block(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    adapter = FakeAdapter({"600519": _bar("2026-07-13 09:55", 101.0)})
    monitor = IntradayWatchMonitor(db_path=db_path, adapter=adapter)
    monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))
    monitor.run_once(datetime(2026, 7, 13, 10, 6, tzinfo=SHANGHAI))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO watch_events (
                event_id, dedupe_key, trade_date, event_type, severity,
                title, message, status, payload, created_at, updated_at
            ) VALUES (
                'daily-block', 'data:2026-07-13:industry_state', '2026-07-13',
                'DATA_BLOCKED', 'CRITICAL', '日线数据阻断', 'industry_state',
                'UNREAD', '{"reason":"industry_state"}',
                '2026-07-13 10:06:00', '2026-07-13 10:06:00'
            )
            """
        )

    adapter.bars_by_symbol["600519"] = _bar("2026-07-13 10:10", 99.0)
    result = monitor.run_once(datetime(2026, 7, 13, 10, 11, tzinfo=SHANGHAI))

    assert result["blocked"] == 0
    with sqlite3.connect(db_path) as conn:
        intraday = conn.execute(
            "SELECT resolved_at FROM watch_events WHERE dedupe_key=?",
            ("intraday:data:2026-07-13:no_new_completed_bar",),
        ).fetchone()
        daily = conn.execute(
            "SELECT resolved_at FROM watch_events WHERE event_id='daily-block'"
        ).fetchone()
        recovered = conn.execute(
            "SELECT event_type, json_extract(payload, '$.reason') FROM watch_events "
            "WHERE event_type='DATA_RECOVERED'"
        ).fetchall()
    assert intraday[0]
    assert daily == (None,)
    assert recovered == [("DATA_RECOVERED", "no_new_completed_bar")]


def test_adapter_outage_does_not_falsely_resolve_missing_bar_block(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    adapter = FakeAdapter({"600519": _bar("2026-07-13 09:55", 101.0)})
    monitor = IntradayWatchMonitor(db_path=db_path, adapter=adapter)
    monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))
    monitor.run_once(datetime(2026, 7, 13, 10, 6, tzinfo=SHANGHAI))

    adapter.error = ConnectionError("offline")
    result = monitor.run_once(datetime(2026, 7, 13, 10, 11, tzinfo=SHANGHAI))

    assert result["blocked"] == 1
    with sqlite3.connect(db_path) as conn:
        missing_bar = conn.execute(
            "SELECT resolved_at FROM watch_events WHERE dedupe_key=?",
            ("intraday:data:2026-07-13:no_new_completed_bar",),
        ).fetchone()
        recovered = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='DATA_RECOVERED'"
        ).fetchone()[0]
    assert missing_bar == (None,)
    assert recovered == 0


def test_lunch_break_skips_network_and_more_than_50_symbols_is_capped(tmp_path):
    db_path = tmp_path / "watch.db"
    snapshots = [
        (
            "2026-07-10",
            f"{symbol:06d}",
            "IN_ZONE",
            100.0,
            92.0,
            f"setup-{symbol}",
            98.0,
            "manual",
        )
        for symbol in range(1, 52)
    ]
    _create_db(db_path, snapshots)
    adapter = FakeAdapter()
    monitor = IntradayWatchMonitor(db_path=db_path, adapter=adapter)

    lunch = monitor.run_once(datetime(2026, 7, 13, 12, 0, tzinfo=SHANGHAI))
    active = monitor.run_once(datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI))

    assert lunch["status"] == "outside_session"
    assert active["monitored"] == 50
    assert len(adapter.calls) == 50
    with sqlite3.connect(db_path) as conn:
        event_types = [row[0] for row in conn.execute("SELECT event_type FROM watch_events")]
        assert "DATA_BLOCKED" in event_types


def test_adapter_failure_never_uses_snapshot_price_for_trigger(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    adapter = FakeAdapter(error=ConnectionError("offline"))

    result = IntradayWatchMonitor(db_path=db_path, adapter=adapter).run_once(
        datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI)
    )

    assert result["triggered"] == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT event_type FROM watch_events"
        ).fetchall() == [("DATA_BLOCKED",)]


def test_poll_schedule_exits_at_1505_and_jumps_over_lunch():
    assert IntradayWatchMonitor._next_poll(
        datetime(2026, 7, 13, 11, 30, tzinfo=SHANGHAI)
    ) == datetime(2026, 7, 13, 11, 30, 30, tzinfo=SHANGHAI)
    assert IntradayWatchMonitor._next_poll(
        datetime(2026, 7, 13, 11, 30, 31, tzinfo=SHANGHAI)
    ) == datetime(2026, 7, 13, 13, 5, 30, tzinfo=SHANGHAI)
    assert (
        IntradayWatchMonitor._next_poll(
            datetime(2026, 7, 13, 15, 5, tzinfo=SHANGHAI)
        )
        is None
    )


def test_poll_schedule_waits_for_completed_bar_publish_grace():
    assert IntradayWatchMonitor._next_poll(
        datetime(2026, 7, 13, 9, 35, tzinfo=SHANGHAI)
    ) == datetime(2026, 7, 13, 9, 35, 30, tzinfo=SHANGHAI)
    assert IntradayWatchMonitor._next_poll(
        datetime(2026, 7, 13, 9, 35, 30, tzinfo=SHANGHAI)
    ) == datetime(2026, 7, 13, 9, 40, 30, tzinfo=SHANGHAI)


def test_run_until_close_exits_immediately_on_closed_day_after_login(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE trading_calendar (trade_date TEXT, is_open INTEGER)")
        conn.execute("INSERT INTO trading_calendar VALUES ('2026-07-18', 0)")

    monitor = IntradayWatchMonitor(
        db_path=db_path,
        now_provider=lambda: datetime(2026, 7, 18, 10, 0, tzinfo=SHANGHAI),
        sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )

    result = monitor.run_until_close()

    assert result == {
        "success": True,
        "status": "market_closed",
        "runs": 0,
        "triggered": 0,
        "blocked": 0,
    }


def test_run_until_close_exits_on_exchange_holiday_without_calendar_row(tmp_path):
    db_path = tmp_path / "watch.db"
    _create_db(db_path)
    monitor = IntradayWatchMonitor(
        db_path=db_path,
        now_provider=lambda: datetime(2026, 9, 25, 10, 0, tzinfo=SHANGHAI),
        sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )

    assert monitor.run_until_close()["status"] == "market_closed"


def test_run_until_close_sleeps_until_next_scheduled_window(tmp_path):
    times = iter(
        [
            datetime(2026, 7, 14, 8, 0, tzinfo=SHANGHAI),
            datetime(2026, 7, 15, 8, 0, tzinfo=SHANGHAI),
        ]
    )
    sleeps = []
    monitor = IntradayWatchMonitor(
        db_path=tmp_path / "watch.db",
        now_provider=lambda: next(times),
        sleep=sleeps.append,
    )

    result = monitor.run_until_close()

    assert result["success"] is True
    assert result["runs"] == 0
    assert sleeps == [5730.0]


def test_run_until_close_polls_each_completed_five_minute_bar_once(tmp_path):
    class Clock:
        def __init__(self):
            self.current = datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI)
            self.next_values = iter(
                [
                    datetime(2026, 7, 13, 10, 2, tzinfo=SHANGHAI),
                    datetime(2026, 7, 13, 10, 5, 30, tzinfo=SHANGHAI),
                    datetime(2026, 7, 13, 15, 5, tzinfo=SHANGHAI),
                ]
            )

        def now(self):
            return self.current

        def sleep(self, _seconds):
            self.current = next(self.next_values)

    clock = Clock()
    monitor = IntradayWatchMonitor(
        db_path=tmp_path / "watch.db",
        now_provider=clock.now,
        sleep=clock.sleep,
    )
    polled = []
    monitor.run_once = lambda now: polled.append(now) or {
        "triggered": 0,
        "blocked": 0,
    }

    result = monitor.run_until_close()

    assert result["runs"] == 2
    assert [item.strftime("%H:%M:%S") for item in polled] == ["10:01:00", "10:05:30"]
