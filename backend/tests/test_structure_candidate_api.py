"""结构候选端点的隔离、校验和资源边界。"""
import sqlite3
from contextlib import closing

import pytest
from fastapi import HTTPException

from app.api.v1 import structure_candidate as api


@pytest.fixture
def candidate_db(tmp_path, monkeypatch):
    path = tmp_path / 'prices.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE stock_daily_prices(symbol TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,PRIMARY KEY(symbol,trade_date))')
        conn.execute("INSERT INTO stock_daily_prices VALUES('600641','2026-09-01',10,11,9,10,100)")
        conn.commit()
    monkeypatch.setattr(api, 'DB_PATH', path)
    return path


def test_candidate_is_read_only_and_filters_visible_markers(candidate_db, monkeypatch):
    original = candidate_db.read_bytes()
    def evaluate(prices):
        assert prices[0]['trade_date'] == '2026-09-01'
        return {'version':'test','markers':[
            {'trade_date':'2026-08-01'}, {'trade_date':'2026-09-01'},
        ], 'trades':[], 'summary':{}}
    monkeypatch.setattr(api, 'evaluate_structure_turning', evaluate)
    result = api.get_structure_candidate('600641','2026-09-01','2026-09-21').data
    assert result['experimental'] is True
    assert result['markers'] == [{'trade_date':'2026-09-01'}]
    assert candidate_db.read_bytes() == original


@pytest.mark.parametrize('symbol,start,end', [
    ('bad','2026-09-01','2026-09-21'),
    ('600641','bad','2026-09-21'),
    ('600641','2026-09-21','2026-09-01'),
    ('600641','2020-01-01','2026-09-21'),
    ('600641','0001-01-01','0001-01-02'),
    ('600641','9999-12-01','9999-12-31'),
    ('600641','20260901','2026-09-21'),
    ('600641','2026-W36-1','2026-09-21'),
])
def test_invalid_query_rejected_without_read(symbol,start,end):
    with pytest.raises(HTTPException) as caught:
        api.get_structure_candidate(symbol,start,end)
    assert caught.value.status_code == 422


def test_missing_database_does_not_create_file(tmp_path, monkeypatch):
    missing = tmp_path / 'missing.db'
    monkeypatch.setattr(api, 'DB_PATH', missing)
    with pytest.raises(HTTPException) as caught:
        api.get_structure_candidate('600641','2026-09-01','2026-09-21')
    assert caught.value.status_code == 503
    assert not missing.exists()


def test_no_data_safe_empty(candidate_db):
    result = api.get_structure_candidate('000001','2026-09-01','2026-09-21').data
    assert result['markers'] == []
    assert result['history_start'] is None


def test_visible_window_does_not_change_calculation_origin(candidate_db, monkeypatch):
    inputs = []
    def evaluate(prices):
        inputs.append(prices)
        return {'version':'test', 'markers':[], 'trades':[], 'summary':{}}
    monkeypatch.setattr(api, 'evaluate_structure_turning', evaluate)
    api.get_structure_candidate('600641','2026-09-01','2026-09-21')
    api.get_structure_candidate('600641','2026-09-20','2026-09-21')
    assert inputs[0] == inputs[1]
    assert len(inputs[1]) == 1
