from datetime import date, timedelta
import sqlite3
import pytest
from quant_engine.watch.chan_daily_snapshot import ChanDailySnapshotStore


def rows():
    return [dict(trade_date=(date(2026, 1, 1)+timedelta(days=i)).isoformat(),
                 open=100, high=101, low=99, close=100) for i in range(70)]


def test_roundtrip_idempotent_and_deduplicated(tmp_path, monkeypatch):
    store = ChanDailySnapshotStore(tmp_path/'db.sqlite')
    history = rows(); target = history[-1]['trade_date']
    calendar = [r['trade_date'] for r in history[-3:]]
    first = store.run(target, {'600001': history}, calendar)
    assert first['status'] == 'READY'
    monkeypatch.setattr('quant_engine.watch.chan_daily_snapshot.confirm_daily',
                        lambda *a, **k: pytest.fail('cache must skip replay'))
    assert store.run(target, {'600001': history}, calendar)['reused'] == 1
    assert store.read('600001', target, history, calendar)['status'] == 'READY'
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute('select count(*) from chan_daily_snapshot').fetchone()[0] == 1


def test_correction_and_calendar_change_invalidate(tmp_path):
    store = ChanDailySnapshotStore(tmp_path/'db.sqlite'); history=rows()
    target=history[-1]['trade_date']; calendar=[r['trade_date'] for r in history[-3:]]
    store.run(target, {'600001': history}, calendar)
    history[-1]['close']=100.5
    assert store.read('600001', target, history, calendar) is None
    assert store.run(target, {'600001': history}, calendar)['computed'] == 1
    assert store.read('600001', target, history, [target]) is None
    assert store.load_date(target, ['600001'])['600001']['source']['repainting'] is True


def test_missing_and_bad_input_block_without_fabricating(tmp_path):
    store = ChanDailySnapshotStore(tmp_path/'db.sqlite'); history=rows()
    target=history[-1]['trade_date']
    result=store.run(target, {'600001': history[:-1]}, [target])
    assert result['status']=='BLOCKED'
    assert store.load_date(target,['600001'])['600001']['reason']=='target_bar_missing'
    assert store.load_date('2026-03-01',['600001']) == {}
    with pytest.raises(ValueError): store.run(target, {'invalid': history}, [target])
    with pytest.raises(ValueError): store.run('bad', {'600001': history}, [target])


def test_invalid_ohlc_persisted_as_blocked_and_empty_member_batch(tmp_path):
    store=ChanDailySnapshotStore(tmp_path/'db.sqlite'); history=rows()
    target=history[-1]['trade_date']; history[-1]['close']=float('nan')
    assert store.run(target, {'600001': history}, [target])['blocked']==['600001']
    assert store.read('600001',target,history,[target])['reason']=='invalid_ohlc'
    assert store.run(target,{},[target])['status']=='BLOCKED'
