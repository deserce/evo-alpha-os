import asyncio
import json
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


def _make_db(path, *, industry_state: str | None = "I3") -> tuple[str, str]:
    dates = _trade_dates()
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
        high = 100.0 if index == 40 else 91.0
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
