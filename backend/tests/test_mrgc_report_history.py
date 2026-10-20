from contextlib import closing
import json
import sqlite3
import pytest
from tests.test_structure_monitor import make_db, _buy_day


def test_exact_date_no_writes(tmp_path):
    from quant_engine.watch.mrgc_report_history import calculate_history
    path, prices = make_db(tmp_path)
    before = path.read_bytes()
    target = _buy_day(prices)
    snapshots, events = calculate_history(path, target, ['600641'])
    assert snapshots['600641']['data_status'] == 'OK'
    assert json.loads(snapshots['600641']['state_json'])['status'] == 'HOLDING'
    assert [e['event_type'] for e in events['600641']] == ['BUY_CONFIRM']
    assert all(e['trade_date'] == target for e in events['600641'])
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM stock_daily_prices WHERE trade_date>?', (target,))
    assert calculate_history(path, target, ['600641']) == (snapshots, events)


def test_quality_failure(tmp_path):
    from quant_engine.watch.mrgc_report_history import calculate_history
    path, prices = make_db(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET status='FAIL' WHERE trade_date=?", (target,))
    snapshots, events = calculate_history(path, target, ['600641'])
    assert snapshots['600641']['reason'] == 'stock_daily_quality_failed:SH'
    assert events['600641'] == []


def test_budget_and_validation(tmp_path, monkeypatch):
    import quant_engine.watch.mrgc_report_history as history
    path, prices = make_db(tmp_path)
    ticks = iter([0, 91])
    monkeypatch.setattr(history.time, 'monotonic', lambda: next(ticks))
    snapshots, _ = history.calculate_history(path, _buy_day(prices), ['600641'])
    assert snapshots['600641']['reason'] == 'history_time_budget_exceeded'
    with pytest.raises(ValueError):
        history.calculate_history(path, _buy_day(prices), [f'{i:06d}' for i in range(301)])
    with pytest.raises(ValueError):
        history.calculate_history(path, _buy_day(prices), ['bad'])


def test_missing_target_bar_stays_blocked(tmp_path):
    from quant_engine.watch.mrgc_report_history import calculate_history
    path, prices = make_db(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM stock_daily_prices WHERE trade_date=?', (target,))
    snapshots, events = calculate_history(path, target, ['600641'])
    assert snapshots['600641']['reason'] == 'target_daily_bar_missing'
    assert snapshots['600641']['current_price'] is None
    assert events['600641'] == []


def test_progress_and_duplicate_symbol_are_bounded(tmp_path):
    from quant_engine.watch.mrgc_report_history import calculate_history
    path, prices = make_db(tmp_path)
    updates = []
    snapshots, _ = calculate_history(path, _buy_day(prices), ['600641', '600641'],
                                    progress=lambda completed, total: updates.append((completed, total)))
    assert list(snapshots) == ['600641']
    assert updates == [(1, 1)]


def test_observation_markers_are_not_report_trade_events(tmp_path):
    from quant_engine.watch.mrgc_report_history import calculate_history
    from quant_engine.research.structure_turning import evaluate_structure_turning
    path, prices = make_db(tmp_path)
    observed = next(marker['trade_date'] for marker in evaluate_structure_turning(prices)['markers']
                    if marker['marker_type'] == 'OBSERVE')
    _, events = calculate_history(path, observed, ['600641'])
    assert all(event['event_type'] in {'BUY_CONFIRM', 'EXIT', 'INVALIDATED', 'RISK_WARNING'}
               for event in events['600641'])
