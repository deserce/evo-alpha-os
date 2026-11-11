import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, timedelta

import pytest


class _RecordingConnection(sqlite3.Connection):
    """记录 Watch Engine 发出的 SQL，便于对真实查询做执行计划回归。"""

    def execute(self, sql, parameters=(), /):
        if not hasattr(self, "recorded_queries"):
            self.recorded_queries = []
        self.recorded_queries.append((sql, parameters))
        return super().execute(sql, parameters)


def _trade_dates(count: int = 62) -> list[str]:
    values: list[str] = []
    current = date(2026, 1, 2)
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def _make_db(path, *, industry_state: str | None = "I3", history_days=62) -> tuple[str, str]:
    dates = _trade_dates(history_days)
    zone_date = dates[-1]
    trigger_date = (date.fromisoformat(zone_date) + timedelta(days=1)).isoformat()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE stock_pools (
            pool_id TEXT PRIMARY KEY, name TEXT, monitor_enabled INTEGER,
            is_default INTEGER DEFAULT 0
        );
        CREATE TABLE stock_pool_members (
            pool_id TEXT, symbol TEXT, added_at TEXT,
            PRIMARY KEY (pool_id, symbol)
        );
        CREATE TABLE stock_daily_prices (
            symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL
        );
        CREATE TABLE stock_lifecycle_state (
            trade_date TEXT, symbol TEXT, name TEXT, market_state TEXT,
            industry_state TEXT, rps_50 REAL, structure_score REAL,
            data_status TEXT, universe_source TEXT,
            PRIMARY KEY (trade_date, symbol)
        );
        """
    )
    conn.execute("INSERT INTO stock_pools VALUES ('manual', '重点池', 1, 0)")
    conn.execute(
        "INSERT INTO stock_pool_members VALUES ('manual', '688001', ?)",
        (dates[0] + " 09:30:00",),
    )
    rows = []
    for index, trade_date in enumerate(dates):
        high = 100.0 if index == len(dates) - 22 else 91.0
        volume = 1000.0
        if index >= len(dates) - 3:
            volume = float(700 - (index - (len(dates) - 3)) * 100)
        rows.append(("688001", trade_date, 91.0, high, 88.5, 90.0, volume))
    conn.executemany("INSERT INTO stock_daily_prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    lifecycle_sql = (
        "INSERT INTO stock_lifecycle_state VALUES "
        "(?, '688001', '测试股份', 'M3', ?, 90, 80, 'OK', 'manual')"
    )
    conn.execute(
        lifecycle_sql,
        (zone_date, industry_state),
    )
    conn.commit()
    conn.close()
    return zone_date, trigger_date


@pytest.fixture()
def watch_db(tmp_path):
    db_path = tmp_path / "watch.db"
    zone_date, trigger_date = _make_db(db_path)
    return db_path, zone_date, trigger_date


def _add_historical_day(db_path, trade_date, *, close=92, industry="I3"):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 90, 93, 89.5, ?, 800)",
            (trade_date, close),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', ?, 90, 80, 'OK', 'manual')",
            (trade_date, industry),
        )


def _legacy_quality(db_path, *trade_dates):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS data_quality_report "
            "(trade_date TEXT, module TEXT, market TEXT, status TEXT, created_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO data_quality_report VALUES (?, 'stock_daily', 'SH', 'PASS', ?)",
            [(day, day + " 16:00:00") for day in trade_dates],
        )


def test_legacy_verified_seed_preserves_zone_and_only_writes_target(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _legacy_quality(db_path, zone_date)
    _add_historical_day(db_path, trigger_date)
    with sqlite3.connect(db_path) as conn:
        seed_before = conn.execute("SELECT * FROM watch_daily_snapshot").fetchall()
        events_before = conn.execute("SELECT * FROM watch_events").fetchall()
    result = engine.run_daily(trigger_date, historical=True)
    assert result["success"] is True
    with sqlite3.connect(db_path) as conn:
        state, previous, setup_id, raw_factors = conn.execute(
            "SELECT watch_state, previous_state, setup_id, factors FROM watch_daily_snapshot "
            "WHERE trade_date=?", (trigger_date,),
        ).fetchone()
        assert conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date=?", (zone_date,)
        ).fetchall() == seed_before
        assert conn.execute("SELECT * FROM watch_events").fetchall() == events_before
        assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone() == (0,)
    assert (state, previous, setup_id) == ("TRIGGERED", "IN_ZONE", f"688001:{zone_date}")
    seed = json.loads(raw_factors)["watch_rebuild"]["legacy_seed"]
    assert seed["mode"] == "legacy_verified"
    assert seed["trade_date"] == zone_date
    assert len(seed["input_signature"]) == 64
    assert engine.check_historical_snapshots(trigger_date)["trusted"] is True


def test_legacy_verified_seed_preserves_cooldown_and_excludes_future_event(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date)
    engine.run(trigger_date)
    _legacy_quality(db_path, zone_date, trigger_date)
    later = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, later, close=90)
    with sqlite3.connect(db_path) as conn:
        engine._event(
            conn, dedupe_key="state:future", trade_date="2099-01-01", event_type="TRIGGERED",
            severity="HIGH", title="future", message="future", symbol="688001",
            setup_id=f"688001:{zone_date}",
        )
    assert engine.run_daily(later, historical=True)["success"]
    with sqlite3.connect(db_path) as conn:
        state, factors = conn.execute(
            "SELECT watch_state, factors FROM watch_daily_snapshot WHERE trade_date=?", (later,)
        ).fetchone()
    assert state == "TRIGGERED"
    assert json.loads(factors)["watch_rebuild"]["trigger_date"] == trigger_date
    assert engine.check_historical_snapshots(later)["trusted"]


@pytest.mark.parametrize("event_kind", ["intraday", "stale", "daily_price", "older_anchor"])
def test_legacy_trigger_uses_latest_daily_transition_not_notification(watch_db, event_kind):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date)
    engine.run(trigger_date)
    _legacy_quality(db_path, zone_date, trigger_date)
    target = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, target, close=90)
    with sqlite3.connect(db_path) as conn:
        if event_kind == "older_anchor":
            columns = [row[1] for row in conn.execute("PRAGMA table_info(watch_daily_snapshot)")]
            row = list(conn.execute(
                "SELECT * FROM watch_daily_snapshot WHERE trade_date=?", (trigger_date,)
            ).fetchone())
            row[columns.index("trade_date")] = _trade_dates()[-3]
            conn.execute(
                f"INSERT INTO watch_daily_snapshot VALUES ({','.join('?' for _ in columns)})", row
            )
        else:
            payload = {"current_price": 999, "trigger_price": 998, "invalidation_price": 997}
            if event_kind == "intraday":
                payload["source"] = "intraday_5min"
            conn.execute("UPDATE watch_events SET payload=? WHERE event_type='TRIGGERED'",
                         (json.dumps(payload),))
            if event_kind == "stale":
                conn.execute("UPDATE watch_events SET trade_date=? WHERE event_type='TRIGGERED'",
                             (zone_date,))
        before = conn.execute("SELECT * FROM watch_events").fetchall()
    result = engine.run_daily(target, historical=True)
    assert result["success"], result
    with sqlite3.connect(db_path) as conn:
        state, setup, raw_factors = conn.execute(
            "SELECT watch_state,setup_id,factors FROM watch_daily_snapshot WHERE trade_date=?",
            (target,),
        ).fetchone()
        assert conn.execute("SELECT * FROM watch_events").fetchall() == before
    assert (state, setup) == ("TRIGGERED", f"688001:{zone_date}")
    assert json.loads(raw_factors)["watch_rebuild"]["trigger_date"] == trigger_date


@pytest.mark.parametrize("ratio", [0.9865334488984435, 0.9920098343434771, 0.9989019607796388])
@pytest.mark.parametrize("state", ["IN_ZONE", "TRIGGERED", "INVALIDATED", "WATCHING"])
def test_legacy_uniform_qfq_rebase_preserves_state_and_old_rows(watch_db, ratio, state):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    if state == "WATCHING":
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE stock_daily_prices SET volume=1000")
    engine.run(zone_date)
    days = [zone_date]
    if state in {"TRIGGERED", "INVALIDATED"}:
        _add_historical_day(db_path, trigger_date, close=83 if state == "INVALIDATED" else 92)
        if state == "INVALIDATED":
            with sqlite3.connect(db_path) as conn:
                conn.execute("UPDATE stock_daily_prices SET low=82 WHERE trade_date=?",
                             (trigger_date,))
        engine.run(trigger_date)
        days.append(trigger_date)
    _legacy_quality(db_path, *days)
    seed_date = days[-1]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        original = dict(conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date=?", (seed_date,)
        ).fetchone())
        assert original["watch_state"] == state
        old_rows = [tuple(row) for row in conn.execute("SELECT * FROM watch_daily_snapshot")]
        conn.execute(
            "UPDATE stock_daily_prices SET open=open*?,high=high*?,low=low*?,close=close*?",
            (ratio,) * 4,
        )
    target = (date.fromisoformat(seed_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, target, close=90)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE stock_daily_prices SET open=open*?,high=high*?,low=low*?,close=close*? "
            "WHERE trade_date=?", (ratio,) * 4 + (target,),
        )
    result = engine.run_daily(target, historical=True)
    assert result["success"], result
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date<>?", (target,)
        ).fetchall() == old_rows
        previous, setup, raw_factors = conn.execute(
            "SELECT previous_state,setup_id,factors FROM watch_daily_snapshot WHERE trade_date=?",
            (target,),
        ).fetchone()
        assert conn.execute("SELECT count(*) FROM notification_deliveries").fetchone() == (0,)
    assert previous == state
    if state in {"IN_ZONE", "TRIGGERED"}:
        assert setup == original["setup_id"]
    metadata = json.loads(raw_factors)["watch_rebuild"]
    assert metadata["legacy_seed"]["price_rebase_ratio"] == pytest.approx(ratio)
    if state == "TRIGGERED":
        assert metadata["trigger_date"] == trigger_date
    assert engine.check_historical_snapshots(target)["trusted"]


@pytest.mark.parametrize("change", [
    "close", "high", "low", "volume", "macd_history", "lifecycle", "anchor_version",
    "anchor_quality", "anchor_price", "anchor_parent", "anchor_missing",
])
def test_legacy_rebase_keeps_nonuniform_changes_and_bad_anchors_blocked(tmp_path, change):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "watch.db"
    zone_date, trigger_date = _make_db(db_path, history_days=121)
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date)
    engine.run(trigger_date)
    current = date.fromisoformat(trigger_date)
    seed_date = (current + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, seed_date, close=90)
    engine.run(seed_date)
    _legacy_quality(db_path, zone_date, trigger_date, seed_date)
    target = (current + timedelta(days=2)).isoformat()
    _add_historical_day(db_path, target, close=90)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE stock_daily_prices SET open=open*0.98653,high=high*0.98653,"
            "low=low*0.98653,close=close*0.98653"
        )
        if change in {"close", "high", "low", "volume"}:
            conn.execute(
                f"UPDATE stock_daily_prices SET {change}={change}*1.001 WHERE trade_date=?",
                (seed_date,),
            )
        elif change == "macd_history":
            conn.execute("UPDATE stock_daily_prices SET close=close*1.001 WHERE trade_date=?",
                         (_trade_dates()[10],))
        elif change == "lifecycle":
            conn.execute("UPDATE stock_lifecycle_state SET rps_50=rps_50+0.02 WHERE trade_date=?",
                         (trigger_date,))
        elif change == "anchor_version":
            conn.execute("UPDATE watch_daily_snapshot SET rule_version=999 WHERE trade_date=?",
                         (trigger_date,))
        elif change == "anchor_quality":
            conn.execute("UPDATE data_quality_report SET status='FAIL' WHERE trade_date=?",
                         (trigger_date,))
        elif change == "anchor_price":
            conn.execute("UPDATE watch_daily_snapshot SET trigger_price=trigger_price+1 "
                         "WHERE trade_date=?", (trigger_date,))
        elif change == "anchor_parent":
            conn.execute("UPDATE watch_daily_snapshot SET watch_state='WATCHING' WHERE trade_date=?",
                         (zone_date,))
        else:
            conn.execute("UPDATE watch_daily_snapshot SET previous_state='TRIGGERED' "
                         "WHERE trade_date=?", (trigger_date,))
        rows = conn.execute("SELECT * FROM watch_daily_snapshot").fetchall()
        events = conn.execute("SELECT * FROM watch_events").fetchall()
    if change in {"close", "high", "low", "volume", "macd_history", "lifecycle"}:
        # 真修订可以从更早可信种子重放，但绝不能直接通过 uniform-rebase 门。
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            member = engine._monitored_members(conn)[0]
            previous = engine._previous_snapshot(conn, member.symbol, target)
            seed, errors = engine._legacy_seed(
                conn, member, target, previous, engine._historical_rules(conn), allow_warmup=False
            )
            assert seed is None
            assert errors[0].startswith("legacy_seed_untrusted:")
            assert conn.total_changes == 0
        return
    result = engine.run_daily(target, historical=True)
    assert not result["success"], result
    assert result["blocked_reasons"][0].startswith("legacy_seed_untrusted:")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT watch_state,data_status FROM watch_daily_snapshot WHERE trade_date=?", (target,)
        ).fetchone() == ("UNKNOWN", "BLOCKED")
        assert conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date<>?", (target,)
        ).fetchall() == rows
        assert conn.execute("SELECT * FROM watch_events").fetchall() == events


def test_legacy_rebase_read_only_seed_has_bounded_window_and_stable_signature(tmp_path, monkeypatch):
    from contextlib import closing
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "watch.db"
    zone_date, trigger_date = _make_db(db_path, history_days=121)
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date)
    engine.run(trigger_date)
    _legacy_quality(db_path, zone_date, trigger_date)
    target = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, target, close=90)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE stock_daily_prices SET open=open*0.98653,high=high*0.98653,"
                     "low=low*0.98653,close=close*0.98653")
    before = db_path.read_bytes()
    calls = []
    original = engine._prices

    def prices(conn, symbol, trade_date):
        rows = original(conn, symbol, trade_date)
        calls.append((symbol, trade_date, len(rows)))
        assert len(rows) <= 121
        return rows

    monkeypatch.setattr(engine, "_prices", prices)
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rules = engine._historical_rules(conn)
        member = engine._monitored_members(conn)[0]
        previous = engine._previous_snapshot(conn, member.symbol, target)
        seed, errors = engine._legacy_seed(conn, member, target, previous, rules)
        assert errors == []
        assert seed["watch_state"] == "TRIGGERED"
        assert len(calls) <= 3
        assert max(count for _, _, count in calls) == 121
        calls.clear()
        again, errors = engine._legacy_seed(conn, member, target, previous, rules)
        assert errors == []
        assert again == seed
        assert len(calls) <= 3
        assert conn.total_changes == 0
    assert db_path.read_bytes() == before


@pytest.mark.parametrize("bad", [None, "output_signature", "row", "nonuniform", "input_only"])
def test_signed_historical_seed_only_rebases_intact_uniform_qfq(watch_db, bad):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    assert engine.run_daily(zone_date, historical=True)["success"]
    _add_historical_day(db_path, trigger_date)
    assert engine.run_daily(trigger_date, historical=True)["success"]
    _legacy_quality(db_path, zone_date, trigger_date)
    target = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, target, close=90)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        saved = dict(conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date=?", (trigger_date,)
        ).fetchone())
        factors = json.loads(saved["factors"])
        output_signature = factors["watch_rebuild"]["output_signature"]
        if bad != "input_only":
            conn.execute("UPDATE stock_daily_prices SET open=open*0.98653,high=high*0.98653,"
                         "low=low*0.98653,close=close*0.98653")
        if bad == "output_signature":
            factors["watch_rebuild"]["output_signature"] = "tampered"
        elif bad == "row":
            conn.execute("UPDATE watch_daily_snapshot SET current_price=current_price+1 "
                         "WHERE trade_date=?", (trigger_date,))
        elif bad == "nonuniform":
            conn.execute("UPDATE stock_daily_prices SET volume=volume+1 WHERE trade_date=?",
                         (zone_date,))
        elif bad == "input_only":
            factors["watch_rebuild"]["input_signature"] = "stale"
            saved["factors"] = factors
            factors["watch_rebuild"]["output_signature"] = engine._snapshot_signature(saved)
        conn.execute("UPDATE watch_daily_snapshot SET factors=? WHERE trade_date=?",
                     (json.dumps(factors), trigger_date))
        before = [tuple(row) for row in conn.execute("SELECT * FROM watch_daily_snapshot")]
    result = engine.run_daily(target, historical=True)
    assert result["success"] is (bad is None), result
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date<>?", (target,)
        ).fetchall() == before
        state, previous, setup, raw_factors, status = conn.execute(
            "SELECT watch_state,previous_state,setup_id,factors,data_status "
            "FROM watch_daily_snapshot WHERE trade_date=?", (target,),
        ).fetchone()
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('watch_events','notification_deliveries')"
        ).fetchone() == (0,)
    if bad is None:
        assert (state, previous, setup) == ("TRIGGERED", "TRIGGERED", f"688001:{zone_date}")
        metadata = json.loads(raw_factors)["watch_rebuild"]
        assert metadata["trigger_date"] == trigger_date
        assert metadata["legacy_seed"]["source_output_signature"] == output_signature
        assert metadata["legacy_seed"]["price_rebase_ratio"] == pytest.approx(0.98653)
        assert engine.check_historical_snapshots(target)["trusted"]
    else:
        assert (state, status) == ("UNKNOWN", "BLOCKED")
        if bad == "nonuniform":
            assert "no_verified_seed" in result["blocked_reasons"][0]
        else:
            assert result["blocked_reasons"] == ["previous_snapshot_untrusted"]


def _warmup_revision_history(watch_db, *, old_watching=False):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date)
    engine.run(trigger_date)
    days = [zone_date, trigger_date]
    current = date.fromisoformat(trigger_date)
    for _ in range(4):
        current += timedelta(days=1)
        while current.weekday() >= 5:
            current += timedelta(days=1)
        days.append(current.isoformat())
        _add_historical_day(db_path, days[-1], close=90)
        engine.run(days[-1])
    current += timedelta(days=1)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    target = current.isoformat()
    _add_historical_day(db_path, target, close=90)
    _legacy_quality(db_path, *days)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE trading_calendar (trade_date TEXT PRIMARY KEY,is_open INTEGER)")
        conn.executemany("INSERT INTO trading_calendar VALUES (?,1)",
                         [(day,) for day in _trade_dates() + days[1:] + [target]])
        conn.execute("UPDATE stock_lifecycle_state SET rps_50=89 WHERE trade_date=?",
                     (trigger_date,))
        if old_watching:
            conn.execute("UPDATE watch_daily_snapshot SET watch_state='WATCHING',setup_id=NULL "
                         "WHERE trade_date=?", (days[-1],))
    return engine, days, target


@pytest.mark.parametrize("old_watching", [False, True])
def test_historical_warmup_recovers_revised_anchor_without_old_writes(watch_db, monkeypatch,
                                                                   old_watching):
    from contextlib import closing

    db_path = watch_db[0]
    engine, days, target = _warmup_revision_history(watch_db, old_watching=old_watching)
    tables = ("watch_daily_snapshot", "watch_events", "notification_deliveries",
              "stock_pools", "stock_pool_members")
    with sqlite3.connect(db_path) as conn:
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    calls = []
    original = engine._prices

    def prices(conn, symbol, day):
        rows = original(conn, symbol, day)
        calls.append((symbol, day, len(rows)))
        assert len(rows) <= 121
        return rows

    monkeypatch.setattr(engine, "_prices", prices)
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rules = engine._historical_rules(conn)
        member = engine._monitored_members(conn)[0]
        previous, errors = engine._historical_previous(conn, member, target, rules)
        assert errors == [], errors
        assert previous["watch_state"] == "TRIGGERED"
        metadata = json.loads(previous["factors"])["watch_rebuild"]
        assert metadata["mode"] == "warmup_verified"
        assert metadata["trigger_date"] == days[1]
        assert metadata["warmup"]["seed_date"] == days[0]
        assert metadata["warmup"]["start_date"] == days[1]
        assert metadata["warmup"]["end_date"] == days[-1]
        assert metadata["output_signature"] == engine._snapshot_signature(previous)
        assert conn.total_changes == 0
    assert len(calls) <= 82
    calls.clear()
    result = engine.run_daily(target, historical=True)
    assert result["success"], result
    assert len(calls) <= 82
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT * FROM watch_daily_snapshot WHERE trade_date<>?",
                            (target,)).fetchall() == before["watch_daily_snapshot"]
        for table in tables[1:]:
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == before[table]
        state, prior, raw_factors = conn.execute(
            "SELECT watch_state,previous_state,factors FROM watch_daily_snapshot WHERE trade_date=?",
            (target,),
        ).fetchone()
    assert (state, prior) == ("TRIGGERED", "TRIGGERED")
    assert json.loads(raw_factors)["watch_rebuild"]["warmup"]["seed_date"] == days[0]
    assert engine.check_historical_snapshots(target)["trusted"]


@pytest.mark.parametrize("missing", ["quality", "quality_fail", "lifecycle", "price", "bad_bar"])
def test_historical_warmup_missing_middle_source_is_blocked(watch_db, missing):
    db_path = watch_db[0]
    engine, days, target = _warmup_revision_history(watch_db)
    middle = days[2]
    with sqlite3.connect(db_path) as conn:
        if missing == "quality":
            conn.execute("DELETE FROM data_quality_report WHERE trade_date=?", (middle,))
        elif missing == "quality_fail":
            conn.execute("UPDATE data_quality_report SET status='FAIL' WHERE trade_date=?", (middle,))
        elif missing == "lifecycle":
            conn.execute("DELETE FROM stock_lifecycle_state WHERE trade_date=?", (middle,))
        elif missing == "price":
            conn.execute("DELETE FROM stock_daily_prices WHERE trade_date=?", (middle,))
        else:
            conn.execute("UPDATE stock_daily_prices SET low=1000 WHERE trade_date=?", (middle,))
        old = conn.execute("SELECT * FROM watch_daily_snapshot").fetchall()
    result = engine.run_daily(target, historical=True)
    assert not result["success"], result
    expected = "source_invalid_ohlcv" if missing == "bad_bar" else "warmup"
    assert any(expected in error for error in result["blocked_reasons"]), result
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT * FROM watch_daily_snapshot WHERE trade_date<>?",
                            (target,)).fetchall() == old
        assert conn.execute("SELECT watch_state,data_status FROM watch_daily_snapshot "
                            "WHERE trade_date=?", (target,)).fetchone() == ("UNKNOWN", "BLOCKED")


@pytest.mark.parametrize("calendar", ["empty", "invalid_date"])
def test_historical_warmup_early_calendar_error_retains_original_reason(watch_db, calendar):
    from contextlib import closing

    db_path = watch_db[0]
    engine, _, target = _warmup_revision_history(watch_db)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM trading_calendar")
        if calendar == "invalid_date":
            conn.execute("INSERT INTO trading_calendar VALUES ('2025-99-99',1)")
    before = db_path.read_bytes()
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        member = engine._monitored_members(conn)[0]
        seed, errors = engine._historical_warmup(
            conn, member, target, engine._historical_rules(conn), "factors_changed", {"remaining": 81}
        )
        assert seed is None
        assert len(errors) == 1
        assert errors[0].startswith(f"legacy_warmup_untrusted:{target}:")
        assert "trading_days_missing" in errors[0] if calendar == "empty" else "month" in errors[0]
        assert conn.total_changes == 0
    assert db_path.read_bytes() == before


@pytest.mark.parametrize("bad", [
    "status", "version", "price", "zone", "stop", "state", "factors",
    "quality_failed", "quality_missing", "lifecycle", "history", "bad_bar", "gap",
])
def test_unverifiable_legacy_seed_is_blocked_not_reset(watch_db, bad):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _legacy_quality(db_path, zone_date)
    _add_historical_day(db_path, trigger_date)
    with sqlite3.connect(db_path) as conn:
        statements = {
            "status": "UPDATE watch_daily_snapshot SET data_status='BLOCKED'",
            "version": "UPDATE watch_daily_snapshot SET rule_version=999",
            "price": "UPDATE watch_daily_snapshot SET current_price=999",
            "zone": "UPDATE watch_daily_snapshot SET zone_low=1",
            "stop": "UPDATE watch_daily_snapshot SET invalidation_price=1",
            "state": "UPDATE watch_daily_snapshot SET watch_state='TRIGGERED'",
            "factors": "UPDATE watch_daily_snapshot SET factors='{}'",
            "quality_failed": "UPDATE data_quality_report SET status='FAIL'",
            "quality_missing": "DELETE FROM data_quality_report",
            "lifecycle": "UPDATE stock_lifecycle_state SET structure_score=79 "
                         f"WHERE trade_date='{zone_date}'",
            "history": "UPDATE stock_daily_prices SET volume=volume+1 "
                       f"WHERE trade_date='{_trade_dates()[-3]}'",
            "bad_bar": "UPDATE stock_daily_prices SET low=1000 "
                       f"WHERE trade_date='{zone_date}'",
            "gap": "UPDATE watch_daily_snapshot SET trade_date='2026-01-01'",
        }
        conn.execute(statements[bad])
        previous = conn.execute("SELECT * FROM watch_daily_snapshot").fetchall()
    result = engine.run_daily(trigger_date, historical=True)
    assert result["success"] is False
    assert any("legacy" in reason for reason in result["blocked_reasons"])
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT watch_state, data_status FROM watch_daily_snapshot WHERE trade_date=?",
            (trigger_date,),
        ).fetchone() == ("UNKNOWN", "BLOCKED")
        assert conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date<>?", (trigger_date,)
        ).fetchall() == previous


def test_legacy_seed_verification_is_bounded_and_read_only(watch_db, monkeypatch):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _legacy_quality(db_path, zone_date)
    _add_historical_day(db_path, trigger_date)
    assert engine.run_daily(trigger_date, historical=True)["success"]
    before = db_path.read_bytes()
    calls = []
    original = engine._prices

    def prices(conn, symbol, trade_date):
        result = original(conn, symbol, trade_date)
        calls.append((symbol, trade_date, len(result)))
        assert len(result) <= 121
        return result

    monkeypatch.setattr(engine, "_prices", prices)
    assert engine.check_historical_snapshots(trigger_date)["trusted"]
    assert len(calls) <= 4
    assert {symbol for symbol, _, _ in calls} == {"688001"}
    assert db_path.read_bytes() == before
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE data_quality_report SET status='FAIL'")
    assert engine.check_historical_snapshots(trigger_date)["trusted"] is False


@pytest.mark.parametrize("without_events", [False, True])
def test_legacy_continuing_cooldown_uses_bounded_anchor(watch_db, monkeypatch, without_events):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date)
    engine.run(trigger_date)
    days = [zone_date, trigger_date]
    current = date.fromisoformat(trigger_date)
    for _ in range(2):
        current += timedelta(days=1)
        days.append(current.isoformat())
        _add_historical_day(db_path, days[-1], close=90)
        engine.run(days[-1])
    _legacy_quality(db_path, *days)
    target = (current + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, target, close=90)
    if without_events:
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM watch_events")
    seen = []
    original = engine._prices

    def prices(conn, symbol, trade_date):
        seen.append(trade_date)
        rows = original(conn, symbol, trade_date)
        assert len(rows) <= 121
        return rows

    monkeypatch.setattr(engine, "_prices", prices)
    assert engine.run_daily(target, historical=True)["success"]
    assert len(seen) <= 4
    with sqlite3.connect(db_path) as conn:
        state, setup, factors = conn.execute(
            "SELECT watch_state, setup_id, factors FROM watch_daily_snapshot WHERE trade_date=?",
            (target,),
        ).fetchone()
    assert (state, setup) == ("TRIGGERED", f"688001:{zone_date}")
    metadata = json.loads(factors)["watch_rebuild"]
    assert metadata["legacy_seed"]["mode"] == "legacy_verified"
    assert metadata["trigger_date"] == trigger_date
    seen.clear()
    assert engine.check_historical_snapshots(target)["trusted"]
    assert len(seen) <= 4


def test_legacy_watching_seed_is_verified_without_fabricated_setup(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE stock_daily_prices SET volume=1000")
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _legacy_quality(db_path, zone_date)
    _add_historical_day(db_path, trigger_date)
    assert engine.run_daily(trigger_date, historical=True)["success"]
    with sqlite3.connect(db_path) as conn:
        state, setup, factors = conn.execute(
            "SELECT previous_state, setup_id, factors FROM watch_daily_snapshot WHERE trade_date=?",
            (trigger_date,),
        ).fetchone()
    assert state == "WATCHING"
    assert json.loads(factors)["watch_rebuild"]["legacy_seed"]["mode"] == "legacy_verified"
    assert setup is None or setup == f"688001:{trigger_date}"


def test_legacy_invalidated_seed_is_verified_against_previous_stop(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, invalid_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, invalid_date, close=83)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE stock_daily_prices SET low=82 WHERE trade_date=?", (invalid_date,))
    engine.run(invalid_date)
    _legacy_quality(db_path, zone_date, invalid_date)
    target = (date.fromisoformat(invalid_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, target, close=90)
    result = engine.run_daily(target, historical=True)
    assert result["success"], result
    with sqlite3.connect(db_path) as conn:
        previous, factors = conn.execute(
            "SELECT previous_state, factors FROM watch_daily_snapshot WHERE trade_date=?", (target,)
        ).fetchone()
    assert previous == "INVALIDATED"
    assert json.loads(factors)["watch_rebuild"]["legacy_seed"]["mode"] == "legacy_verified"


def test_legacy_seed_without_required_parent_is_explicitly_blocked(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date)
    engine.run(trigger_date)
    _legacy_quality(db_path, zone_date, trigger_date)
    target = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, target, close=90)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM watch_daily_snapshot WHERE trade_date=?", (zone_date,))
    result = engine.run_daily(target, historical=True)
    assert result["success"] is False
    assert result["blocked_reasons"] == ["legacy_seed_untrusted:parent_missing"]


def test_legacy_quality_all_failure_overrides_market_pass(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _legacy_quality(db_path, zone_date)
    _add_historical_day(db_path, trigger_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO data_quality_report VALUES (?, 'stock_daily', 'ALL', 'FAIL', ?)",
                     (zone_date, zone_date + ' 17:00:00'))
    result = engine.run_daily(trigger_date, historical=True)
    assert result["success"] is False
    assert result["blocked_reasons"] == ["legacy_seed_untrusted:quality_failed"]


def test_legacy_new_setup_after_cooldown_verifies_previous_setup_anchor(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date)
    engine.run(trigger_date)
    days = [zone_date, trigger_date]
    current = date.fromisoformat(trigger_date)
    for index in range(7):
        current += timedelta(days=1)
        while current.weekday() >= 5:
            current += timedelta(days=1)
        day = current.isoformat()
        days.append(day)
        _add_historical_day(db_path, day, close=90.5 if index == 6 else 90)
        if index == 6:
            with sqlite3.connect(db_path) as conn:
                conn.execute("UPDATE stock_daily_prices SET volume=100 WHERE trade_date=?", (day,))
        engine.run(day)
    seed_date = days[-1]
    _legacy_quality(db_path, *days)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT watch_state, setup_id FROM watch_daily_snapshot WHERE trade_date=?", (seed_date,)
        ).fetchone() == ("IN_ZONE", f"688001:{seed_date}")
    target = (current + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, target)
    result = engine.run_daily(target, historical=True)
    assert result["success"], result
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT setup_id FROM watch_daily_snapshot WHERE trade_date=?", (target,)
        ).fetchone() == (f"688001:{seed_date}",)


def test_historical_only_creates_snapshot_and_checker_is_read_only(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    engine = WatchEngine(db_path)
    before = db_path.read_bytes()
    assert engine.check_historical_snapshots(zone_date)["trusted"] is False
    assert db_path.read_bytes() == before
    result = engine.run_daily(zone_date, historical=True)
    assert result["success"] is True
    assert result["historical"] is True
    assert result["events"] == 0
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        snapshot = conn.execute("SELECT factors FROM watch_daily_snapshot").fetchone()
    assert "watch_events" not in tables
    assert "notification_deliveries" not in tables
    assert "watch_rule_configs" not in tables
    metadata = json.loads(snapshot[0])["watch_rebuild"]
    assert len(metadata["input_signature"]) == 64
    assert metadata["mode"] == "historical"
    before = db_path.read_bytes()
    assert engine.check_historical_snapshots(zone_date)["trusted"] is True
    assert db_path.read_bytes() == before


def test_historical_preserves_events_deliveries_pools_and_other_dates(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _add_historical_day(db_path, trigger_date, industry=None)
    engine.run(trigger_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE watch_events SET dedupe_key='' WHERE symbol='688001'")
        conn.execute(
            "INSERT INTO notification_deliveries "
            "(delivery_id, status, dedupe_key, created_at, updated_at) "
            "VALUES ('legacy', 'FAILED', '', 'old', 'old')"
        )
        conn.execute("CREATE TABLE portfolio_guard (value TEXT)")
        conn.execute("INSERT INTO portfolio_guard VALUES ('unchanged')")
    tables = ("watch_events", "notification_deliveries", "stock_pools",
              "stock_pool_members", "portfolio_guard")
    with sqlite3.connect(db_path) as conn:
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
        other = conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date=?", (trigger_date,)
        ).fetchall()
    assert engine.run_daily(zone_date, historical=True)["success"] is True
    with sqlite3.connect(db_path) as conn:
        after = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
        assert conn.execute(
            "SELECT * FROM watch_daily_snapshot WHERE trade_date=?", (trigger_date,)
        ).fetchall() == other
    assert after == before


def test_historical_cooldown_uses_snapshots_and_ignores_future_events(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    assert engine.run_daily(zone_date, historical=True)["success"]
    _add_historical_day(db_path, trigger_date)
    assert engine.run_daily(trigger_date, historical=True)["success"]
    later_date = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, later_date, close=90)
    with sqlite3.connect(db_path) as conn:
        engine.ensure_schema(conn)
        engine._event(
            conn, dedupe_key="future", trade_date="2099-01-01", event_type="TRIGGERED",
            severity="HIGH", title="future", message="future", symbol="688001",
            setup_id=f"688001:{zone_date}",
        )
    assert engine.run_daily(later_date, historical=True)["success"]
    with sqlite3.connect(db_path) as conn:
        state, factors = conn.execute(
            "SELECT watch_state, factors FROM watch_daily_snapshot WHERE trade_date=?",
            (later_date,),
        ).fetchone()
    assert state == "TRIGGERED"
    assert json.loads(factors)["watch_rebuild"]["trigger_date"] == trigger_date
    assert engine.check_historical_snapshots(later_date)["trusted"]


@pytest.mark.parametrize("change", ["prices", "lifecycle", "rules", "previous", "output"])
def test_historical_signatures_detect_changed_inputs_and_output(watch_db, change):
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import load_active_rules

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    with sqlite3.connect(db_path) as conn:
        load_active_rules(conn)
    engine.run_daily(zone_date, historical=True)
    _add_historical_day(db_path, trigger_date)
    engine.run_daily(trigger_date, historical=True)
    assert engine.check_historical_snapshots(trigger_date)["trusted"]
    with sqlite3.connect(db_path) as conn:
        if change == "prices":
            conn.execute("UPDATE stock_daily_prices SET volume=volume+1 WHERE trade_date=?",
                         (_trade_dates()[0],))
        elif change == "lifecycle":
            conn.execute("UPDATE stock_lifecycle_state SET structure_score=79 WHERE trade_date=?",
                         (trigger_date,))
        elif change == "rules":
            conn.execute("UPDATE watch_rule_configs SET config_json=? WHERE version=1",
                         (json.dumps({"rps50_min": 95}),))
        elif change == "previous":
            conn.execute("UPDATE watch_daily_snapshot SET trigger_price=1 WHERE trade_date=?",
                         (zone_date,))
        else:
            conn.execute("UPDATE watch_daily_snapshot SET current_price=1 WHERE trade_date=?",
                         (trigger_date,))
    assert engine.check_historical_snapshots(trigger_date)["trusted"] is False


def test_historical_rules_are_pinned_and_legacy_seed_requires_same_version(watch_db):
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import save_rule_config

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    _legacy_quality(db_path, zone_date)
    _add_historical_day(db_path, trigger_date)
    assert engine.check_historical_snapshots(zone_date)["trusted"] is False
    assert engine.run_daily(trigger_date, historical=True)["rule_version"] == 1
    with sqlite3.connect(db_path) as conn:
        previous_state = conn.execute(
            "SELECT previous_state FROM watch_daily_snapshot WHERE trade_date=?", (trigger_date,)
        ).fetchone()[0]
        save_rule_config(conn, {"rps50_min": 95}, activate=True)
    assert previous_state == "IN_ZONE"
    assert engine.run_daily(trigger_date, historical=True)["rule_version"] == 1
    blocked = WatchEngine(db_path, rule_version=2).run_daily(trigger_date, historical=True)
    assert blocked["rule_version"] == 2
    assert blocked["success"] is False


@pytest.mark.parametrize("env_name", ["LOCAL_DB_PATH", "EVOALPHA_DB_PATH"])
def test_historical_cli_uses_env_and_returns_failure_for_unknown(watch_db, env_name):
    db_path, zone_date, trigger_date = watch_db
    env = dict(os.environ, **{env_name: str(db_path)})
    env.pop("EVOALPHA_DB_PATH" if env_name == "LOCAL_DB_PATH" else "LOCAL_DB_PATH", None)
    for trade_date, expected in ((zone_date, 0), (trigger_date, 1)):
        completed = subprocess.run(
            [sys.executable, "-m", "quant_engine.watch.engine", "--mode", "daily",
             "--trade-date", trade_date, "--historical"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert completed.returncode == expected, completed.stderr
        result = json.loads(completed.stdout)
        assert result["trade_date"] == trade_date
        assert result["success"] is (expected == 0)


@pytest.mark.parametrize("trade_date", [None, "bad-date", "2026-01-01suffix"])
def test_historical_requires_explicit_exact_date_without_writes(watch_db, trade_date):
    from quant_engine.watch.engine import WatchEngine

    db_path, _, _ = watch_db
    before = db_path.read_bytes()
    assert WatchEngine(db_path).run_daily(trade_date, historical=True)["success"] is False
    assert db_path.read_bytes() == before


def test_historical_stale_predecessor_blocks_until_ascending_rebuild(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run_daily(zone_date, historical=True)
    _add_historical_day(db_path, trigger_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE stock_lifecycle_state SET structure_score=79 WHERE trade_date=?",
                     (zone_date,))
    blocked = engine.run_daily(trigger_date, historical=True)
    assert blocked["success"] is False
    assert "previous_snapshot_untrusted" in blocked["blocked_reasons"]
    assert engine.run_daily(zone_date, historical=True)["success"]
    assert engine.run_daily(trigger_date, historical=True)["success"]
    assert engine.check_historical_snapshots(trigger_date)["trusted"]


def test_historical_snapshot_trigger_cannot_mutate_other_tables(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    engine = WatchEngine(db_path)
    engine.run_daily(zone_date, historical=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            "CREATE TABLE protected_state (value INTEGER);"
            "INSERT INTO protected_state VALUES (1);"
            "CREATE TRIGGER forbidden AFTER INSERT ON watch_daily_snapshot BEGIN "
            "UPDATE protected_state SET value=2; END;"
        )
        before = conn.execute("SELECT * FROM watch_daily_snapshot").fetchall()
    assert engine.run_daily(zone_date, historical=True)["success"] is False
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT value FROM protected_state").fetchone() == (1,)
        assert conn.execute("SELECT * FROM watch_daily_snapshot").fetchall() == before


def test_historical_snapshot_trigger_cannot_write_another_date(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run_daily(zone_date, historical=True)
    _add_historical_day(db_path, trigger_date)
    engine.run_daily(trigger_date, historical=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            "CREATE TRIGGER forbidden_other_date AFTER INSERT ON watch_daily_snapshot BEGIN "
            f"DELETE FROM watch_daily_snapshot WHERE trade_date='{trigger_date}'; END;"
        )
        before = conn.execute("SELECT * FROM watch_daily_snapshot ORDER BY trade_date").fetchall()
    assert engine.run_daily(zone_date, historical=True)["success"] is False
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT * FROM watch_daily_snapshot ORDER BY trade_date"
        ).fetchall() == before


def test_historical_cooldown_expires_without_formal_trigger_event(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run_daily(zone_date, historical=True)
    _add_historical_day(db_path, trigger_date)
    engine.run_daily(trigger_date, historical=True)
    current = date.fromisoformat(trigger_date)
    states = []
    while len(states) < 7:
        current += timedelta(days=1)
        if current.weekday() >= 5:
            continue
        trade_date = current.isoformat()
        _add_historical_day(db_path, trade_date, close=90)
        assert engine.run_daily(trade_date, historical=True)["success"]
        with sqlite3.connect(db_path) as conn:
            states.append(conn.execute(
                "SELECT watch_state FROM watch_daily_snapshot WHERE trade_date=?", (trade_date,)
            ).fetchone()[0])
    assert states[:6] == ["TRIGGERED"] * 6
    assert states[-1] == "WATCHING"


def test_live_day_after_historical_rebuild_keeps_snapshot_cooldown(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run_daily(zone_date, historical=True)
    _add_historical_day(db_path, trigger_date)
    engine.run_daily(trigger_date, historical=True)
    next_date = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    _add_historical_day(db_path, next_date, close=90)
    assert engine.run_daily(next_date)["success"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT watch_state FROM watch_daily_snapshot WHERE trade_date=?", (next_date,)
        ).fetchone() == ("TRIGGERED",)
        assert conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='TRIGGERED'"
        ).fetchone() == (0,)


def test_historical_check_ignores_future_inputs_and_unmonitored_stocks(watch_db, monkeypatch):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run_daily(zone_date, historical=True)
    _add_historical_day(db_path, trigger_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO stock_pools VALUES ('disabled', 'disabled', 0, 0)")
        conn.execute("INSERT INTO stock_pool_members VALUES ('disabled', '600519', '2026-01-01')")
    seen = []
    original = engine._prices

    def prices(conn, symbol, trade_date):
        seen.append(symbol)
        result = original(conn, symbol, trade_date)
        assert len(result) <= 121
        return result

    monkeypatch.setattr(engine, "_prices", prices)
    assert engine.check_historical_snapshots(zone_date)["trusted"]
    assert seen == ["688001"]


def test_historical_run_and_check_close_connections(watch_db, monkeypatch):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    connections = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    engine = WatchEngine(db_path)
    assert engine.run_daily(zone_date, historical=True)["success"]
    assert engine.check_historical_snapshots(zone_date)["trusted"]
    assert len(connections) == 2
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_monitored_manual_stock_enters_zone_and_schema_is_created(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    result = WatchEngine(db_path).run(zone_date)

    assert result["success"] is True
    assert result["records"] == 1
    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state, data_status, pool_id, setup_id FROM watch_daily_snapshot"
        ).fetchone()
        event = conn.execute(
            "SELECT event_type, payload FROM watch_events WHERE symbol='688001'"
        ).fetchone()
        delivery_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(notification_deliveries)")
        }
    assert snapshot[:3] == ("IN_ZONE", "OK", "manual")
    assert snapshot[3]
    assert event[0] == "IN_ZONE"
    assert json.loads(event[1])["setup_path"] == "TREND_PULLBACK"
    assert {"delivery_id", "dedupe_key", "status", "attempt_count"} <= delivery_columns


def test_schema_safely_migrates_legacy_watch_tables(tmp_path):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE watch_daily_snapshot (trade_date TEXT, symbol TEXT, state TEXT);
            INSERT INTO watch_daily_snapshot VALUES ('2026-01-01', '688001', 'WATCHING');
            CREATE TABLE watch_events (event_id TEXT PRIMARY KEY, event_type TEXT);
            INSERT INTO watch_events VALUES ('old-event', 'IN_ZONE');
            CREATE TABLE notification_deliveries (delivery_id TEXT PRIMARY KEY);
            INSERT INTO notification_deliveries VALUES ('old-delivery');
            """
        )
    with sqlite3.connect(db_path) as conn:
        WatchEngine(db_path).ensure_schema(conn)
        migrated_state = conn.execute(
            "SELECT watch_state FROM watch_daily_snapshot"
        ).fetchone()[0]
        event_dedupe = conn.execute("SELECT dedupe_key FROM watch_events").fetchone()[0]
        delivery_dedupe = conn.execute(
            "SELECT dedupe_key FROM notification_deliveries"
        ).fetchone()[0]
    assert migrated_state == "WATCHING"
    assert event_dedupe == "legacy:old-event"
    assert delivery_dedupe == "legacy:old-delivery"


