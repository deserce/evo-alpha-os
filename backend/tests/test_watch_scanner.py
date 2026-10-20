import asyncio
import fcntl
import json
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi import BackgroundTasks

from tests.test_watch_engine import _make_db


def _add_trigger_day(db_path, trigger_date: str) -> None:
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


def test_historical_scan_is_chronological_and_does_not_touch_live_alerts(tmp_path):
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "scan.db"
    zone_date, trigger_date = _make_db(db_path)
    _add_trigger_day(db_path, trigger_date)
    scanner = WatchHistoricalScanner(db_path)

    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=trigger_date,
        scope="all_groups",
    )
    result = scanner.run_scan(scan["scan_id"])

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT trade_date, watch_state FROM watch_scan_results "
            "WHERE scan_id=? ORDER BY trade_date",
            (scan["scan_id"],),
        ).fetchall()
        live_snapshots = conn.execute(
            "SELECT COUNT(*) FROM watch_daily_snapshot"
        ).fetchone()[0]
        live_events = conn.execute("SELECT COUNT(*) FROM watch_events").fetchone()[0]

    assert result["status"] == "COMPLETED"
    assert rows == [(zone_date, "IN_ZONE"), (trigger_date, "TRIGGERED")]
    assert live_snapshots == 0
    assert live_events == 0


def test_historical_scan_marks_missing_lifecycle_unknown_without_buy_result(tmp_path):
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "scan_unknown.db"
    zone_date, _ = _make_db(db_path, industry_state=None)
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="monitored_groups",
    )
    scanner.run_scan(scan["scan_id"])

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT watch_state, data_status, data_reason FROM watch_scan_results"
        ).fetchone()
        live_events = conn.execute("SELECT COUNT(*) FROM watch_events").fetchone()[0]

    assert row[0:2] == ("UNKNOWN", "BLOCKED")
    assert "industry_state_missing" in row[2]
    assert live_events == 0


def test_historical_scan_rejects_more_than_160_trade_dates(tmp_path):
    from quant_engine.watch.scanner import ScanValidationError, WatchHistoricalScanner

    db_path = tmp_path / "scan_limit.db"
    zone_date, _ = _make_db(db_path)
    start = date.fromisoformat(zone_date) + timedelta(days=1)
    with sqlite3.connect(db_path) as conn:
        for offset in range(161):
            trade_date = (start + timedelta(days=offset)).isoformat()
            conn.execute(
                "INSERT INTO stock_daily_prices VALUES ('688001', ?, 90, 91, 89, 90, 500)",
                (trade_date,),
            )

    scanner = WatchHistoricalScanner(db_path)
    with pytest.raises(ScanValidationError, match="160"):
        scanner.create_scan(
            start_date=start.isoformat(),
            end_date=(start + timedelta(days=160)).isoformat(),
            scope="all_groups",
        )


def test_watch_scan_api_runs_in_background_and_returns_filtered_results(
    tmp_path, monkeypatch
):
    from app.api.v1 import watch as watch_api

    db_path = tmp_path / "scan_api.db"
    zone_date, trigger_date = _make_db(db_path)
    _add_trigger_day(db_path, trigger_date)
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)
    tasks = BackgroundTasks()

    created = asyncio.run(
        watch_api.create_watch_scan(
            watch_api.WatchScanRequest(
                start_date=zone_date,
                end_date=trigger_date,
                scope="pools",
                pool_ids=["manual"],
            ),
            tasks,
        )
    ).data
    asyncio.run(tasks())
    status = asyncio.run(watch_api.get_watch_scan(created["scan_id"])).data
    latest = asyncio.run(watch_api.get_watch_scan("latest")).data
    triggered = asyncio.run(
        watch_api.get_watch_scan_results(
            scan_id=created["scan_id"],
            state="TRIGGERED",
            pool_id="manual",
        )
    ).data

    assert created["status"] == "PENDING"
    assert status["status"] == "COMPLETED"
    assert latest["scan_id"] == created["scan_id"]
    assert status["progress_pct"] == 100
    assert triggered["count"] == 1
    assert triggered["rows"][0]["trade_date"] == trigger_date
    assert isinstance(triggered["rows"][0]["factors"], dict)
    assert triggered["rows"][0]["pool_ids"] == ["manual"]


def test_watch_scan_api_rejects_invalid_date_range(tmp_path, monkeypatch):
    from app.api.v1 import watch as watch_api

    db_path = tmp_path / "scan_api_invalid.db"
    zone_date, _ = _make_db(db_path)
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    with pytest.raises(Exception) as error:
        asyncio.run(
            watch_api.create_watch_scan(
                watch_api.WatchScanRequest(
                    start_date="2026-12-31",
                    end_date=zone_date,
                    scope="all_groups",
                ),
                BackgroundTasks(),
            )
        )

    assert getattr(error.value, "status_code", None) == 422


