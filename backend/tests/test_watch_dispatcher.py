from contextlib import closing
import json
import sqlite3
import sys

import pytest

from app.notifications.email_notifier import EmailDeliveryResult
from quant_engine.watch.engine import WatchEngine
from quant_engine.watch.scanner import WatchHistoricalScanner


def seed_events(db_path):
    with sqlite3.connect(db_path) as conn:
        WatchEngine(db_path).ensure_schema(conn)
        now = "2026-07-13 20:01:00"
        conn.executemany(
            """
            INSERT INTO watch_events (
                event_id, dedupe_key, trade_date, symbol, setup_id, event_type,
                from_state, to_state, severity, title, message, status, payload,
                created_at, updated_at
            ) VALUES (?, ?, '2026-07-13', '600001', 'setup-1', ?,
                      'WATCHING', ?, ?, ?, ?, 'UNREAD', '{}', ?, ?)
            """,
            [
                (
                    "zone-event", "zone", "IN_ZONE", "IN_ZONE", "INFO",
                    "进入回调区", "缩量回调", now, now,
                ),
                (
                    "trigger-event", "trigger", "TRIGGERED", "TRIGGERED", "HIGH",
                    "低吸确认触发", "重新站上 MA10", now, now,
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO watch_daily_snapshot (
                trade_date, symbol, name, pool_id, pool_name, watch_state,
                previous_state, current_price, zone_low, zone_high,
                trigger_price, invalidation_price, distance_to_trigger_pct,
                reason, factors, data_status, created_at, updated_at
            ) VALUES (
                '2026-07-13', ?, ?, 'manual', '重点池', ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, 'OK', ?, ?
            )
            """,
            [
                (
                    "600001", "测试股份", "IN_ZONE", "WATCHING", 100, 95, 105,
                    102, 92, 2.0, "领先修复等待反转确认",
                    '{"setup_path":"REPAIR_REVERSAL","repair_confidence":"HIGH"}',
                    now, now,
                ),
                (
                    "600002", "观察股份", "WATCHING", "WATCHING", 50, 47, 52,
                    None, 45, None, "等待进入回调区", "{}", now, now,
                ),
            ],
        )
        conn.commit()


def seed_manual_scan(db_path):
    with sqlite3.connect(db_path) as conn:
        WatchHistoricalScanner(db_path).ensure_schema(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stock_pools (
                pool_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                monitor_enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS stock_pool_members (
                pool_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                PRIMARY KEY (pool_id, symbol)
            );
            """
        )
        conn.executemany(
            "INSERT INTO stock_pools (pool_id, name, sort_order) VALUES (?, ?, ?)",
            [("watchlist", "自选股", 1), ("company", "公司", 2)],
        )
        conn.execute(
            """
            INSERT INTO watch_scan_runs (
                scan_id, start_date, end_date, scope, pool_ids, rule_version,
                rule_config_json, trade_dates_json, universe_json, status,
                total_symbols, total_dates, total_cells, processed_cells,
                opportunity_count, created_at, started_at, completed_at, origin
            ) VALUES (
                'manual-scan', '2026-07-13', '2026-07-13', 'all_groups', '[]', 23,
                '{}', '["2026-07-13"]', '[]', 'COMPLETED',
                2, 1, 2, 2, 2, '2026-07-13 21:00:00',
                '2026-07-13 21:00:00', '2026-07-13 21:01:00', 'MANUAL'
            )
            """
        )
        rows = [
            (
                "600003", "历史触发股", ["watchlist"], "TRIGGERED",
                "IN_ZONE", 88.0, 80.0, 86.0, 87.5, 79.0,
                "双底与 MACD 共振确认", "TREND_PULLBACK",
            ),
            (
                "600004", "历史回调股", ["company"], "IN_ZONE",
                "WATCHING", 52.0, 49.0, 53.0, 52.5, 47.0,
                "进入回调区等待确认", "REPAIR_REVERSAL",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO watch_scan_results (
                scan_id, trade_date, symbol, name, pool_ids, watch_state,
                previous_state, setup_id, rule_version, current_price,
                zone_low, zone_high, trigger_price, invalidation_price,
                distance_to_trigger_pct, reason, factors, data_status,
                data_reason, created_at
            ) VALUES (
                'manual-scan', '2026-07-13', ?, ?, ?, ?, ?, 'setup-history', 23,
                ?, ?, ?, ?, ?, 0.5, ?, ?, 'OK', NULL, '2026-07-13 21:01:00'
            )
            """,
            [
                (
                    symbol,
                    name,
                    json.dumps(pool_ids, ensure_ascii=False),
                    state,
                    previous_state,
                    current_price,
                    zone_low,
                    zone_high,
                    trigger_price,
                    invalidation_price,
                    reason,
                    json.dumps({"setup_path": path}, ensure_ascii=False),
                )
                for (
                    symbol,
                    name,
                    pool_ids,
                    state,
                    previous_state,
                    current_price,
                    zone_low,
                    zone_high,
                    trigger_price,
                    invalidation_price,
                    reason,
                    path,
                ) in rows
            ],
        )
        conn.commit()


def seed_intraday_event(db_path, *, observations=None):
    payload = {
        "source": "intraday_5min",
        "name": "海思科",
        "pool_name": "自选股",
        "bar_time": "2026-07-20T09:45:00+08:00",
        "current_price": 71.37,
        "close": 71.37,
        "trigger_price": 70.34,
        "invalidation_price": 65.568,
        "observations": observations
        if observations is not None
        else [
            {
                "symbol": "002653",
                "name": "海思科",
                "pool_name": "自选股",
                "bar_time": "2026-07-20T09:45:00+08:00",
                "current_price": 71.37,
                "trigger_price": 70.34,
                "invalidation_price": 65.568,
                "distance_to_trigger_pct": -1.46,
                "status": "TRIGGERED",
            },
            {
                "symbol": "600584",
                "name": "长电科技",
                "pool_name": "公司",
                "bar_time": "2026-07-20T09:45:00+08:00",
                "current_price": 84.5,
                "trigger_price": 90.41,
                "invalidation_price": 78.651,
                "distance_to_trigger_pct": 6.54,
                "status": "WAITING",
            },
        ],
    }
    with sqlite3.connect(db_path) as conn:
        WatchEngine(db_path).ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO watch_events (
                event_id, dedupe_key, trade_date, symbol, setup_id, event_type,
                from_state, to_state, severity, title, message, status, payload,
                created_at, updated_at
            ) VALUES (
                'intraday-trigger', 'intraday-trigger', '2026-07-20', '002653',
                'setup-intraday', 'TRIGGERED', 'IN_ZONE', 'TRIGGERED', 'HIGH',
                '盘中低吸确认触发',
                '海思科 002653 5分钟收盘价 71.37 突破触发价 70.34。',
                'UNREAD', ?, '2026-07-20 09:45:00', '2026-07-20 09:45:00'
            )
            """,
            (json.dumps(payload, ensure_ascii=False),),
        )


def test_immediate_dispatch_sends_only_urgent_events_and_is_idempotent(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_intraday_event(db_path)
    sent = []

    def sender(subject, body, *, is_html):
        sent.append((subject, body, is_html))
        return EmailDeliveryResult(True)

    dispatcher = WatchNotificationDispatcher(db_path, sender=sender)
    first = dispatcher.dispatch_immediate("2026-07-20")
    second = dispatcher.dispatch_immediate("2026-07-20")

    assert first == {"sent": 1, "failed": 0, "skipped": 0}
    assert second == {"sent": 0, "failed": 0, "skipped": 0}
    assert len(sent) == 1
    assert "低吸确认触发" in sent[0][0]
    assert "EvoAlpha 低吸提醒" in sent[0][1]
    assert "确认触发" in sent[0][1]
    assert "交易纪律" in sent[0][1]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT event_id, status, attempt_count FROM notification_deliveries"
        ).fetchall()
    assert rows == [("intraday-trigger", "SENT", 1)]


def test_end_of_day_trigger_is_digest_only_without_immediate_delivery(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda subject, body, *, is_html: (
            sent.append((subject, body)) or EmailDeliveryResult(True)
        ),
    )

    immediate = dispatcher.dispatch_immediate("2026-07-13")

    with sqlite3.connect(db_path) as conn:
        event_deliveries = conn.execute(
            "SELECT COUNT(*) FROM notification_deliveries WHERE delivery_type='event'"
        ).fetchone()[0]
    assert immediate == {"sent": 0, "failed": 0, "skipped": 0}
    assert sent == []
    assert event_deliveries == 0

    assert dispatcher.dispatch_digest("2026-07-13")["sent"] == 1
    assert sent[0][0] == "低吸日报 · 0 确认 / 1 回调 · 2026-07-13"
    assert "低吸确认触发" in sent[0][1]


def test_legacy_invalidated_email_enriches_name_group_and_state_from_snapshot(
    tmp_path,
):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE watch_daily_snapshot SET pool_name=? "
            "WHERE trade_date='2026-07-13' AND symbol='600001'",
            ("重点池<script>alert(1)</script>",),
        )
        conn.execute(
            """
            INSERT INTO watch_daily_snapshot (
                trade_date, symbol, name, pool_id, pool_name, watch_state,
                previous_state, invalidation_price, data_status
            ) VALUES (
                '2026-07-12', '600001', '测试股份', 'manual', '重点池',
                'IN_ZONE', 'WATCHING', 93.5, 'OK'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO watch_events (
                event_id, dedupe_key, trade_date, symbol, setup_id, event_type,
                from_state, to_state, severity, title, message, status, payload,
                created_at, updated_at
            ) VALUES (
                'invalidated-event', 'invalidated', '2026-07-13', '600001',
                'setup-1', 'INVALIDATED', 'IN_ZONE', 'INVALIDATED', 'HIGH',
                '低吸 setup 已失效', '跌破失效价', 'UNREAD',
                ?,
                '2026-07-13 20:02:00', '2026-07-13 20:02:00'
            )
            """,
            (
                json.dumps(
                    {
                        "current_price": 89,
                        "zone_low": 95,
                        "zone_high": 105,
                        "trigger_price": 102,
                        "invalidation_price": 92,
                        "setup_path": "REPAIR_REVERSAL",
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda subject, body, *, is_html: (
            sent.append((subject, body)) or EmailDeliveryResult(True)
        ),
    )

    result = dispatcher.dispatch_immediate("2026-07-13")

    assert result == {"sent": 1, "failed": 0, "skipped": 0}
    assert len(sent) == 1
    subject, body = sent[0]
    assert subject == "低吸 setup 已失效 · 测试股份 600001"
    assert "测试股份 600001" in body
    assert "重点池" in body
    assert "重点池&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(1)</script>" not in body
    assert "进入回调 → 已经失效" in body
    assert "89.00" in body
    assert "93.50" in body
    assert "92.00" not in body
    assert "跌破失效价" in body
    assert "原低吸 setup 已失效" in body


def test_failed_legacy_end_of_day_trigger_is_skipped_before_retry(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: sent.append(args) or EmailDeliveryResult(True),
    )
    with dispatcher._connect() as conn:
        delivery = dispatcher._ensure_delivery(
            conn,
            dedupe_key="email:event:trigger-event",
            event_id="trigger-event",
            delivery_type="event",
        )
        conn.execute(
            "UPDATE notification_deliveries "
            "SET status='FAILED', attempt_count=1 WHERE delivery_id=?",
            (delivery["delivery_id"],),
        )
        conn.commit()

    result = dispatcher.retry_failed()

    with sqlite3.connect(db_path) as conn:
        delivery = conn.execute(
            "SELECT status, attempt_count, last_error FROM notification_deliveries "
            "WHERE event_id='trigger-event'"
        ).fetchone()
    assert result == {"sent": 0, "failed": 0, "skipped": 1}
    assert sent == []
    assert delivery == (
        "SKIPPED",
        1,
        "end-of-day trigger included in digest",
    )


def test_intraday_email_subject_and_body_include_name_bar_and_observation_list(
    tmp_path,
):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_intraday_event(db_path)
    sent = []

    def sender(subject, body, *, is_html):
        sent.append((subject, body))
        return EmailDeliveryResult(True)

    dispatcher = WatchNotificationDispatcher(db_path, sender=sender)
    assert dispatcher.dispatch_immediate("2026-07-20")["sent"] == 1

    subject, body = sent[0]
    assert subject == "盘中低吸确认触发 · 海思科 002653"
    assert "海思科" in body
    assert "09:45" in body
    assert "5 分钟完成柱" in body
    assert "本轮盘中观察" in body
    assert "自选股" in body
    assert "公司" in body
    assert "长电科技" in body
    assert "等待触发" in body


def test_dispatch_immediate_explicitly_closes_sqlite_connection(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_intraday_event(db_path)

    class TrackingDispatcher(WatchNotificationDispatcher):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.connections = []

        def _connect(self):
            connection = super()._connect()
            self.connections.append(connection)
            return connection

    dispatcher = TrackingDispatcher(
        db_path,
        sender=lambda subject, body, *, is_html: EmailDeliveryResult(True),
    )

    assert dispatcher.dispatch_immediate("2026-07-20")["sent"] == 1
    assert dispatcher.connections
    for connection in dispatcher.connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_intraday_email_ignores_malformed_observation_items(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_intraday_event(db_path, observations=["bad-row", 1, None])
    bodies = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda subject, body, *, is_html: (
            bodies.append(body) or EmailDeliveryResult(True)
        ),
    )

    assert dispatcher.dispatch_immediate("2026-07-20")["sent"] == 1
    assert "海思科" in bodies[0]
    assert "本轮盘中观察" not in bodies[0]


def test_failed_delivery_is_retried_and_error_is_sanitized(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_intraday_event(db_path)
    outcomes = [
        EmailDeliveryResult(False, error="email delivery failed"),
        EmailDeliveryResult(True),
    ]

    def sender(subject, body, *, is_html):
        return outcomes.pop(0)

    dispatcher = WatchNotificationDispatcher(db_path, sender=sender)
    assert dispatcher.dispatch_immediate("2026-07-20")["failed"] == 1
    assert dispatcher.retry_failed()["sent"] == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, attempt_count, last_error FROM notification_deliveries"
        ).fetchone()
    assert row == ("SENT", 2, None)


def test_daily_digest_includes_in_zone_without_duplicate_send(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    bodies = []

    def sender(subject, body, *, is_html):
        bodies.append(body)
        return EmailDeliveryResult(True)

    dispatcher = WatchNotificationDispatcher(db_path, sender=sender)
    first = dispatcher.dispatch_digest("2026-07-13")
    second = dispatcher.dispatch_digest("2026-07-13")

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert "进入回调区" in bodies[0]
    assert "低吸确认触发" in bodies[0]
    assert "今日行动" in bodies[0]
    assert "路径 B · 领先修复" in bodies[0]
    assert "数据可信" in bodies[0]
    assert "回调区" in bodies[0]


def test_trusted_post_calculation_digest_is_sent(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    subjects = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda subject, body, *, is_html: (
            subjects.append(subject) or EmailDeliveryResult(True)
        ),
    )

    result = dispatcher.dispatch_digest("2026-07-13", require_trusted=True)

    assert result == {"sent": 1, "failed": 0, "skipped": 0}
    assert subjects == ["低吸日报 · 0 确认 / 1 回调 · 2026-07-13"]


def test_post_calculation_digest_is_withheld_when_snapshot_is_untrusted(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE watch_daily_snapshot SET watch_state='UNKNOWN', "
            "data_status='MISSING' WHERE symbol='600001'"
        )
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: sent.append(args) or EmailDeliveryResult(True),
    )

    result = dispatcher.dispatch_digest("2026-07-13", require_trusted=True)

    with sqlite3.connect(db_path) as conn:
        deliveries = conn.execute(
            "SELECT COUNT(*) FROM notification_deliveries"
        ).fetchone()[0]
    assert result == {"sent": 0, "failed": 0, "skipped": 1}
    assert sent == []
    assert deliveries == 0


def test_post_calculation_digest_is_withheld_when_snapshot_is_missing(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    with sqlite3.connect(db_path) as conn:
        WatchEngine(db_path).ensure_schema(conn)
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: sent.append(args) or EmailDeliveryResult(True),
    )

    result = dispatcher.dispatch_digest("2026-07-13", require_trusted=True)

    assert result == {"sent": 0, "failed": 0, "skipped": 1}
    assert sent == []


def test_digest_prefers_manual_scan_and_groups_rows_like_watchlist(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    seed_manual_scan(db_path)
    sent = []

    def sender(subject, body, *, is_html):
        sent.append((subject, body))
        return EmailDeliveryResult(True)

    dispatcher = WatchNotificationDispatcher(db_path, sender=sender)
    assert dispatcher.dispatch_digest("2026-07-13", force=True)["sent"] == 1

    subject, body = sent[0]
    assert "1 确认 / 1 回调" in subject
    assert "网站历史遍历" in body
    assert "规则 v23" in body
    assert "自选股" in body
    assert "公司" in body
    assert "历史触发股" in body
    assert "历史回调股" in body


def test_digest_keeps_rendering_when_manual_scan_pool_ids_are_malformed(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    seed_manual_scan(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE watch_scan_results SET pool_ids='not-json' WHERE symbol='600003'"
        )

    dispatcher = WatchNotificationDispatcher(db_path)
    with dispatcher._connect() as conn:
        body = dispatcher._digest_body(conn, "2026-07-13")

    assert "网站历史遍历" in body
    assert "未分组" in body
    assert "历史触发股" in body


def test_manual_digest_resend_creates_a_separate_auditable_delivery(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_events(db_path)
    subjects = []

    def sender(subject, body, *, is_html):
        subjects.append(subject)
        return EmailDeliveryResult(True)

    dispatcher = WatchNotificationDispatcher(db_path, sender=sender)
    assert dispatcher.dispatch_digest("2026-07-13")["sent"] == 1
    assert dispatcher.dispatch_digest("2026-07-13", force=True)["sent"] == 1

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT dedupe_key, delivery_type, status "
            "FROM notification_deliveries ORDER BY created_at, delivery_id"
        ).fetchall()
    assert len(rows) == 2
    regular = next(row for row in rows if row[0] == "email:digest:2026-07-13")
    manual = next(row for row in rows if row[0].startswith("email:digest-manual:"))
    assert regular == ("email:digest:2026-07-13", "digest", "SENT")
    assert manual[0].endswith(":2026-07-13")
    assert manual[1:] == ("digest", "SENT")
    assert subjects[-1].endswith("（人工重发）")


def test_skipped_delivery_stays_pending_until_email_is_enabled(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "watch.db"
    seed_intraday_event(db_path)
    disabled = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: EmailDeliveryResult(
            False,
            skipped=True,
            error="email notifications disabled",
        ),
    )

    assert disabled.dispatch_immediate("2026-07-20")["skipped"] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status, attempt_count FROM notification_deliveries"
        ).fetchone() == ("PENDING", 0)

    enabled = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: EmailDeliveryResult(True),
    )
    assert enabled.retry_failed()["sent"] == 1


def _seed_data_block(db_path, *, resolved_at=None):
    with sqlite3.connect(db_path) as conn:
        WatchEngine(db_path).ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO watch_events (
                event_id, dedupe_key, trade_date, symbol, setup_id, event_type,
                from_state, to_state, severity, title, message, status, payload,
                created_at, updated_at, resolved_at
            ) VALUES (
                'blocked-event', 'data:2026-07-17:lifecycle_missing',
                '2026-07-17', NULL, NULL, 'DATA_BLOCKED', NULL, 'UNKNOWN',
                'CRITICAL', 'Watch Engine 数据阻断',
                '关键数据不完整：lifecycle_missing；已禁止生成买点。',
                'UNREAD', '{"reason":"lifecycle_missing"}',
                '2026-07-19 18:40:53', '2026-07-19 19:01:43', ?
            )
            """,
            (resolved_at,),
        )


def test_resolved_data_block_does_not_create_or_send_immediate_delivery(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "resolved_block.db"
    _seed_data_block(db_path, resolved_at="2026-07-19 19:01:43")
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: sent.append(args) or EmailDeliveryResult(True),
    )

    result = dispatcher.dispatch_immediate("2026-07-17")

    with sqlite3.connect(db_path) as conn:
        delivery_count = conn.execute(
            "SELECT COUNT(*) FROM notification_deliveries"
        ).fetchone()[0]
    assert result == {"sent": 0, "failed": 0, "skipped": 0}
    assert sent == []
    assert delivery_count == 0


def test_failed_data_block_delivery_is_cancelled_if_event_recovers_before_retry(
    tmp_path,
):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "recovered_before_retry.db"
    _seed_data_block(db_path)
    failed = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: EmailDeliveryResult(False),
    )
    assert failed.dispatch_immediate("2026-07-17")["failed"] == 1
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE watch_events SET resolved_at='2026-07-19 19:01:43' "
            "WHERE event_id='blocked-event'"
        )

    sent = []
    recovered = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: sent.append(args) or EmailDeliveryResult(True),
    )
    result = recovered.retry_failed()

    with sqlite3.connect(db_path) as conn:
        delivery = conn.execute(
            "SELECT status, attempt_count, last_error "
            "FROM notification_deliveries WHERE event_id='blocked-event'"
        ).fetchone()
    assert result == {"sent": 0, "failed": 0, "skipped": 1}
    assert sent == []
    assert delivery == ("SKIPPED", 1, "event resolved before delivery")


def seed_portfolio_exit_events(db_path, rows):
    with sqlite3.connect(db_path) as conn:
        WatchEngine(db_path).ensure_schema(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_exit_events (
                id INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                position_revision INTEGER NOT NULL DEFAULT 1,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                event_status TEXT NOT NULL,
                trigger_date TEXT NOT NULL,
                observed_price_micros INTEGER,
                available_quantity INTEGER NOT NULL DEFAULT 0,
                recommended_quantity INTEGER NOT NULL DEFAULT 0,
                executable INTEGER NOT NULL DEFAULT 0,
                tier INTEGER,
                payload_json TEXT,
                bar_time TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO portfolio_exit_events (
                id, account_id, symbol, event_type, severity, event_status,
                trigger_date, observed_price_micros, available_quantity,
                recommended_quantity, executable, tier, payload_json,
                bar_time, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def seed_portfolio_exit_context(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE IF NOT EXISTS portfolio_accounts (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                account_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                position_revision INTEGER NOT NULL DEFAULT 1,
                quantity INTEGER NOT NULL,
                available_quantity INTEGER NOT NULL,
                avg_cost_micros INTEGER NOT NULL,
                PRIMARY KEY (account_id, symbol)
            );
            """
        )
        conn.executemany(
            "INSERT INTO stock_info (symbol, name) VALUES (?, ?)",
            [
                ("000001", "平安银行"),
                ("600519", "贵州茅台"),
                ("<script>", "<img src=x onerror=alert(1)>"),
            ],
        )
        conn.executemany(
            "INSERT INTO portfolio_accounts (id, name) VALUES (?, ?)",
            [(1, "主账户"), (2, "长期账户")],
        )
        conn.executemany(
            """
            INSERT INTO portfolio_positions (
                account_id, symbol, position_revision, quantity,
                available_quantity, avg_cost_micros
            ) VALUES (?, ?, 1, ?, ?, ?)
            """,
            [
                (1, "000001", 100, 80, 9_500_000),
                (1, "600519", 100, 100, 9_500_000),
                (2, "600519", 200, 0, 10_500_000),
            ],
        )
        conn.commit()


def _portfolio_event(
    event_id,
    *,
    account_id=1,
    symbol="600519",
    event_type="HARD_STOP_TRIGGERED",
    severity="CRITICAL",
    status="OPEN",
    trade_date="2026-07-24",
    source="intraday_5min",
    price=10_000_000,
    available=100,
    recommended=100,
    executable=1,
    tier=None,
    payload=None,
    bar_time=None,
):
    event_payload = {"source": source}
    event_payload.update(payload or {})
    return (
        event_id,
        account_id,
        symbol,
        event_type,
        severity,
        status,
        trade_date,
        price,
        available,
        recommended,
        executable,
        tier,
        json.dumps(event_payload, ensure_ascii=False),
        bar_time or f"{trade_date} 14:55:00",
        f"{trade_date} 15:00:00",
    )


def test_portfolio_exit_immediate_is_source_isolated_and_idempotent(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "portfolio-exit-immediate.db"
    seed_portfolio_exit_events(
        db_path,
        [
            _portfolio_event(
                1,
                payload={"hard_stop_micros": 10_200_000},
            ),
            _portfolio_event(2, source="daily"),
            _portfolio_event(3, status="ACKNOWLEDGED"),
        ],
    )
    seed_portfolio_exit_context(db_path)
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda subject, body, *, is_html: (
            sent.append((subject, body, is_html)) or EmailDeliveryResult(True)
        ),
    )

    first = dispatcher.dispatch_portfolio_exit_immediate("2026-07-24")
    second = dispatcher.dispatch_portfolio_exit_immediate("2026-07-24")

    assert first == {"sent": 1, "failed": 0, "skipped": 0}
    assert second == {"sent": 0, "failed": 0, "skipped": 0}
    assert len(sent) == 1
    assert "贵州茅台 600519" in sent[0][0]
    assert "600519" in sent[0][0]
    assert "贵州茅台" in sent[0][1]
    assert "盘中卖点触发" in sent[0][1]
    assert "5 分钟完成柱 · 14:55" in sent[0][1]
    assert "5分钟价" in sent[0][1]
    assert "硬止损线" in sent[0][1]
    assert "¥10.2000" in sent[0][1]
    assert "主账户" in sent[0][1]
    assert "持仓 100 股" in sent[0][1]
    assert "可卖 100 股" in sent[0][1]
    assert "建议卖出 100 股" in sent[0][1]
    assert "为什么现在" in sent[0][1]
    assert "硬止损" in sent[0][1]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT event_id, dedupe_key, delivery_type, status
            FROM notification_deliveries
            """
        ).fetchall() == [
            (
                "portfolio_exit:1",
                "email:portfolio-exit:event:1",
                "portfolio_exit_event",
                "SENT",
            )
        ]


def test_portfolio_exit_immediate_rechecks_open_status_before_send(
    tmp_path,
    monkeypatch,
):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "portfolio-exit-recheck.db"
    seed_portfolio_exit_events(db_path, [_portfolio_event(1)])
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: (
            sent.append(args) or EmailDeliveryResult(True)
        ),
    )
    original = dispatcher._ensure_delivery

    def acknowledge_after_delivery(conn, **kwargs):
        delivery = original(conn, **kwargs)
        conn.execute(
            "UPDATE portfolio_exit_events "
            "SET event_status='ACKNOWLEDGED' WHERE id=1"
        )
        conn.commit()
        return delivery

    monkeypatch.setattr(dispatcher, "_ensure_delivery", acknowledge_after_delivery)

    result = dispatcher.dispatch_portfolio_exit_immediate("2026-07-24")

    assert result == {"sent": 0, "failed": 0, "skipped": 1}
    assert sent == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status FROM notification_deliveries"
        ).fetchone()[0] == "SKIPPED"


def test_failed_portfolio_exit_is_not_retried_after_acknowledgement(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "portfolio-exit-failed.db"
    seed_portfolio_exit_events(db_path, [_portfolio_event(1)])
    failed = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: EmailDeliveryResult(False),
    )
    assert failed.dispatch_portfolio_exit_immediate()["failed"] == 1
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE portfolio_exit_events "
            "SET event_status='ACKNOWLEDGED' WHERE id=1"
        )
        conn.commit()

    sent = []
    retry = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: (
            sent.append(args) or EmailDeliveryResult(True)
        ),
    )
    result = retry.retry_failed()

    assert result == {"sent": 0, "failed": 0, "skipped": 1}
    assert sent == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status, attempt_count FROM notification_deliveries"
        ).fetchone() == ("SKIPPED", 1)


def test_failed_portfolio_digest_is_skipped_when_all_events_resolve(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "portfolio-exit-digest-failed.db"
    seed_portfolio_exit_events(
        db_path,
        [_portfolio_event(1, source="daily")],
    )
    failed = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: EmailDeliveryResult(False),
    )
    assert failed.dispatch_portfolio_exit_digest("2026-07-24")["failed"] == 1
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE portfolio_exit_events SET event_status='RESOLVED' WHERE id=1"
        )
        conn.commit()

    sent = []
    retry = WatchNotificationDispatcher(
        db_path,
        sender=lambda *args, **kwargs: (
            sent.append(args) or EmailDeliveryResult(True)
        ),
    )
    result = retry.retry_failed()

    assert result == {"sent": 0, "failed": 0, "skipped": 1}
    assert sent == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status, attempt_count FROM notification_deliveries"
        ).fetchone() == ("SKIPPED", 1)


def test_portfolio_exit_daily_digest_is_one_grouped_escaped_email(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "portfolio-exit-digest.db"
    seed_portfolio_exit_events(
        db_path,
        [
            _portfolio_event(
                1,
                account_id=2,
                symbol="600519",
                source="daily",
                recommended=0,
                executable=0,
            ),
            _portfolio_event(
                2,
                account_id=1,
                symbol="000001",
                event_type="TAKE_PROFIT_TRIGGERED",
                severity="WARNING",
                source="daily",
                recommended=40,
                executable=1,
                tier=1,
                payload={"threshold_micros": 10_000_000},
            ),
            _portfolio_event(
                3,
                account_id=1,
                symbol="<script>",
                source="daily",
            ),
            _portfolio_event(4, source="intraday_5min"),
        ],
    )
    seed_portfolio_exit_context(db_path)
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda subject, body, *, is_html: (
            sent.append((subject, body)) or EmailDeliveryResult(True)
        ),
    )

    first = dispatcher.dispatch_portfolio_exit_digest("2026-07-24")
    second = dispatcher.dispatch_portfolio_exit_digest("2026-07-24")

    assert first == {"sent": 1, "failed": 0, "skipped": 0}
    assert second == {"sent": 0, "failed": 0, "skipped": 0}
    assert len(sent) == 1
    subject, body = sent[0]
    assert "Portfolio 卖点日报" in subject
    assert body.index("主账户") < body.index("长期账户")
    assert "平安银行" in body
    assert "贵州茅台" in body
    assert "000001" in body
    assert "止盈第 1 档" in body
    assert "¥10.0000" in body
    assert "成本 ¥9.5000" in body
    assert "浮盈 +5.26%" in body
    assert "持仓 100 股" in body
    assert "可卖 80 股" in body
    assert "建议 40 股" in body
    assert "T+1 可执行" in body
    assert "T+1 暂不可执行" in body
    assert "为什么现在" in body
    assert "当前价已达到第 1 档止盈条件" in body
    assert "&lt;script&gt;" in body
    assert "<script>" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body
    assert "<img src=x onerror=alert(1)>" not in body
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM notification_deliveries
            WHERE delivery_type='portfolio_exit_digest'
            """
        ).fetchone()[0] == 1


def test_portfolio_exit_digest_truncates_after_500_rows(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "portfolio-exit-truncated.db"
    seed_portfolio_exit_events(
        db_path,
        [
            _portfolio_event(
                event_id,
                symbol=f"{event_id:06d}",
                source="daily",
            )
            for event_id in range(1, 502)
        ],
    )
    bodies = []
    dispatcher = WatchNotificationDispatcher(
        db_path,
        sender=lambda subject, body, *, is_html: (
            bodies.append(body) or EmailDeliveryResult(True)
        ),
    )

    assert dispatcher.dispatch_portfolio_exit_digest("2026-07-24")["sent"] == 1
    assert "仅展示前 500 条" in bodies[0]
    assert "000500" in bodies[0]
    assert "000501" not in bodies[0]


def test_portfolio_exit_dispatch_without_table_is_zero_write_and_closes(
    tmp_path,
):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "portfolio-exit-no-table.db"

    class TrackingDispatcher(WatchNotificationDispatcher):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.connections = []

        def _connect(self):
            connection = super()._connect()
            self.connections.append(connection)
            return connection

    dispatcher = TrackingDispatcher(
        db_path,
        sender=lambda *args, **kwargs: pytest.fail("sender must not run"),
    )

    assert dispatcher.dispatch_portfolio_exit_immediate() == {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert dispatcher.dispatch_portfolio_exit_digest("2026-07-24") == {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM notification_deliveries"
        ).fetchone()[0] == 0
    assert dispatcher.connections
    for connection in dispatcher.connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


@pytest.mark.parametrize(
    ("argv", "expected_call"),
    (
        (
            ["dispatcher", "--mode", "portfolio-immediate"],
            ("immediate", None),
        ),
        (
            [
                "dispatcher",
                "--mode",
                "portfolio-immediate",
                "--trade-date",
                "2026-07-24",
            ],
            ("immediate", "2026-07-24"),
        ),
        (
            [
                "dispatcher",
                "--mode",
                "portfolio-digest",
                "--trade-date",
                "2026-07-24",
            ],
            ("digest", "2026-07-24"),
        ),
    ),
)
def test_portfolio_cli_modes_route_and_emit_stats(
    monkeypatch,
    capsys,
    argv,
    expected_call,
):
    import quant_engine.watch.dispatcher as dispatcher_module

    calls = []

    class FakeDispatcher:
        def dispatch_portfolio_exit_immediate(self, trade_date=None):
            calls.append(("immediate", trade_date))
            return {"sent": 1, "failed": 0, "skipped": 0}

        def dispatch_portfolio_exit_digest(self, trade_date):
            calls.append(("digest", trade_date))
            return {"sent": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr(
        dispatcher_module,
        "WatchNotificationDispatcher",
        FakeDispatcher,
    )
    monkeypatch.setattr(sys, "argv", argv)

    assert dispatcher_module.main() == 0
    assert calls == [expected_call]
    assert json.loads(capsys.readouterr().out) == {
        "sent": 1,
        "failed": 0,
        "skipped": 0,
    }


def test_portfolio_digest_cli_requires_trade_date(monkeypatch, capsys):
    import quant_engine.watch.dispatcher as dispatcher_module

    monkeypatch.setattr(
        dispatcher_module,
        "WatchNotificationDispatcher",
        lambda: pytest.fail("dispatcher must not be constructed"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["dispatcher", "--mode", "portfolio-digest"],
    )

    with pytest.raises(SystemExit) as error:
        dispatcher_module.main()

    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "requires --trade-date" in captured.err


@pytest.mark.parametrize('path,label', [('TREND_PULLBACK', '路径 A · 强势回调'), ('REPAIR_REVERSAL', '路径 B · 领先修复'), ('', '路径未记录')])
def test_low_buy_alert_explains_strategy_and_source(path, label):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    event = {'payload': json.dumps({'source': 'intraday_5min', 'setup_path': path, 'rule_version': 25}), 'event_type': 'TRIGGERED', 'to_state': 'TRIGGERED', 'from_state': 'IN_ZONE', 'symbol': '600001', 'trade_date': '2026-10-02', 'message': '完成柱确认'}
    body = WatchNotificationDispatcher._event_body(event)
    assert '采用策略：两段式低吸' in body
    assert label in body
    assert '规则 v25' in body
    assert '盘中已完成 5 分钟 K 线确认' in body


def test_strategy_enrichment_uses_event_setup_not_current_rules(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / 'watch.db'
    seed_events(db_path)
    dispatcher = WatchNotificationDispatcher(db_path)
    with dispatcher._connect() as conn:
        conn.execute("INSERT INTO watch_daily_snapshot (trade_date,symbol,setup_id,rule_version,watch_state,data_status,factors) VALUES ('2026-07-12','600001','setup-1',24,'IN_ZONE','OK',?)", (json.dumps({'setup_path': 'REPAIR_REVERSAL'}),))
        conn.execute("INSERT OR REPLACE INTO watch_daily_snapshot (trade_date,symbol,setup_id,rule_version,watch_state,data_status,factors) VALUES ('2026-07-13','600001','new-setup',25,'IN_ZONE','OK',?)", (json.dumps({'setup_path': 'TREND_PULLBACK'}),))
        event = conn.execute("SELECT * FROM watch_events WHERE event_id='trigger-event'").fetchone()
        enriched = dispatcher._enrich_watch_event(conn, event)
        payload = enriched['payload']
        assert payload['rule_version'] == 24
        assert payload['setup_path'] == 'REPAIR_REVERSAL'
        missing = dict(event)
        missing['setup_id'] = 'missing-setup'
        body = dispatcher._event_body(dispatcher._enrich_watch_event(conn, missing))
        assert '规则版本未记录' in body
        assert '路径未记录' in body


def test_strategy_note_escapes_untrusted_rule_version():
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    body = WatchNotificationDispatcher._strategy_note({'rule_version': '<script>bad</script>'})
    assert '<script>' not in body
    assert '&lt;script&gt;' in body


def test_c_scope_without_completed_run_prevents_digest_delivery(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    db = tmp_path / 'watch.db'
    seed_events(db)
    with sqlite3.connect(db) as conn:
        conn.executescript("CREATE TABLE stock_pools(pool_id TEXT, name TEXT);"
                           "CREATE TABLE stock_pool_members(pool_id TEXT, symbol TEXT);"
                           "INSERT INTO stock_pools VALUES('c','C池');"
                           "INSERT INTO stock_pool_members VALUES('c','600001');")
    sent = []
    dispatcher = WatchNotificationDispatcher(db, sender=lambda *a, **k: sent.append(a))
    assert dispatcher.dispatch_digest('2026-07-13')['skipped'] == 1
    assert not sent
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM notification_deliveries').fetchone()[0] == 0


def test_c_digest_renders_same_day_events_with_escaped_identity(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    from quant_engine.watch.structure_monitor import StructureMonitor, SCOPE
    from quant_engine.research.structure_turning import VERSION
    db = tmp_path / 'watch.db'
    seed_events(db)
    monitor = StructureMonitor(db)
    with sqlite3.connect(db) as conn:
        monitor.ensure_schema(conn)
        conn.execute('INSERT INTO structure_monitor_runs VALUES(?,?,?,?,?,?,?,?,?)',
                     ('2026-07-13', VERSION, monitor.config_hash, 'COMPLETED', SCOPE,
                      '{}', '{}', 1, 'now'))
        conn.execute('INSERT INTO structure_monitor_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('c1','2026-07-13','600001',VERSION,monitor.config_hash,'<危险>',
                      '["c"]','["C池"]','BUY_CONFIRM','s','t',
                      '{"price":12.5,"reason":"共振确认"}', 'now'))
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db, sender=lambda *a, **k: sent.append(a) or EmailDeliveryResult(True))
    assert dispatcher.dispatch_digest('2026-07-13')['sent'] == 1
    subject, body = sent[0]
    assert 'C 1 买入确认' in subject
    assert 'C 结构策略' in body and '模拟策略信号' in body
    assert '&lt;危险&gt;' in body and '共振确认' in body and '12.50' in body
    assert '本邮件不含C信号' not in body


@pytest.mark.parametrize('failure', ['blocked', 'coverage', 'old_version'])
def test_c_untrusted_run_does_not_create_delivery(tmp_path, failure):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    from quant_engine.watch.structure_monitor import StructureMonitor, SCOPE
    from quant_engine.research.structure_turning import VERSION
    db = tmp_path / 'watch.db'
    seed_events(db)
    monitor = StructureMonitor(db)
    with sqlite3.connect(db) as conn:
        conn.executescript("CREATE TABLE stock_pools(pool_id TEXT, name TEXT);"
                           "CREATE TABLE stock_pool_members(pool_id TEXT, symbol TEXT);"
                           "INSERT INTO stock_pools VALUES('c','C池');"
                           "INSERT INTO stock_pool_members VALUES('c','600001');")
        monitor.ensure_schema(conn)
        conn.execute('INSERT INTO structure_monitor_runs VALUES(?,?,?,?,?,?,?,?,?)',
                     ('2026-07-13', VERSION if failure != 'old_version' else 'old',
                      monitor.config_hash, 'BLOCKED' if failure == 'blocked' else 'COMPLETED',
                      SCOPE, '{}', '{}', 1, 'now'))
    dispatcher = WatchNotificationDispatcher(db, sender=lambda *a, **k: pytest.fail('must not send'))
    assert dispatcher.dispatch_digest('2026-07-13', force=True)['skipped'] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM notification_deliveries').fetchone()[0] == 0


def test_c_empty_events_still_reports_zero_with_detail_link(tmp_path, monkeypatch):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    dispatcher = WatchNotificationDispatcher(tmp_path / 'unused.db')
    monkeypatch.setattr(dispatcher, '_public_link', lambda path: 'https://example.test/?q="x"&b=1')
    body = dispatcher._c_digest_body(dict(available=True, rows=[], events=[], version='v'))
    assert '当日无新增C事件' in body
    assert 'q=&quot;x&quot;&amp;b=1' in body


def test_c_group_and_reason_are_html_escaped(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    dispatcher = WatchNotificationDispatcher(tmp_path / 'unused.db')
    event = dict(name='正常', symbol='600001', pool_names='["<img src=x>"]',
                 event_type='RISK_WARNING', payload='{"reason":"<script>bad</script>","price":2}')
    body = dispatcher._c_digest_body(dict(available=True, rows=[], events=[event], version='v'))
    assert '&lt;img src=x&gt;' in body and '&lt;script&gt;bad&lt;/script&gt;' in body
    assert '<script>' not in body


def test_c_digest_retry_waits_for_recovery_without_consuming_attempt(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    from quant_engine.watch.structure_monitor import StructureMonitor, SCOPE
    from quant_engine.research.structure_turning import VERSION
    db = tmp_path / 'watch.db'
    seed_events(db)
    monitor = StructureMonitor(db)
    with sqlite3.connect(db) as conn:
        monitor.ensure_schema(conn)
        conn.execute('INSERT INTO structure_monitor_runs VALUES(?,?,?,?,?,?,?,?,?)',
                     ('2026-07-13', VERSION, monitor.config_hash, 'BLOCKED', SCOPE,
                      '{}', '{"BLOCKED":1}', 1, 'now'))
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db, sender=lambda *a, **k: sent.append(a) or EmailDeliveryResult(True))
    with dispatcher._connect() as conn:
        delivery = dispatcher._ensure_delivery(conn, dedupe_key='email:digest:2026-07-13',
                                               event_id=None, delivery_type='digest')
        conn.execute("UPDATE notification_deliveries SET status='FAILED',attempt_count=1 "
                     "WHERE delivery_id=?", (delivery['delivery_id'],))
        conn.commit()
    assert dispatcher.retry_failed()['skipped'] == 1
    assert not sent
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT attempt_count FROM notification_deliveries').fetchone()[0] == 1
        conn.execute("UPDATE structure_monitor_runs SET status='COMPLETED',stats_json='{}'")
    assert dispatcher.retry_failed()['sent'] == 1
    assert len(sent) == 1
@pytest.mark.parametrize("binding,stop,expected", [
    ("BOUND_AT_ENTRY", 9.25, "结构线已绑定"),
    ("UNBOUND", None, "结构线未绑定"),
])
@pytest.mark.parametrize("event_type", ["TREND_EXIT_WATCH", "TREND_EXIT_TRIGGERED"])
def test_portfolio_strategy_c_digest_explains_bound_and_escaped_reason(
    tmp_path, binding, stop, expected, event_type,
):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    db_path = tmp_path / "strategy-c-mail.db"
    seed_portfolio_exit_events(db_path, [_portfolio_event(
        1, event_type=event_type, source="daily",
        payload={"strategy": "C", "reason": "<img src=x onerror=alert(1)>",
                 "stop_binding": binding, "stop_price": stop},
    )])
    sent = []
    dispatcher = WatchNotificationDispatcher(
        db_path, sender=lambda subject, body, *, is_html:
        (sent.append(body) or EmailDeliveryResult(True)),
    )
    assert dispatcher.dispatch_portfolio_exit_digest("2026-07-24")["sent"] == 1
    body = sent[0]
    assert "策略 C" in body
    assert expected in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body
    assert "<img src=x" not in body
    assert "MA20" not in body
    if stop is not None:
        assert "复权结构失效线 9.2500" in body


def test_portfolio_legacy_trend_reason_remains_compatible():
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher

    event = {"payload_json": json.dumps({"qfq_close": 9.0, "ma10": 10.0,
                                        "ma20": 11.0, "macd_hist": -0.1}),
             "event_type": "TREND_EXIT_WATCH", "observed_price_micros": 9_000_000}
    reason = WatchNotificationDispatcher._portfolio_exit_reason(event)
    assert "MA20 11.00" in reason
    assert "策略 C" not in reason


def test_smart_mrgc_initial_day_suppresses_only_automatic_group_events(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    from quant_engine.watch.structure_monitor import StructureMonitor, SCOPE
    from quant_engine.research.structure_turning import VERSION
    db = tmp_path / 'smart.db'
    seed_events(db)
    worker = StructureMonitor(db)
    with sqlite3.connect(db) as conn:
        worker.ensure_schema(conn)
        conn.execute("CREATE TABLE smart_watchlist_state(pool_id TEXT,status TEXT,target_trade_date TEXT,initialized_date TEXT)")
        conn.execute("INSERT INTO smart_watchlist_state VALUES('smart_mrgc_20d','READY','2026-07-13','2026-07-13')")
        conn.execute('INSERT INTO structure_monitor_runs VALUES(?,?,?,?,?,?,?,?,?)',
                     ('2026-07-13', VERSION, worker.config_hash, 'COMPLETED', SCOPE, '{}','{}',1,'now'))
        for event_id, groups in [('auto','["smart_mrgc_20d"]'),('manual','["smart_mrgc_20d","manual"]')]:
            conn.execute('INSERT INTO structure_monitor_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (event_id,'2026-07-13','600001',VERSION,worker.config_hash,'测试',groups,
                          '["MRGC"]','BUY_CONFIRM','s','t','{}','now'))
    dispatcher = WatchNotificationDispatcher(db)
    with closing(dispatcher._connect()) as conn:
        dataset = dispatcher._c_digest_dataset(conn, '2026-07-13')
    assert [event['event_id'] for event in dataset['events']] == ['manual']


def test_smart_mrgc_blocked_update_prevents_trusted_digest(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    db = tmp_path / 'smart.db'
    seed_events(db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE smart_watchlist_state(pool_id TEXT,status TEXT,target_trade_date TEXT,initialized_date TEXT)")
        conn.execute("INSERT INTO smart_watchlist_state VALUES('smart_mrgc_20d','BLOCKED','2026-07-13','2026-07-12')")
    sent = []
    dispatcher = WatchNotificationDispatcher(db, sender=lambda *a, **k: sent.append(a))
    assert dispatcher.dispatch_digest('2026-07-13')['skipped'] == 1
    assert not sent


def test_smart_mrgc_old_failed_digest_leaves_retry_queue(tmp_path):
    from quant_engine.watch.dispatcher import WatchNotificationDispatcher
    db = tmp_path / 'smart.db'
    seed_events(db)
    dispatcher = WatchNotificationDispatcher(db, sender=lambda *a, **k: pytest.fail('旧日报不得补发'))
    with closing(dispatcher._connect()) as conn:
        conn.execute("CREATE TABLE smart_watchlist_state(pool_id TEXT,status TEXT,target_trade_date TEXT,initialized_date TEXT)")
        conn.execute("INSERT INTO smart_watchlist_state VALUES('smart_mrgc_20d','READY','2026-07-14','2026-07-13')")
        delivery = dispatcher._ensure_delivery(conn, dedupe_key='email:digest:2026-07-13', event_id=None, delivery_type='digest')
    assert dispatcher.retry_failed()['skipped'] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT status FROM notification_deliveries WHERE delivery_id=?', (delivery['delivery_id'],)).fetchone()[0] == 'SKIPPED'
