"""Legacy retries must leave independently frozen three-group reports alone."""
from contextlib import closing
import sqlite3
from unittest.mock import Mock

from quant_engine.watch.engine import WatchEngine
from quant_engine.watch.dispatcher import WatchNotificationDispatcher


def test_legacy_retry_does_not_resolve_or_send_three_group_deliveries(tmp_path):
    path = tmp_path / 'test.db'
    with closing(sqlite3.connect(path)) as conn, conn:
        WatchEngine(path).ensure_schema(conn)
        for status in ('FAILED', 'PENDING', 'UNKNOWN'):
            conn.execute('INSERT INTO notification_deliveries '
                         '(delivery_id,event_id,channel,delivery_type,dedupe_key,status,created_at,updated_at) '
                         "VALUES (?,NULL,'email','three_group_daily',?,?,?,?)",
                         (status, 'three_group_daily:2026-09-30:' + status, status, 'now', 'now'))
    sender = Mock(side_effect=AssertionError('legacy must not send this type'))
    WatchNotificationDispatcher(path, sender=sender).retry_failed()
    with closing(sqlite3.connect(path)) as conn:
        assert dict(conn.execute('SELECT delivery_id,status FROM notification_deliveries')) == {
            'FAILED': 'FAILED', 'PENDING': 'PENDING', 'UNKNOWN': 'UNKNOWN'}
    sender.assert_not_called()