def test_automatic_member_scan_does_not_replace_latest_manual_scan(tmp_path):
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "scan_origin.db"
    zone_date, _ = _make_db(db_path)
    scanner = WatchHistoricalScanner(db_path)
    manual = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="all_groups",
    )
    scanner.run_scan(manual["scan_id"])

    automatic = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="all_groups",
        symbols=["688001"],
        origin="AUTO_MEMBER",
    )
    scanner.run_scan(automatic["scan_id"])

    assert scanner.get_scan("latest")["scan_id"] == manual["scan_id"]
    assert scanner.get_scan(automatic["scan_id"])["origin"] == "AUTO_MEMBER"


def test_scan_results_without_id_keep_using_latest_manual_scan(tmp_path, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "scan_api_origin.db"
    zone_date, _ = _make_db(db_path)
    scanner = WatchHistoricalScanner(db_path)
    manual = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="all_groups",
    )
    scanner.run_scan(manual["scan_id"])
    automatic = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="all_groups",
        symbols=["688001"],
        origin="AUTO_MEMBER",
    )
    scanner.run_scan(automatic["scan_id"])
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    result = asyncio.run(watch_api.get_watch_scan_results()).data

    assert result["scan"]["scan_id"] == manual["scan_id"]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE watch_scan_runs SET completed_at='2999-07-19 23:59:59' "
            "WHERE scan_id=?",
            (automatic["scan_id"],),
        )
    symbol_result = asyncio.run(
        watch_api.get_watch_scan_results(symbol="688001")
    ).data
    assert symbol_result["scan"]["scan_id"] == automatic["scan_id"]


def test_marker_lookup_uses_latest_scan_containing_requested_symbol(
    tmp_path, monkeypatch
):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "marker_scan_symbol.db"
    zone_date, _ = _make_db(db_path)
    scanner = WatchHistoricalScanner(db_path)
    first = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="all_groups",
        symbols=["688001"],
        origin="AUTO_MEMBER",
    )
    scanner.run_scan(first["scan_id"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_pool_members VALUES ('manual', '688002', ?)",
            (zone_date + " 10:00:00",),
        )
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES "
            "('688002', ?, 90, 100, 88.5, 90, 600)",
            (zone_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688002', '第二只', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (zone_date,),
        )
    second = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="all_groups",
        symbols=["688002"],
        origin="AUTO_MEMBER",
    )
    scanner.run_scan(second["scan_id"])
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    markers = asyncio.run(
        watch_api.get_watch_markers(symbol="688001")
    ).data

    assert markers["scan_id"] == first["scan_id"]


def test_automatic_member_scan_rejects_symbol_outside_selected_scope(tmp_path):
    from quant_engine.watch.scanner import ScanValidationError, WatchHistoricalScanner

    db_path = tmp_path / "scan_symbol_scope.db"
    zone_date, _ = _make_db(db_path)
    scanner = WatchHistoricalScanner(db_path)

    with pytest.raises(ScanValidationError, match="所选范围"):
        scanner.create_scan(
            start_date=zone_date,
            end_date=zone_date,
            scope="all_groups",
            symbols=["999999"],
            origin="AUTO_MEMBER",
        )


def test_historical_scan_preloads_each_symbol_instead_of_querying_every_cell(
    tmp_path, monkeypatch
):
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "scan_preload.db"
    zone_date, trigger_date = _make_db(db_path)
    _add_trigger_day(db_path, trigger_date)
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=trigger_date,
        scope="all_groups",
    )

    def forbid_cell_query(*_args, **_kwargs):
        raise AssertionError("扫描循环不应逐日查询大表")

    monkeypatch.setattr(scanner.engine, "_prices", forbid_cell_query)
    monkeypatch.setattr(scanner.engine, "_lifecycle", forbid_cell_query)
    monkeypatch.setattr(scanner.engine, "_trade_days_since_setup", forbid_cell_query)

    assert scanner.run_scan(scan["scan_id"])["status"] == "COMPLETED"