def test_schema_adds_source_indexes_for_symbol_first_watch_queries(tmp_path):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "watch_indexes.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_lifecycle_state (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                PRIMARY KEY (trade_date, symbol)
            );
            CREATE TABLE industry_strength_daily (
                trade_date TEXT NOT NULL,
                sector_name TEXT NOT NULL,
                PRIMARY KEY (trade_date, sector_name)
            );
            """
        )
        WatchEngine(db_path).ensure_schema(conn)
        lifecycle_columns = [
            row[2]
            for row in conn.execute("PRAGMA index_info(idx_lifecycle_symbol_date)")
        ]
        industry_columns = [
            row[2]
            for row in conn.execute("PRAGMA index_info(idx_industry_sector_date)")
        ]

    assert lifecycle_columns == ["symbol", "trade_date"]
    assert industry_columns == ["sector_name", "trade_date"]


def test_watch_source_queries_use_trade_date_indexes_without_temp_sort(tmp_path):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "watch_query_plan.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL,
                close REAL, volume REAL
            );
            CREATE INDEX idx_prices_symbol_date
                ON stock_daily_prices (symbol, trade_date);
            CREATE INDEX idx_prices_date ON stock_daily_prices (trade_date);
            CREATE TABLE stock_lifecycle_state (
                symbol TEXT, trade_date TEXT, sector_name TEXT, market_state TEXT,
                industry_state TEXT, rps_20 REAL, rps_50 REAL, rps_120 REAL,
                structure_score REAL, data_status TEXT, universe_source TEXT
            );
            CREATE INDEX idx_lifecycle_symbol_date
                ON stock_lifecycle_state (symbol, trade_date);
            CREATE TABLE market_strength_daily (
                index_code TEXT, trade_date TEXT, market_strength_score REAL
            );
            CREATE INDEX idx_market_code_date
                ON market_strength_daily (index_code, trade_date);
            CREATE TABLE industry_strength_daily (
                sector_name TEXT, trade_date TEXT, industry_strength_score REAL,
                sector_rps_20 REAL
            );
            CREATE INDEX idx_industry_sector_date
                ON industry_strength_daily (sector_name, trade_date);
            CREATE TABLE quant_feature_rps (
                symbol TEXT, trade_date TEXT, rps_20 REAL
            );
            CREATE INDEX idx_rps_symbol_date
                ON quant_feature_rps (symbol, trade_date);
            INSERT INTO stock_daily_prices VALUES
                ('688001', '2026-08-22 15:00:00', 10, 11, 9, 10.5, 1000),
                ('688001', '2026-08-25 15:00:00', 11, 12, 10, 11.5, 900);
            INSERT INTO stock_lifecycle_state VALUES
                ('688001', '2026-08-25 15:30:00', '半导体', 'M3', 'I3',
                 90, 88, 85, 80, 'OK', 'manual');
            INSERT INTO market_strength_daily VALUES
                ('000001', '2026-08-22 15:00:00', 60),
                ('000001', '2026-08-25 15:00:00', 65);
            INSERT INTO industry_strength_daily VALUES
                ('半导体', '2026-08-22 15:00:00', 70, 80),
                ('半导体', '2026-08-25 15:00:00', 75, 85);
            INSERT INTO quant_feature_rps VALUES
                ('688001', '2026-08-22 15:00:00', 86),
                ('688001', '2026-08-25 15:00:00', 90);
            """
        )

    engine = WatchEngine(db_path)
    conn = sqlite3.connect(db_path, factory=_RecordingConnection)
    try:
        conn.recorded_queries = []
        assert engine._output_date(conn, None) == "2026-08-25"
        lifecycle = engine._lifecycle(conn, "688001", "2026-08-25")
        prices = engine._prices(conn, "688001", "2026-08-25")

        assert lifecycle is not None
        assert lifecycle["market_strength"] == 65
        assert lifecycle["industry_strength"] == 75
        assert lifecycle["rps_20_context"] == 90
        assert prices[-1]["trade_date"] == "2026-08-25"

        source_tables = (
            "stock_daily_prices",
            "stock_lifecycle_state",
            "market_strength_daily",
            "industry_strength_daily",
            "quant_feature_rps",
        )
        source_queries = [
            (sql, parameters)
            for sql, parameters in conn.recorded_queries
            if sql.lstrip().upper().startswith("SELECT")
            and any(f"FROM {table}" in sql for table in source_tables)
        ]
        assert len(source_queries) == 6
        for sql, parameters in source_queries:
            normalized = " ".join(sql.lower().split())
            assert "where substr(trade_date" not in normalized
            assert "and substr(trade_date" not in normalized
            assert "order by substr(trade_date" not in normalized
            plan = conn.execute("EXPLAIN QUERY PLAN " + sql, parameters).fetchall()
            details = " ".join(str(row[3]).upper() for row in plan)
            assert "SEARCH " in details, (normalized, details)
            assert "USE TEMP B-TREE" not in details, (normalized, details)
    finally:
        conn.close()


