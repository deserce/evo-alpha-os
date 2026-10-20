"""C monitor endpoint remains read-only, exact-date and bounded."""
import sqlite3
from contextlib import closing

import pytest
from fastapi import HTTPException

from app.api.v1 import structure_candidate as api
from quant_engine.watch.structure_monitor import StructureMonitor


@pytest.mark.parametrize('kwargs', [
    {'trade_date': '20260922'}, {'trade_date': '2026-02-30'},
    {'trade_date': '2020-01-01'}, {'symbol': '600641 OR 1=1'},
    {'pool_id': 'x\' OR 1=1'}, {'pool_id': 'x' * 129},
])
def test_bad_monitor_filters_rejected(kwargs):
    with pytest.raises(HTTPException) as caught:
        api.get_structure_monitor(**kwargs)
    assert caught.value.status_code == 422


def test_absent_schema_does_not_write_or_fallback(tmp_path, monkeypatch):
    path = tmp_path / 'monitor.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE unrelated(value TEXT)')
        conn.commit()
    monkeypatch.setattr(api, 'DB_PATH', path)
    before = path.read_bytes()
    result = api.get_structure_monitor('2026-09-22').data
    assert result['run'] is None
    assert result['trade_date'] == '2026-09-22'
    assert result['simulated'] is True
    assert path.read_bytes() == before


def test_monitor_passes_exact_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB_PATH', tmp_path / 'unused.db')
    def read(self, trade_date, *, symbol, pool_id):
        assert (trade_date, symbol, pool_id) == ('2026-09-22', '600641', 'pool_test')
        return {'run': None, 'trade_date': trade_date, 'rows': [], 'events': []}
    monkeypatch.setattr(StructureMonitor, 'read_status', read)
    assert api.get_structure_monitor('2026-09-22', '600641', 'pool_test').data['events'] == []


def test_database_error_is_service_unavailable(tmp_path, monkeypatch):
    path = tmp_path / 'broken.db'
    path.write_text('not sqlite')
    monkeypatch.setattr(api, 'DB_PATH', path)
    with pytest.raises(HTTPException) as caught:
        api.get_structure_monitor('2026-09-22')
    assert caught.value.status_code == 503


def test_corrupt_saved_json_is_service_unavailable(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError('malformed saved JSON')
    monkeypatch.setattr(StructureMonitor, 'read_status', broken)
    with pytest.raises(HTTPException) as caught:
        api.get_structure_monitor('2026-09-22')
    assert caught.value.status_code == 503