def test_historical_scan_uses_same_causal_context_as_live_engine(tmp_path):
    from quant_engine.watch.rules import load_active_rules, save_rule_config
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "scan_context.db"
    zone_date, _ = _make_db(db_path)
    with sqlite3.connect(db_path) as conn:
        previous_date = conn.execute(
            "SELECT MAX(substr(trade_date, 1, 10)) FROM stock_daily_prices "
            "WHERE substr(trade_date, 1, 10)<?",
            (zone_date,),
        ).fetchone()[0]
        conn.executescript(
            """
            ALTER TABLE stock_lifecycle_state ADD COLUMN sector_name TEXT;
            ALTER TABLE stock_lifecycle_state ADD COLUMN rps_20 REAL;
            ALTER TABLE stock_lifecycle_state ADD COLUMN rps_120 REAL;
            CREATE TABLE market_strength_daily (
                trade_date TEXT, index_code TEXT, market_strength_score REAL
            );
            CREATE TABLE industry_strength_daily (
                trade_date TEXT, sector_name TEXT, industry_strength_score REAL,
                sector_rps_20 REAL
            );
            CREATE TABLE quant_feature_rps (
                symbol TEXT, trade_date TEXT, rps_20 REAL
            );
            """
        )
        conn.execute(
            "UPDATE stock_lifecycle_state SET market_state='M1', industry_state='I2', "
            "rps_50=74, structure_score=55, sector_name='测试行业', "
            "rps_20=92, rps_120=80 WHERE trade_date=?",
            (zone_date,),
        )
        conn.executemany(
            "INSERT INTO market_strength_daily VALUES (?, '000001', ?)",
            [(previous_date, 50), (zone_date, 60)],
        )
        conn.executemany(
            "INSERT INTO industry_strength_daily VALUES (?, '测试行业', ?, ?)",
            [(previous_date, 39, 70), (zone_date, 45, 76)],
        )
        conn.executemany(
            "INSERT INTO quant_feature_rps VALUES ('688001', ?, ?)",
            [(previous_date, 88), (zone_date, 92)],
        )

    scanner = WatchHistoricalScanner(db_path)
    with sqlite3.connect(db_path) as conn:
        scanner.ensure_schema(conn)
        active = load_active_rules(conn)
        save_rule_config(
            conn,
            {**active.to_dict(), "repair_path_enabled": True},
            activate=True,
        )
        conn.commit()
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="all_groups",
    )
    scanner.run_scan(scan["scan_id"])

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT watch_state, factors FROM watch_scan_results WHERE scan_id=?",
            (scan["scan_id"],),
        ).fetchone()
    factors = json.loads(row[1])

    assert row[0] == "IN_ZONE"
    assert factors["setup_path"] == "REPAIR_REVERSAL"
    assert factors["market_strength_delta"] == 10
    assert factors["industry_strength_delta"] == 6
    assert factors["rps_20_delta"] == 4


def test_historical_scan_rejects_a_second_process_writer(tmp_path):
    from quant_engine.watch.scanner import ScanBusyError, WatchHistoricalScanner

    db_path = tmp_path / "scan_process_lock.db"
    zone_date, _ = _make_db(db_path)
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=zone_date,
        scope="all_groups",
    )
    lock_path = db_path.with_suffix(f"{db_path.suffix}.watch-scan.lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ScanBusyError, match="已有历史遍历"):
            scanner.run_scan(scan["scan_id"])


def test_watch_marker_api_returns_only_zone_and_trigger_transitions(
    tmp_path, monkeypatch
):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "watch_markers.db"
    zone_date, trigger_date = _make_db(db_path)
    _add_trigger_day(db_path, trigger_date)
    cooldown_date = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 91, 94, 90, 93, 700)",
            (cooldown_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (cooldown_date,),
        )
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=cooldown_date,
        scope="all_groups",
    )
    scanner.run_scan(scan["scan_id"])
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    payload = asyncio.run(
        watch_api.get_watch_markers(symbol="688001")
    ).data
    missing = asyncio.run(
        watch_api.get_watch_markers(symbol="000000")
    ).data

    assert payload["scan_id"] == scan["scan_id"]
    assert [(row["trade_date"], row["marker_type"]) for row in payload["rows"]] == [
        (zone_date, "IN_ZONE"),
        (trigger_date, "TRIGGERED"),
    ]
    assert payload["rows"][1]["current_price"] == pytest.approx(92)
    assert missing["rows"] == []


def test_watch_event_api_uses_scan_transition_for_historical_action_box(
    tmp_path, monkeypatch
):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "watch_action_box.db"
    zone_date, trigger_date = _make_db(db_path)
    _add_trigger_day(db_path, trigger_date)
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=trigger_date,
        scope="all_groups",
    )
    scanner.run_scan(scan["scan_id"])
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    payload = asyncio.run(
        watch_api.get_watch_events(trade_date=trigger_date, pool_id="manual")
    ).data

    assert payload["count"] == 1
    event = payload["rows"][0]
    assert "event_id" not in event
    assert event["event_type"] == "TRIGGERED"
    assert event["from_state"] == "IN_ZONE"
    assert event["to_state"] == "TRIGGERED"
    assert event["status"] == "HISTORICAL"
    assert event["payload"]["source"] == "historical_scan"
    assert event["payload"]["is_transition"] is True
    assert event["payload"]["current_price"] == pytest.approx(92)