def test_trade_day_age_uses_index_range_and_preserves_day_boundaries(tmp_path):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "watch_trade_day_age.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT);
            CREATE INDEX idx_prices_date ON stock_daily_prices (trade_date);
            CREATE TABLE watch_events (
                symbol TEXT, setup_id TEXT, event_type TEXT, trade_date TEXT
            );
            INSERT INTO watch_events VALUES
                ('688001', '688001:2026-08-21', 'TRIGGERED', '2026-08-21');
            INSERT INTO stock_daily_prices VALUES
                ('688001', '2026-08-21'),
                ('688002', '2026-08-21 15:00:00'),
                ('688001', '2026-08-22'),
                ('688002', '2026-08-22 15:00:00'),
                ('688001', '2026-08-25'),
                ('688002', '2026-08-25 15:00:00'),
                ('688001', '2026-08-26');
            """
        )

    engine = WatchEngine(db_path)
    conn = sqlite3.connect(db_path, factory=_RecordingConnection)
    try:
        conn.recorded_queries = []
        setup_days = engine._trade_days_since_setup(
            conn, "688001:2026-08-21", "2026-08-25"
        )
        trigger_days = engine._trade_days_since_trigger(
            conn, "688001", "688001:2026-08-21", "2026-08-25"
        )

        # setup 当日排除、当前日包含；同日多股/时间戳只计一个交易日。
        assert setup_days == 2
        assert trigger_days == 2

        count_queries = [
            (sql, parameters)
            for sql, parameters in conn.recorded_queries
            if "COUNT(DISTINCT substr(trade_date, 1, 10))" in sql
        ]
        assert len(count_queries) == 2
        for sql, parameters in count_queries:
            normalized = " ".join(sql.lower().split())
            assert "where trade_date>=? and trade_date<?" in normalized
            plan = conn.execute("EXPLAIN QUERY PLAN " + sql, parameters).fetchall()
            details = " ".join(str(row[3]).upper() for row in plan)
            assert "SEARCH STOCK_DAILY_PRICES USING COVERING INDEX IDX_PRICES_DATE" in details
    finally:
        conn.close()


def test_zone_trigger_is_idempotent_and_respects_setup_cooldown(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 90, 93, 89.5, 92, 800)",
            (trigger_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (trigger_date,),
        )
        conn.commit()

    first = engine.run(trigger_date)
    second = engine.run(trigger_date)
    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state FROM watch_daily_snapshot WHERE trade_date=?",
            (trigger_date,),
        ).fetchone()
        triggered = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='TRIGGERED'"
        ).fetchone()[0]
    assert first["records"] == second["records"] == 1
    assert snapshot == ("TRIGGERED",)
    assert triggered == 1


def test_missing_industry_becomes_unknown_and_global_block_is_deduplicated(tmp_path):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "blocked.db"
    zone_date, _ = _make_db(db_path, industry_state=None)
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    engine.run(zone_date)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state, data_status, data_reason FROM watch_daily_snapshot"
        ).fetchone()
        blocked_count = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='DATA_BLOCKED'"
        ).fetchone()[0]
        buy_events = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type IN ('IN_ZONE', 'TRIGGERED')"
        ).fetchone()[0]
    assert snapshot[0:2] == ("UNKNOWN", "BLOCKED")
    assert "industry_state" in snapshot[2]
    assert blocked_count == 1
    assert buy_events == 0


def test_member_scoped_run_ignores_later_uninitialized_member_but_full_run_blocks(
    tmp_path,
):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "member_scope.db"
    zone_date, _ = _make_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_pool_members VALUES "
            "('manual', '688002', '2026-08-06 13:57:05')"
        )
        conn.execute(
            "INSERT INTO stock_daily_prices "
            "SELECT '688002', trade_date, open, high, low, close, volume "
            "FROM stock_daily_prices WHERE symbol='688001'"
        )
        conn.commit()

    engine = WatchEngine(db_path)
    partial = engine.run(zone_date, symbols=["688001"])
    with sqlite3.connect(db_path) as conn:
        partial_symbols = conn.execute(
            "SELECT symbol FROM watch_daily_snapshot ORDER BY symbol"
        ).fetchall()
        partial_blocks = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='DATA_BLOCKED'"
        ).fetchone()[0]

    assert partial["success"] is True
    assert partial["records"] == 1
    assert partial["blocked_reasons"] == []
    assert partial_symbols == [("688001",)]
    assert partial_blocks == 0

    full = engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        missing_snapshot = conn.execute(
            "SELECT watch_state, data_status, data_reason "
            "FROM watch_daily_snapshot WHERE symbol='688002'"
        ).fetchone()
        full_blocks = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='DATA_BLOCKED'"
        ).fetchone()[0]

    assert full["success"] is True
    assert full["records"] == 2
    assert "lifecycle_missing" in full["blocked_reasons"]
    assert missing_snapshot == ("UNKNOWN", "BLOCKED", "lifecycle_missing")
    assert full_blocks == 1


def test_data_recovery_resumes_previous_in_zone_setup_instead_of_resetting(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, blocked_date = watch_db
    recovered_date = (date.fromisoformat(blocked_date) + timedelta(days=1)).isoformat()
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        setup_id = conn.execute(
            "SELECT setup_id FROM watch_daily_snapshot WHERE trade_date=?",
            (zone_date,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 90, 91, 88.8, 89, 650)",
            (blocked_date,),
        )
        conn.commit()

    engine.run(blocked_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 89, 93, 89, 92, 700)",
            (recovered_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (recovered_date,),
        )
        conn.commit()

    engine.run(recovered_date)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT trade_date, watch_state, setup_id FROM watch_daily_snapshot "
            "WHERE trade_date IN (?, ?, ?) ORDER BY trade_date",
            (zone_date, blocked_date, recovered_date),
        ).fetchall()
        triggered = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='TRIGGERED'"
        ).fetchone()[0]

    assert rows == [
        (zone_date, "IN_ZONE", setup_id),
        (blocked_date, "UNKNOWN", setup_id),
        (recovered_date, "TRIGGERED", setup_id),
    ]
    assert triggered == 1


def test_zone_is_invalidated_when_close_breaks_saved_stop(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, next_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        saved_stop = conn.execute(
            "SELECT invalidation_price FROM watch_daily_snapshot WHERE trade_date=?",
            (zone_date,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 88, 89, 86, 87, 700)",
            (next_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (next_date,),
        )
        conn.commit()
    engine.run(next_date)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state, invalidation_price FROM watch_daily_snapshot "
            "WHERE trade_date=?",
            (next_date,),
        ).fetchone()
        event_payload = json.loads(
            conn.execute(
                "SELECT payload FROM watch_events WHERE event_type='INVALIDATED'"
            ).fetchone()[0]
        )
    assert snapshot[0] == "INVALIDATED"
    assert snapshot[1] == saved_stop
    assert event_payload["invalidation_price"] == saved_stop
    assert event_payload["name"] == "测试股份"
    assert event_payload["pool_name"] == "重点池"


def test_in_zone_setup_can_confirm_through_temporary_industry_deterioration(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, next_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 90, 93, 89.5, 92, 800)",
            (next_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I2', 90, 80, 'OK', 'manual')",
            (next_date,),
        )
        conn.commit()

    engine.run(next_date)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state, reason FROM watch_daily_snapshot WHERE trade_date=?",
            (next_date,),
        ).fetchone()
        invalidated = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='INVALIDATED'"
        ).fetchone()[0]
    assert snapshot[0] == "TRIGGERED"
    assert "市场/行业" in snapshot[1]
    assert invalidated == 0


def test_in_zone_setup_waits_when_rps_or_structure_score_deteriorates(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, next_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 90, 93, 89.5, 92, 800)",
            (next_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I2', 84, 55, 'OK', 'manual')",
            (next_date,),
        )
        conn.commit()

    engine.run(next_date)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state, reason FROM watch_daily_snapshot WHERE trade_date=?",
            (next_date,),
        ).fetchone()
    assert snapshot[0] == "IN_ZONE"
    assert "RPS/结构" in snapshot[1]


def test_confirmation_more_than_three_percent_above_trigger_is_not_emitted(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, next_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 91, 95, 90, 94, 700)",
            (next_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (next_date,),
        )
        conn.commit()
    engine.run(next_date)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT watch_state, reason FROM watch_daily_snapshot WHERE trade_date=?", (next_date,)
        ).fetchone()
        triggered = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='TRIGGERED'"
        ).fetchone()[0]
    assert row[0] == "WATCHING"
    assert "追高" in row[1]
    assert triggered == 0


def test_new_setup_recalculates_trigger_instead_of_reusing_watching_snapshot(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    previous_date = _trade_dates()[-2]
    engine = WatchEngine(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 60, 'OK', 'manual')",
            (previous_date,),
        )
        conn.commit()
    engine.run(previous_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE watch_daily_snapshot SET trigger_price=10 WHERE trade_date=?",
            (previous_date,),
        )
        conn.commit()

    engine.run(zone_date)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state, trigger_price FROM watch_daily_snapshot WHERE trade_date=?",
            (zone_date,),
        ).fetchone()
    assert snapshot[0] == "IN_ZONE"
    assert snapshot[1] > 80


def test_confirmation_uses_current_matched_threshold_for_chase_filter(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, next_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 91, 94, 90, 93, 700)",
            (next_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (next_date,),
        )
        conn.commit()

    engine.run(next_date)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state, trigger_price FROM watch_daily_snapshot WHERE trade_date=?",
            (next_date,),
        ).fetchone()
    assert snapshot[0] == "TRIGGERED"
    assert snapshot[1] == pytest.approx(91)


def test_in_zone_setup_keeps_five_day_confirmation_window(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    later_dates: list[str] = []
    current = date.fromisoformat(zone_date) + timedelta(days=1)
    while len(later_dates) < 3:
        if current.weekday() < 5:
            later_dates.append(current.isoformat())
        current += timedelta(days=1)
    price_rows = (
        (later_dates[0], 90, 90.5, 88.5, 89, 1500),
        (later_dates[1], 89, 89.5, 88, 88.5, 1500),
        (later_dates[2], 88.5, 91, 88.2, 90.5, 700),
    )
    for row in price_rows:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO stock_daily_prices VALUES ('688001', ?, ?, ?, ?, ?, ?)",
                row,
            )
            conn.execute(
                "INSERT INTO stock_lifecycle_state VALUES "
                "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
                (row[0],),
            )
            conn.commit()
        engine.run(row[0])

    with sqlite3.connect(db_path) as conn:
        states = conn.execute(
            "SELECT trade_date, watch_state FROM watch_daily_snapshot "
            "WHERE trade_date IN (?, ?, ?) ORDER BY trade_date",
            later_dates,
        ).fetchall()
    assert states == [
        (later_dates[0], "IN_ZONE"),
        (later_dates[1], "IN_ZONE"),
        (later_dates[2], "TRIGGERED"),
    ]


def _structure_macd_prices(*, with_two_bottoms: bool) -> list[dict[str, float | str]]:
    prices: list[dict[str, float | str]] = []
    for index in range(54):
        prices.append(
            {
                "trade_date": f"2026-01-{index + 1:02d}",
                "open": 90.0,
                "high": 91.0,
                "low": 88.5,
                "close": 90.0,
                "volume": 1000.0,
            }
        )
    lows = [89.0, 87.0, 89.0, 89.0, 87.5, 88.5]
    if not with_two_bottoms:
        lows = [89.0, 88.8, 88.6, 88.4, 84.0, 85.0]
    closes = [90.0, 89.0, 90.0, 89.0, 88.0, 89.0]
    opens = [90.0, 90.0, 89.0, 90.0, 89.0, 88.5]
    for index, (open_price, low, close) in enumerate(zip(opens, lows, closes)):
        prices.append(
            {
                "trade_date": f"2026-03-{index + 1:02d}",
                "open": open_price,
                "high": 91.0,
                "low": low,
                "close": close,
                "volume": 700.0,
            }
        )
    return prices


def test_structure_and_macd_resonance_can_confirm_before_breakout():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping(
        {"confirmation_methods": ["structure_macd"], "macd_improve_days": 1}
    )
    factors = WatchEngine()._factors(
        _structure_macd_prices(with_two_bottoms=True),
        {
            "market_state": "M3",
            "industry_state": "I3",
            "rps_50": 90,
            "structure_score": 80,
        },
        rules,
    )
    confirmed, chase, trigger, methods = WatchEngine._confirmed(factors, rules)

    assert factors["higher_swing_low"] is True
    assert factors["macd_hist_improving"] is True
    assert factors["trigger_price"] is None
    assert factors["intraday_trigger_price"] > factors["close"]
    assert confirmed is True
    assert chase is False
    assert trigger == factors["close"]
    assert methods == ("structure_macd",)


def test_macd_improvement_without_price_structure_does_not_confirm():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping({"confirmation_methods": ["structure_macd"]})
    factors = WatchEngine()._factors(
        _structure_macd_prices(with_two_bottoms=False),
        {
            "market_state": "M3",
            "industry_state": "I3",
            "rps_50": 90,
            "structure_score": 80,
        },
        rules,
    )
    confirmed, _, _, _ = WatchEngine._confirmed(factors, rules)

    assert factors["macd_hist_improving"] is True
    assert factors["double_bottom"] is False
    assert factors["higher_swing_low"] is False
    assert confirmed is False


def test_repair_path_accepts_leading_stock_before_market_and_industry_upgrade():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping(
        {
            "repair_path_enabled": True,
            "repair_setup_score_min": 50,
            "repair_pullback_max_pct": 30,
            "repair_ma50_below_max_pct": 15,
            "repair_volume_ratio_max_pct": 95,
        }
    )
    factors = {
        "market_state": "M1",
        "industry_state": "I2",
        "market_strength_delta": 13,
        "industry_strength_delta": 5,
        "sector_rps_20": 79,
        "rps_20": 92,
        "rps_50": 74,
        "rps_120": 89,
        "structure_score": 82,
        "close": 100,
        "ma_20": 96,
        "ma_50": 104,
        "volume": 860,
        "volume_ma20": 1000,
        "volume_shrinking": False,
        "pullback_pct": 0.086,
        "stop_loss_pct": 0.08,
        "double_bottom": False,
        "higher_swing_low": False,
    }

    score = WatchEngine._repair_setup_score(factors, rules)

    assert score["qualified"] is True
    assert score["total"] >= rules.repair_setup_score_min
    assert score["confidence"] in {"MEDIUM", "HIGH"}


def test_repair_path_allows_controlled_bullish_volume_expansion():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping(
        {
            "repair_path_enabled": True,
            "repair_volume_ratio_max_pct": 95,
            "repair_reversal_volume_ratio_max_pct": 120,
        }
    )
    factors = {
        "market_state": "M3",
        "industry_state": "I2",
        "market_strength_delta": 8,
        "industry_strength_delta": 0,
        "sector_rps_20": 60,
        "rps_20": 64,
        "rps_50": 92,
        "rps_120": 88,
        "structure_score": 62.5,
        "close": 27.03,
        "prev_close": 24.57,
        "ma_20": 27.68,
        "ma_50": 25.8,
        "volume": 977,
        "volume_ma20": 1000,
        "volume_shrinking": False,
        "pullback_pct": 0.15,
        "stop_loss_pct": 0.08,
        "double_bottom": False,
        "higher_swing_low": False,
        "bullish_reversal": True,
    }

    score = WatchEngine._repair_setup_score(factors, rules)

    assert score["qualified"] is True
    assert score["bullish_volume_exception"] is True
    assert "volume" not in score["blocked_by"]


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({}, True),
        ({"pct_chg": -2.01}, False),
        ({"volume": 109}, False),
        ({"close": 96.5}, False),
        ({"macd_hist_crossed_zero": False, "macd_hist_improving": False}, False),
        ({"short_higher_low": False}, False),
    ],
)
def test_constructive_consolidation_has_tight_risk_guards(updates, expected):
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping({"constructive_max_decline_pct": 2})
    factors = {
        "pct_chg": -1.49,
        "close": 98.51,
        "ma_10": 96,
        "ma_20": 97,
        "volume": 81,
        "previous_volume": 108,
        "short_higher_low": True,
        "macd_hist_crossed_zero": True,
        "macd_hist_improving": False,
    }
    factors.update(updates)

    assert WatchEngine._is_constructive_consolidation(factors, rules) is expected


def test_path_b_constructive_consolidation_confirms_without_green_candle():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping(
        {
            "confirmation_methods": [
                "reversal_confluence",
                "constructive_consolidation",
            ]
        }
    )
    factors = {
        "close": 98.51,
        "open": 99,
        "prev_close": 100,
        "prev_high": 101,
        "ma_10": 96,
        "trigger_price": None,
        "bullish_reversal": False,
        "repair_confirmation_count": 3,
        "structure_macd_confirmed": False,
        "constructive_consolidation": True,
    }

    confirmed, chase, trigger, methods = WatchEngine._confirmed(
        factors, rules, setup_path="REPAIR_REVERSAL"
    )
    path_a_confirmed, _, _, _ = WatchEngine._confirmed(
        factors, rules, setup_path="TREND_PULLBACK"
    )

    assert confirmed is True
    assert chase is False
    assert trigger == pytest.approx(98.51)
    assert methods == ("constructive_consolidation",)
    assert path_a_confirmed is False


def test_path_a_can_use_ma10_support_without_changing_legacy_rule_versions():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    factors = {
        "close": 100.0,
        "ma_10": 99.0,
        "ma_20": 92.0,
        "ma_50": 90.0,
        "volume": 85.0,
        "volume_ma20": 100.0,
        "volume_shrinking": False,
        "pullback_pct": 0.10,
        "stop_loss_pct": 0.05,
    }
    legacy = LowBuyRules.from_mapping(
        {"volume_ratio_max_pct": 80}, version=21
    )
    optimized = LowBuyRules.from_mapping(
        {
            "trend_include_ma10_support": True,
            "volume_ratio_max_pct": 90,
        }
    )

    assert legacy.trend_include_ma10_support is False
    assert WatchEngine._in_zone(factors, legacy) is False
    assert WatchEngine._in_zone(factors, optimized) is True


def test_confirmation_waiting_copy_only_lists_methods_available_to_the_path():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping(
        {
            "confirmation_methods": [
                "reversal_confluence",
                "constructive_consolidation",
            ]
        }
    )

    trend_copy = WatchEngine._confirmation_waiting_for(rules, "TREND_PULLBACK")
    repair_copy = WatchEngine._confirmation_waiting_for(rules, "REPAIR_REVERSAL")

    assert trend_copy == ["反转 K 线与结构/动能至少2项共振"]
    assert repair_copy == [
        "反转 K 线与结构/动能至少2项共振",
        "缩量小阴、低点抬高且 MACD 转强",
    ]


def test_constructive_confirmation_reason_does_not_claim_a_bullish_candle():
    from quant_engine.watch.engine import WatchEngine

    constructive = WatchEngine._confirmation_reason(
        ("constructive_consolidation",)
    )
    bullish = WatchEngine._confirmation_reason(("reversal_confluence",))

    assert constructive == "缩量小阴且低点抬高、MACD 转强，完成低吸确认"
    assert "收阳并" not in constructive
    assert bullish.startswith("收阳并")


def test_reversal_confluence_accepts_positive_macd_reacceleration_without_breakout():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping(
        {
            "confirmation_methods": ["reversal_confluence"],
            "repair_confirmation_min_count": 2,
        }
    )
    factors = {
        "close": 101,
        "open": 99,
        "prev_close": 98,
        "prev_high": 103,
        "ma_10": 104,
        "trigger_price": None,
        "structure_macd_confirmed": False,
        "bullish_reversal": True,
        "low_rejection": False,
        "short_higher_low": False,
        "double_bottom": False,
        "higher_swing_low": False,
        "macd_momentum_turn_up": True,
        "relative_strength_turn_up": False,
        "repair_confirmation_count": 2,
    }

    confirmed, chase, trigger, methods = WatchEngine._confirmed(factors, rules)

    assert confirmed is True
    assert chase is False
    assert trigger == 101
    assert methods == ("reversal_confluence",)


@pytest.mark.parametrize(
    ("market_state", "ma_50", "volume", "expected_reason"),
    [
        ("M0", 100, 800, "market"),
        ("M2", 125, 800, "ma50"),
        ("M2", 100, 1300, "volume"),
    ],
)
def test_repair_path_rejects_falling_knife_context(
    market_state, ma_50, volume, expected_reason
):
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping({"repair_path_enabled": True})
    factors = {
        "market_state": market_state,
        "industry_state": "I1",
        "market_strength_delta": 0,
        "industry_strength_delta": 0,
        "sector_rps_20": 20,
        "rps_20": 62,
        "rps_50": 41,
        "rps_120": 53,
        "structure_score": 43,
        "close": 100,
        "ma_20": 98,
        "ma_50": ma_50,
        "volume": volume,
        "volume_ma20": 1000,
        "volume_shrinking": False,
        "pullback_pct": 0.20,
        "stop_loss_pct": 0.08,
        "double_bottom": False,
        "higher_swing_low": False,
    }

    score = WatchEngine._repair_setup_score(factors, rules)

    assert score["qualified"] is False
    assert expected_reason in score["blocked_by"]


def test_repair_trigger_score_floor_is_higher_than_zone_entry_floor():
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import LowBuyRules

    rules = LowBuyRules.from_mapping(
        {
            "repair_path_enabled": True,
            "repair_setup_score_min": 50,
            "repair_trigger_score_min": 60,
        }
    )

    assert WatchEngine._confirmation_quality_ok(
        "REPAIR_REVERSAL", True, {"total": 59}, rules
    ) is False
    assert WatchEngine._confirmation_quality_ok(
        "REPAIR_REVERSAL", True, {"total": 60}, rules
    ) is True
    assert WatchEngine._confirmation_quality_ok(
        "TREND_PULLBACK", True, {"total": 0}, rules
    ) is True


def test_triggered_setup_stays_in_seven_trade_day_cooldown_then_resets(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 90, 93, 89.5, 92, 800)",
            (trigger_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (trigger_date,),
        )
        conn.commit()
    engine.run(trigger_date)
    with sqlite3.connect(db_path) as conn:
        first_setup = conn.execute(
            "SELECT setup_id FROM watch_daily_snapshot WHERE trade_date=?", (trigger_date,)
        ).fetchone()[0]

    later_dates: list[str] = []
    current = date.fromisoformat(trigger_date) + timedelta(days=1)
    while len(later_dates) < 7:
        if current.weekday() < 5:
            later_dates.append(current.isoformat())
        current += timedelta(days=1)
    for trade_date in later_dates:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO stock_daily_prices VALUES ('688001', ?, 91, 91, 88.5, 90, 500)",
                (trade_date,),
            )
            conn.execute(
                "INSERT INTO stock_lifecycle_state VALUES "
                "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
                (trade_date,),
            )
            conn.commit()
        engine.run(trade_date)

    with sqlite3.connect(db_path) as conn:
        before_reset = conn.execute(
            "SELECT watch_state FROM watch_daily_snapshot WHERE trade_date=?", (later_dates[5],)
        ).fetchone()[0]
        after_reset = conn.execute(
            "SELECT watch_state, setup_id FROM watch_daily_snapshot WHERE trade_date=?",
            (later_dates[6],),
        ).fetchone()
    assert before_reset == "TRIGGERED"
    assert after_reset[0] == "WATCHING"
    assert after_reset[1] is None
    assert first_setup


def test_triggered_setup_is_not_invalidated_only_by_entry_score_deterioration(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, trigger_date = watch_db
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    day_after_trigger = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 90, 93, 89.5, 92, 800)",
            (trigger_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (trigger_date,),
        )
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 92, 94, 91, 93.5, 700)",
            (day_after_trigger,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 55, 'OK', 'manual')",
            (day_after_trigger,),
        )
        conn.commit()

    engine.run(trigger_date)
    engine.run(day_after_trigger)

    with sqlite3.connect(db_path) as conn:
        snapshots = conn.execute(
            "SELECT trade_date, watch_state, trigger_price FROM watch_daily_snapshot "
            "WHERE trade_date IN (?, ?) ORDER BY trade_date",
            (trigger_date, day_after_trigger),
        ).fetchall()
        invalidated = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='INVALIDATED'"
        ).fetchone()[0]
    assert snapshots[1][1] == "TRIGGERED"
    assert snapshots[1][2] == snapshots[0][2]
    assert invalidated == 0


def test_data_recovery_emits_one_recovery_event(tmp_path):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "recovered.db"
    zone_date, _ = _make_db(db_path, industry_state=None)
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE stock_lifecycle_state SET industry_state='I3' WHERE trade_date=?",
            (zone_date,),
        )
        conn.commit()
    engine.run(zone_date)
    engine.run(zone_date)

    with sqlite3.connect(db_path) as conn:
        recovered_count = conn.execute(
            "SELECT COUNT(*) FROM watch_events WHERE event_type='DATA_RECOVERED'"
        ).fetchone()[0]
    assert recovered_count == 1


def test_daily_recovery_does_not_resolve_intraday_data_block(tmp_path):
    from quant_engine.watch.engine import WatchEngine

    db_path = tmp_path / "scoped-recovery.db"
    zone_date, _ = _make_db(db_path, industry_state=None)
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        daily_event_id = conn.execute(
            "SELECT event_id FROM watch_events WHERE dedupe_key LIKE 'data:%'"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO watch_events (
                event_id, dedupe_key, trade_date, event_type, severity,
                title, message, status, payload, created_at, updated_at
            ) VALUES (
                'intraday-block', ?, ?, 'DATA_BLOCKED', 'CRITICAL',
                '盘中监控数据阻断', 'no_new_completed_bar', 'UNREAD',
                '{"reason":"no_new_completed_bar"}',
                '2026-07-13 10:00:00', '2026-07-13 10:00:00'
            )
            """,
            (f"intraday:data:{zone_date}:no_new_completed_bar", zone_date),
        )
        conn.execute(
            "UPDATE stock_lifecycle_state SET industry_state='I3' WHERE trade_date=?",
            (zone_date,),
        )
        conn.commit()

    engine.run(zone_date)

    with sqlite3.connect(db_path) as conn:
        daily_resolved = conn.execute(
            "SELECT resolved_at FROM watch_events WHERE event_id=?", (daily_event_id,)
        ).fetchone()[0]
        intraday_resolved = conn.execute(
            "SELECT resolved_at FROM watch_events WHERE event_id='intraday-block'"
        ).fetchone()[0]
        recovered_ids = {
            json.loads(row[0])["blocked_event_id"]
            for row in conn.execute(
                "SELECT payload FROM watch_events WHERE event_type='DATA_RECOVERED'"
            )
        }
    assert daily_resolved
    assert intraday_resolved is None
    assert recovered_ids == {daily_event_id}


