import asyncio
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.api.v1 import watch as watch_api
from quant_engine.watch.scanner import WatchHistoricalScanner


TRADE_DATE = "2026-07-17"
SYMBOL = "600584"


def _insert_scan(
    conn: sqlite3.Connection,
    *,
    scan_id: str,
    status: str,
    completed_at: str | None,
    state: str,
    previous_state: str,
) -> None:
    conn.execute(
        """
        INSERT INTO watch_scan_runs (
            scan_id, start_date, end_date, scope, pool_ids, rule_version,
            rule_config_json, trade_dates_json, universe_json, status,
            total_symbols, total_dates, total_cells, processed_cells,
            opportunity_count, created_at, started_at, completed_at
        ) VALUES (?, ?, ?, 'all_groups', '[]', 23, '{}', ?, '[]', ?, 1, 1, 1, 1, 1, ?, ?, ?)
        """,
        (
            scan_id,
            TRADE_DATE,
            TRADE_DATE,
            json.dumps([TRADE_DATE]),
            status,
            completed_at or "2026-07-19 19:05:00",
            completed_at,
            completed_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO watch_scan_results (
            scan_id, trade_date, symbol, name, pool_ids, watch_state,
            previous_state, setup_id, rule_version, current_price,
            zone_low, zone_high, trigger_price, invalidation_price,
            distance_to_trigger_pct, reason, factors, data_status,
            data_reason, created_at
        ) VALUES (?, ?, ?, '长电科技', '["watchlist"]', ?, ?, ?, 23, 85.49,
                  79.709, 108.177, 85.49, 78.651, 0, ?, ?, 'OK', NULL,
                  '2026-07-19 19:05:00')
        """,
        (
            scan_id,
            TRADE_DATE,
            SYMBOL,
            state,
            previous_state,
            f"{SYMBOL}:2026-07-16",
            "历史回放状态",
            json.dumps(
                {
                    "setup_path": "REPAIR_REVERSAL",
                    "repair_setup_score": 69,
                    "repair_confidence": "MEDIUM",
                    "confirmation_methods_matched": ["reversal_confluence"],
                }
            ),
        ),
    )


@pytest.fixture()
def watch_detail_db(tmp_path, monkeypatch):
    db_path = tmp_path / "watch-detail.db"
    with sqlite3.connect(db_path) as conn:
        WatchHistoricalScanner(db_path).ensure_schema(conn)
    monkeypatch.setattr(watch_api, "WATCH_DB_PATH", db_path)
    watch_api._SCHEMA_READY_PATHS.clear()
    yield db_path
    watch_api._SCHEMA_READY_PATHS.clear()


def test_stock_detail_keeps_matching_tracking_and_scan_snapshots(watch_detail_db):
    with sqlite3.connect(watch_detail_db) as conn:
        conn.execute(
            """
            INSERT INTO watch_daily_snapshot (
                trade_date, symbol, name, pool_id, pool_name, watch_state,
                previous_state, setup_id, rule_version, current_price,
                zone_low, zone_high, trigger_price, invalidation_price,
                reason, factors, data_status, created_at, updated_at
            ) VALUES (?, ?, '长电科技', 'watchlist', '自选股', 'IN_ZONE',
                      'WATCHING', ?, 23, 85.49, 79.709, 108.177, 85.49,
                      78.651, '正式跟踪状态', ?, 'OK',
                      '2026-07-19 19:00:00', '2026-07-19 19:00:00')
            """,
            (
                TRADE_DATE,
                SYMBOL,
                f"{SYMBOL}:2026-07-16",
                json.dumps({"setup_path": "REPAIR_REVERSAL"}),
            ),
        )
        _insert_scan(
            conn,
            scan_id="completed-23",
            status="COMPLETED",
            completed_at="2026-07-19 19:05:00",
            state="IN_ZONE",
            previous_state="WATCHING",
        )
        conn.commit()

    data = asyncio.run(
        watch_api.get_watch_stock_detail(SYMBOL, trade_date=TRADE_DATE)
    ).data

    assert data["tracking_snapshot"]["watch_state"] == "IN_ZONE"
    assert data["scan_snapshot"]["watch_state"] == "IN_ZONE"
    assert data["scan"]["scan_id"] == "completed-23"
    assert data["consistency"] == "MATCHED"
    assert data["scan_snapshot"]["factors"]["setup_path"] == "REPAIR_REVERSAL"


def test_stock_detail_preserves_divergence_and_ignores_newer_pending_scan(watch_detail_db):
    with sqlite3.connect(watch_detail_db) as conn:
        conn.execute(
            """
            INSERT INTO watch_daily_snapshot (
                trade_date, symbol, name, watch_state, previous_state,
                setup_id, rule_version, current_price, reason, factors,
                data_status, created_at, updated_at
            ) VALUES (?, ?, '长电科技', 'IN_ZONE', 'WATCHING', ?, 23,
                      85.49, '正式入区', '{}', 'OK',
                      '2026-07-19 19:00:00', '2026-07-19 19:00:00')
            """,
            (TRADE_DATE, SYMBOL, f"{SYMBOL}:{TRADE_DATE}"),
        )
        _insert_scan(
            conn,
            scan_id="completed-23",
            status="COMPLETED",
            completed_at="2026-07-19 19:05:00",
            state="TRIGGERED",
            previous_state="IN_ZONE",
        )
        _insert_scan(
            conn,
            scan_id="pending-24",
            status="RUNNING",
            completed_at=None,
            state="WATCHING",
            previous_state="WATCHING",
        )
        conn.execute(
            """
            INSERT INTO watch_events (
                event_id, dedupe_key, trade_date, symbol, setup_id,
                event_type, from_state, to_state, severity, title, message,
                status, payload, created_at, updated_at
            ) VALUES ('event-1', 'detail-event-1', ?, ?, ?, 'IN_ZONE',
                      'WATCHING', 'IN_ZONE', 'INFO', '进入回调区', '正式提醒',
                      'ACKNOWLEDGED', '{}', '2026-07-19 19:00:00',
                      '2026-07-19 19:01:00')
            """,
            (TRADE_DATE, SYMBOL, f"{SYMBOL}:{TRADE_DATE}"),
        )
        conn.commit()

    data = asyncio.run(
        watch_api.get_watch_stock_detail(SYMBOL, trade_date=TRADE_DATE)
    ).data

    assert data["tracking_snapshot"]["watch_state"] == "IN_ZONE"
    assert data["scan_snapshot"]["watch_state"] == "TRIGGERED"
    assert data["scan"]["scan_id"] == "completed-23"
    assert data["consistency"] == "DIVERGED"
    assert data["events"][0]["status"] == "ACKNOWLEDGED"


def test_stock_detail_handles_empty_data_and_rejects_invalid_date(watch_detail_db):
    empty = asyncio.run(
        watch_api.get_watch_stock_detail("000001", trade_date=TRADE_DATE)
    ).data

    assert empty["tracking_snapshot"] is None
    assert empty["scan_snapshot"] is None
    assert empty["events"] == []
    assert empty["consistency"] == "NO_DATA"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            watch_api.get_watch_stock_detail("000001", trade_date="2026-99-99")
        )
    assert exc.value.status_code == 422