def test_historical_action_box_keeps_triggered_cooldown_state(
    tmp_path, monkeypatch
):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "watch_action_box_cooldown.db"
    zone_date, trigger_date = _make_db(db_path)
    _add_trigger_day(db_path, trigger_date)
    cooldown_date = (date.fromisoformat(trigger_date) + timedelta(days=1)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('688001', ?, 92, 94, 91, 93, 650)",
            (cooldown_date,),
        )
        conn.execute(
            "INSERT INTO stock_lifecycle_state VALUES "
            "(?, '688001', '测试股份', 'M3', 'I3', 90, 80, 'OK', 'manual')",
            (cooldown_date,),
        )
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=cooldown_date,
        scope="all_groups",
    )
    scanner.run_scan(scan["scan_id"])
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    payload = asyncio.run(
        watch_api.get_watch_events(trade_date=cooldown_date, pool_id="manual")
    ).data

    assert payload["count"] == 1
    event = payload["rows"][0]
    assert event["event_type"] == "TRIGGERED"
    assert event["from_state"] == "TRIGGERED"
    assert event["to_state"] == "TRIGGERED"
    assert event["status"] == "HISTORICAL"
    assert event["payload"]["is_transition"] is False


def test_historical_action_box_does_not_duplicate_a_formal_event(
    tmp_path, monkeypatch
):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "watch_action_box_dedupe.db"
    zone_date, trigger_date = _make_db(db_path)
    _add_trigger_day(db_path, trigger_date)
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=trigger_date,
        scope="all_groups",
    )
    scanner.run_scan(scan["scan_id"])
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    engine.run(trigger_date)
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    rows = asyncio.run(
        watch_api.get_watch_events(trade_date=trigger_date, pool_id="manual")
    ).data["rows"]

    assert len(rows) == 1
    assert rows[0]["event_type"] == "TRIGGERED"
    assert rows[0]["event_id"]
    assert rows[0]["status"] == "UNREAD"


def test_acknowledged_formal_event_does_not_hide_historical_scan_result(
    tmp_path, monkeypatch
):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.engine import WatchEngine
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / "watch_action_box_acknowledged.db"
    zone_date, trigger_date = _make_db(db_path)
    _add_trigger_day(db_path, trigger_date)
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(
        start_date=zone_date,
        end_date=trigger_date,
        scope="all_groups",
    )
    scanner.run_scan(scan["scan_id"])
    engine = WatchEngine(db_path)
    engine.run(zone_date)
    engine.run(trigger_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE watch_events SET status='ACKNOWLEDGED' "
            "WHERE trade_date=? AND event_type='TRIGGERED'",
            (trigger_date,),
        )
        conn.commit()
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)

    rows = asyncio.run(
        watch_api.get_watch_events(trade_date=trigger_date, pool_id="manual")
    ).data["rows"]

    assert {row["status"] for row in rows} == {"ACKNOWLEDGED", "HISTORICAL"}
    historical = next(row for row in rows if row["status"] == "HISTORICAL")
    assert historical["event_type"] == "TRIGGERED"
    assert historical["payload"]["scan_id"] == scan["scan_id"]