def test_event_api_acknowledge_and_snooze(watch_db, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    WatchEngine(db_path).run(zone_date)
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    events = asyncio.run(watch_api.get_watch_events()).data["rows"]
    event_id = events[0]["event_id"]
    acknowledged = asyncio.run(
        watch_api.update_watch_event(
            event_id,
            watch_api.WatchEventUpdate(action="acknowledge"),
        )
    ).data["event"]
    assert acknowledged["status"] == "ACKNOWLEDGED"
    assert acknowledged["acknowledged_at"]

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE watch_events SET status='UNREAD' WHERE event_id=?", (event_id,))
        conn.commit()
    snoozed = asyncio.run(
        watch_api.update_watch_event(
            event_id,
            watch_api.WatchEventUpdate(action="snooze", snoozed_until="2026-07-14"),
        )
    ).data["event"]
    assert snoozed["status"] == "SNOOZED"
    assert snoozed["snoozed_until"] == "2026-07-14"

    frontend_compatible = watch_api.WatchEventUpdate(status="acknowledged")
    assert frontend_compatible.action == "acknowledge"


def test_watch_summary_and_snapshot_api_contract(watch_db, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    WatchEngine(db_path).run(zone_date)
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    summary = asyncio.run(watch_api.get_watch_summary(trade_date=zone_date)).data
    snapshots = asyncio.run(watch_api.get_watch_snapshots(trade_date=zone_date)).data
    assert summary["status_counts"]["IN_ZONE"] == 1
    assert summary["data_status_counts"]["OK"] == 1
    assert summary["unresolved_events"] == 1
    assert snapshots["rows"][0]["watch_state"] == "IN_ZONE"
    assert snapshots["rows"][0]["reason"]
    assert isinstance(snapshots["rows"][0]["factors"], dict)


def test_watch_api_pool_filter_uses_current_membership_for_multi_pool_stock(
    watch_db, monkeypatch
):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    WatchEngine(db_path).run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO stock_pools VALUES ('secondary', '第二分组', 0, 0)")
        conn.execute(
            "INSERT INTO stock_pool_members VALUES ('secondary', '688001', ?)",
            (zone_date,),
        )
        conn.commit()
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    summary = asyncio.run(
        watch_api.get_watch_summary(trade_date=zone_date, pool_id="secondary")
    ).data
    snapshots = asyncio.run(
        watch_api.get_watch_snapshots(trade_date=zone_date, pool_id="secondary")
    ).data
    events = asyncio.run(
        watch_api.get_watch_events(trade_date=zone_date, pool_id="secondary")
    ).data

    assert summary["status_counts"] == {"IN_ZONE": 1}
    assert snapshots["count"] == 1
    assert events["count"] == 1


def test_watch_api_caches_schema_initialization(watch_db, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    WatchEngine(db_path).run(zone_date)
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)
    watch_api._SCHEMA_READY_PATHS.clear()
    calls = 0
    original = watch_api.WatchEngine.ensure_schema

    def counted_ensure_schema(self, conn):
        nonlocal calls
        calls += 1
        return original(self, conn)

    monkeypatch.setattr(watch_api.WatchEngine, "ensure_schema", counted_ensure_schema)

    asyncio.run(watch_api.get_watch_summary(trade_date=zone_date))
    asyncio.run(watch_api.get_watch_summary(trade_date=zone_date))

    assert calls == 1


def test_watch_event_api_filters_by_trade_date_and_pool(watch_db, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    WatchEngine(db_path).run(zone_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO watch_events (
                event_id, dedupe_key, trade_date, symbol, event_type, severity,
                title, message, status, created_at, updated_at
            ) VALUES (
                'other-day', 'other-day', '2026-01-02', '688001', 'TRIGGERED',
                'HIGH', '旧事件', '旧事件', 'UNREAD', '2026-01-02', '2026-01-02'
            )
            """
        )
        conn.commit()
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    events = asyncio.run(
        watch_api.get_watch_events(trade_date=zone_date, pool_id="manual")
    ).data["rows"]

    assert {row["trade_date"] for row in events} == {zone_date}


def test_watch_engine_cli_has_reliable_exit_code(watch_db):
    db_path, zone_date, _ = watch_db
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "quant_engine.watch.engine",
            "--mode",
            "daily",
            "--trade-date",
            zone_date,
            "--db-path",
            str(db_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["success"] is True

    failed = subprocess.run(
        [
            sys.executable,
            "-m",
            "quant_engine.watch.engine",
            "--trade-date",
            "not-a-date",
            "--db-path",
            str(db_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1


def test_watch_rule_defaults_are_versioned_and_snapshot_is_auditable(watch_db):
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    result = WatchEngine(db_path).run(zone_date)

    with sqlite3.connect(db_path) as conn:
        active = conn.execute(
            "SELECT version, status, config_json FROM watch_rule_configs"
        ).fetchone()
        snapshot_version = conn.execute(
            "SELECT rule_version FROM watch_daily_snapshot WHERE trade_date=?",
            (zone_date,),
        ).fetchone()[0]

    assert result["rule_version"] == 1
    assert active[0:2] == (1, "ACTIVE")
    assert json.loads(active[2])["rps50_min"] == 85
    assert snapshot_version == 1


def test_activated_rule_version_changes_live_engine_threshold(watch_db):
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.rules import load_active_rules, save_rule_config

    db_path, zone_date, _ = watch_db
    engine = WatchEngine(db_path)
    with sqlite3.connect(db_path) as conn:
        engine.ensure_schema(conn)
        original = load_active_rules(conn)
        updated = save_rule_config(
            conn,
            {**original.to_dict(), "rps50_min": 95},
            activate=True,
        )
        conn.commit()

    result = engine.run(zone_date)
    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT watch_state, rule_version FROM watch_daily_snapshot WHERE trade_date=?",
            (zone_date,),
        ).fetchone()

    assert updated.version == 2
    assert result["rule_version"] == 2
    assert snapshot == ("WATCHING", 2)


def test_watch_rule_validation_rejects_invalid_ranges_and_methods():
    from quant_engine.watch.rules import LowBuyRules

    with pytest.raises(ValueError, match="回撤"):
        LowBuyRules.from_mapping(
            {"pullback_min_pct": 16, "pullback_max_pct": 5}
        )
    with pytest.raises(ValueError, match="确认方式"):
        LowBuyRules.from_mapping({"confirmation_methods": ["unknown"]})
    with pytest.raises(ValueError, match="共享确认方式"):
        LowBuyRules.from_mapping(
            {"confirmation_methods": ["constructive_consolidation"]}
        )
    with pytest.raises(ValueError, match="MA10 趋势支撑"):
        LowBuyRules.from_mapping({"trend_include_ma10_support": "yes"})
    with pytest.raises(ValueError, match="未知参数"):
        LowBuyRules.from_mapping({"secret_switch": True})
    assert LowBuyRules.from_mapping({"setup_valid_days": 1}).setup_valid_days == 1
    with pytest.raises(ValueError, match="确认窗口"):
        LowBuyRules.from_mapping({"setup_valid_days": 0})
    with pytest.raises(ValueError, match="双底容差"):
        LowBuyRules.from_mapping({"double_bottom_tolerance_pct": 0})
    with pytest.raises(ValueError, match="MACD 改善天数"):
        LowBuyRules.from_mapping({"macd_improve_days": 4})
    with pytest.raises(ValueError, match="修复路径触发分"):
        LowBuyRules.from_mapping(
            {"repair_setup_score_min": 60, "repair_trigger_score_min": 50}
        )


def test_watch_config_api_supports_draft_then_activation(watch_db, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine

    db_path, _, _ = watch_db
    WatchEngine(db_path).run()
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)
    watch_api._SCHEMA_READY_PATHS.clear()

    initial = asyncio.run(watch_api.get_watch_config()).data
    changed = {**initial["active"]["config"], "rps50_min": 91}
    draft = asyncio.run(
        watch_api.update_watch_config(
            watch_api.WatchRuleConfigUpdate(config=changed, activate=False)
        )
    ).data
    after_draft = asyncio.run(watch_api.get_watch_config()).data
    activated = asyncio.run(
        watch_api.update_watch_config(
            watch_api.WatchRuleConfigUpdate(config=changed, activate=True)
        )
    ).data
    current = asyncio.run(watch_api.get_watch_config()).data

    assert initial["active"]["version"] == 1
    assert draft["status"] == "DRAFT"
    assert after_draft["active"]["version"] == 1
    assert after_draft["draft"]["version"] == 2
    assert activated["status"] == "ACTIVE"
    assert current["active"]["version"] == 3
    assert current["active"]["config"]["rps50_min"] == 91
    assert current["draft"] is None


def test_watch_config_api_returns_422_for_invalid_config(watch_db, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine

    db_path, _, _ = watch_db
    with sqlite3.connect(db_path) as conn:
        WatchEngine(db_path).ensure_schema(conn)
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)
    watch_api._SCHEMA_READY_PATHS.clear()

    with pytest.raises(Exception) as error:
        asyncio.run(
            watch_api.update_watch_config(
                watch_api.WatchRuleConfigUpdate(
                    config={"pullback_min_pct": 20, "pullback_max_pct": 5},
                    activate=True,
                )
            )
        )

    assert getattr(error.value, "status_code", None) == 422


def test_activating_watch_rules_recomputes_latest_action_box(watch_db, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine

    db_path, zone_date, _ = watch_db
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)
    watch_api._SCHEMA_READY_PATHS.clear()
    with sqlite3.connect(db_path) as conn:
        WatchEngine(db_path).ensure_schema(conn)
    calls: list[str | None] = []

    def fake_run(self, trade_date=None):
        calls.append(trade_date)
        return {"success": True, "trade_date": zone_date, "records": 1}

    monkeypatch.setattr(watch_api.WatchEngine, "run", fake_run)
    current = asyncio.run(watch_api.get_watch_config()).data["active"]["config"]
    response = asyncio.run(
        watch_api.update_watch_config(
            watch_api.WatchRuleConfigUpdate(config=current, activate=True)
        )
    ).data

    assert calls == [None]
    assert response["watch_refresh"]["success"] is True
    assert response["watch_refresh"]["trade_date"] == zone_date