def _marker_merge_fixture(tmp_path, monkeypatch):
    from app.api.v1 import watch as watch_api
    from quant_engine.watch.scanner import WatchHistoricalScanner

    db_path = tmp_path / 'marker_merge.db'
    zone_date, _ = _make_db(db_path)
    scanner = WatchHistoricalScanner(db_path)
    scan = scanner.create_scan(start_date=zone_date, end_date=zone_date, scope='all_groups')
    scanner.run_scan(scan['scan_id'])
    with sqlite3.connect(db_path) as conn:
        conn.execute('DELETE FROM watch_scan_results')
        for day, state, previous in [('2026-08-05', 'IN_ZONE', 'WATCHING'),
                                     ('2026-08-06', 'TRIGGERED', 'IN_ZONE')]:
            conn.execute(
                'INSERT INTO watch_scan_results '
                '(scan_id,trade_date,symbol,pool_ids,watch_state,previous_state,'
                'rule_version,data_status,created_at) VALUES (?,?,?, ?,?,?,25,?,?)',
                (scan['scan_id'], day, '688001', '[]', state, previous, 'OK', day),
            )
        for day, state, previous in [('2026-08-06', 'INVALIDATED', 'TRIGGERED'),
                                     ('2026-08-07', 'TRIGGERED', 'IN_ZONE'),
                                     ('2026-08-10', 'WATCHING', 'TRIGGERED'),
                                     ('2026-08-12', 'IN_ZONE', 'WATCHING'),
                                     ('2026-08-17', 'TRIGGERED', 'IN_ZONE'),
                                     ('2026-08-18', 'TRIGGERED', 'TRIGGERED'),
                                     ('2026-08-19', 'INVALIDATED', 'TRIGGERED')]:
            conn.execute(
                'INSERT INTO watch_daily_snapshot '
                '(trade_date,symbol,watch_state,previous_state,rule_version,data_status) '
                'VALUES (?, ?, ?, ?,26, ?)', (day, '688001', state, previous, 'OK'),
            )
    monkeypatch.setattr(watch_api, 'WATCH_DB_PATH', db_path)
    return watch_api, scan['scan_id'], db_path


def test_markers_merge_official_after_scan_and_suppress_boundary_cooldown(tmp_path, monkeypatch):
    api, scan_id, _ = _marker_merge_fixture(tmp_path, monkeypatch)
    data = asyncio.run(api.get_watch_markers(symbol='688001')).data
    assert [(r['trade_date'], r['marker_type']) for r in data['rows']] == [
        ('2026-08-05', 'IN_ZONE'), ('2026-08-06', 'TRIGGERED'),
        ('2026-08-12', 'IN_ZONE'), ('2026-08-17', 'TRIGGERED'),
        ('2026-08-19', 'INVALIDATED'),
    ]
    assert data['rows'][1]['source'] == 'historical_scan'
    assert data['rows'][1]['rule_version'] == 25
    assert data['rows'][-1]['source'] == 'daily_snapshot'
    assert data['rows'][-1]['rule_version'] == 26
    explicit = asyncio.run(api.get_watch_markers(symbol='688001', scan_id=scan_id)).data
    assert len(explicit['rows']) == 2


def test_markers_date_filter_keeps_predecessor_and_supports_official_only(tmp_path, monkeypatch):
    api, _, db_path = _marker_merge_fixture(tmp_path, monkeypatch)
    data = asyncio.run(api.get_watch_markers(
        symbol='688001', start_date='2026-08-07', end_date='2026-08-18')).data
    assert [r['trade_date'] for r in data['rows']] == ['2026-08-12', '2026-08-17']
    with sqlite3.connect(db_path) as conn:
        conn.execute('DELETE FROM watch_scan_results')
    data = asyncio.run(api.get_watch_markers(symbol='688001')).data
    assert data['scan_id'] is None
    assert any(r['trade_date'] == '2026-08-17' for r in data['rows'])


def test_markers_reject_invalid_dates_and_unknown_scan(tmp_path, monkeypatch):
    from fastapi import HTTPException
    api, _, _ = _marker_merge_fixture(tmp_path, monkeypatch)
    for kwargs, status in [({'start_date': 'wrong'}, 422),
                           ({'start_date': '2026-09-01', 'end_date': '2026-08-01'}, 422),
                           ({'start_date': '2000-01-01', 'end_date': '2026-01-01'}, 422),
                           ({'scan_id': 'missing'}, 404)]:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(api.get_watch_markers(symbol='688001', **kwargs))
        assert caught.value.status_code == status


def test_markers_explicit_incomplete_scan_rejected_and_read_is_non_mutating(tmp_path, monkeypatch):
    from fastapi import HTTPException
    api, scan_id, db_path = _marker_merge_fixture(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as conn:
        before = list(conn.iterdump())
    asyncio.run(api.get_watch_markers(symbol='688001'))
    with sqlite3.connect(db_path) as conn:
        assert list(conn.iterdump()) == before
        conn.execute("UPDATE watch_scan_runs SET status='RUNNING' WHERE scan_id=?", (scan_id,))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.get_watch_markers(symbol='688001', scan_id=scan_id))
    assert caught.value.status_code == 409


def test_markers_boundary_allows_real_new_setup_confirmation(tmp_path, monkeypatch):
    api, _, db_path = _marker_merge_fixture(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE watch_scan_results SET setup_id='old'")
        conn.execute("UPDATE watch_daily_snapshot SET setup_id='new'")
    rows = asyncio.run(api.get_watch_markers(symbol='688001')).data['rows']
    assert any(r['trade_date'] == '2026-08-07' for r in rows)
