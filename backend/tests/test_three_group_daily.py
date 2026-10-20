"""Temporary DB and fake transport only; never send production mail."""
from contextlib import closing
from datetime import date,datetime,timedelta
import hashlib
import json
import sqlite3
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from app.notifications.email_notifier import EmailDeliveryResult
from quant_engine.watch import three_group_daily as daily
from quant_engine.watch.structure_monitor import StructureMonitor
from quant_engine.research.structure_turning import VERSION

TARGET='2026-09-30'
NOW=datetime(2026,9,30,16,0,tzinfo=ZoneInfo('Asia/Shanghai'))


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'daily.db'
    with closing(sqlite3.connect(path)) as conn,conn:
        conn.executescript('''CREATE TABLE trading_calendar(trade_date TEXT,is_open INTEGER);
        CREATE TABLE stock_daily_prices(symbol TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL);
        CREATE TABLE strategy_run_log(strategy_name TEXT,trade_date TEXT,status TEXT);
        CREATE TABLE quant_preselect_results(strategy_name TEXT,trade_date TEXT,symbol TEXT);
        CREATE TABLE stock_pools(pool_id TEXT,name TEXT);
        CREATE TABLE stock_pool_members(pool_id TEXT,symbol TEXT);
        CREATE TABLE data_quality_report(trade_date TEXT,module TEXT,market TEXT,status TEXT,created_at TEXT);''')
        for i in range(20):
            day=(date(2026,9,11)+timedelta(days=i)).isoformat()
            conn.execute('INSERT INTO trading_calendar VALUES (?,1)',(day,))
            conn.execute("INSERT INTO strategy_run_log VALUES ('mrgc_v1',?,'success')",(day,))
            conn.execute("INSERT INTO stock_daily_prices VALUES ('600001',?,10,11,9,10,100)",(day,))
        conn.execute("INSERT INTO quant_preselect_results VALUES ('mrgc_v1',?,'600001')",(TARGET,))
        conn.executemany('INSERT INTO stock_pools VALUES (?,?)', [('pool_0d1aea597dc4','公司'),('watchlist','自选股')])
        conn.executemany('INSERT INTO stock_pool_members VALUES (?,?)',[('pool_0d1aea597dc4','600001'),('watchlist','600001')])
        conn.execute("INSERT INTO data_quality_report VALUES (?,'stock_daily','SH','PASS',?)",(TARGET,TARGET))
    return path


@pytest.fixture
def prepared(db,monkeypatch):
    monkeypatch.setattr(daily,'_now',lambda:NOW)
    monkeypatch.setattr(daily,'source_signature',lambda *args:'a'*64)
    monkeypatch.setattr(daily,'read_archive',lambda *args:None)
    def build(path,target,**kwargs):
        groups=kwargs['frozen_groups']
        symbols=sorted({symbol for group in groups for symbol in group['symbols']})
        return {'date':target,'status':'READY','source':{},'missing':[],'groups':groups,
                'stocks':[{'symbol':symbol,'chan':{'status':'READY'}} for symbol in symbols]}
    monkeypatch.setattr(daily,'build_report',build)
    monkeypatch.setattr(daily,'_ensure_c',lambda *args:None)
    monkeypatch.setattr(daily,'_ensure_chan',lambda *args:None)
    monkeypatch.setattr(daily,'render_email',lambda report:'<html>frozen</html>')
    monkeypatch.setattr(daily,'write_archive',lambda path,report,signature:{'archive_id':TARGET+'-1234567890abcdef'})
    assert daily.prepare(db,TARGET)['status']=='READY'
    return db


def test_freeze_before_calculate_and_members_do_not_drift(db,monkeypatch):
    monkeypatch.setattr(daily,'_now',lambda:NOW)
    def inspect_then_fail(path,target,groups):
        with closing(sqlite3.connect(path)) as conn:
            stored=conn.execute('SELECT groups_json FROM three_group_daily_runs').fetchone()
            assert stored and json.loads(stored[0])==groups
        raise ValueError('controlled_missing_dependency')
    monkeypatch.setattr(daily,'_ensure_c',inspect_then_fail)
    monkeypatch.setattr(daily,'source_signature',lambda *args:'a'*64)
    monkeypatch.setattr(daily,'build_report',lambda *args,**kwargs:{'date':TARGET,'groups':kwargs['frozen_groups'],'status':'BLOCKED','missing':['C']})
    assert daily.prepare(db,TARGET)['status']=='BLOCKED'
    with closing(daily._connect(db)) as conn,conn:
        before=conn.execute('SELECT groups_json FROM three_group_daily_runs').fetchone()[0]
        conn.execute("INSERT INTO stock_pool_members VALUES ('watchlist','600002')")
        assert daily._freeze(conn,TARGET,NOW)==json.loads(before)
    assert '600002' not in before


def test_single_mail_and_revision_does_not_add_send(prepared):
    sender=Mock(return_value=EmailDeliveryResult(True))
    assert daily.deliver(prepared,TARGET,send=False,now=NOW,sender=sender)['status']=='READY'
    sender.assert_not_called()
    assert daily.deliver(prepared,TARGET,send=True,now=NOW,sender=sender)['status']=='SENT'
    with closing(daily._connect(prepared)) as conn,conn:
        conn.execute('UPDATE three_group_daily_runs SET report_id=?',(TARGET+'-abcdef0123456789',))
    assert daily.deliver(prepared,TARGET,send=True,now=NOW,sender=sender)['status']=='SKIPPED'
    assert sender.call_count==1
    with closing(daily._connect(prepared)) as conn:
        record=conn.execute('SELECT delivery_type,dedupe_key,status FROM notification_deliveries').fetchone()
        assert record[0]=='three_group_daily' and record[1].startswith('three_group_daily:'+TARGET)
        assert record[2]=='SENT'


def test_uncertain_send_and_interrupted_state_not_retried(prepared):
    sender=Mock(side_effect=RuntimeError('SMTP disconnected after DATA'))
    assert daily.deliver(prepared,TARGET,True,NOW,sender)['status']=='UNKNOWN'
    assert daily.retry(prepared,TARGET,True,NOW+timedelta(minutes=30),sender)['status']=='UNKNOWN'
    assert sender.call_count==1
    with closing(daily._connect(prepared)) as conn,conn:
        conn.execute("UPDATE notification_deliveries SET status='SENDING'")
    assert daily.deliver(prepared,TARGET,True,NOW,sender)['status']=='UNKNOWN'
    assert sender.call_count==1


def test_default_generic_smtp_failure_is_unknown(prepared,monkeypatch):
    sender=Mock(return_value=EmailDeliveryResult(False,error='email delivery failed'))
    monkeypatch.setattr(daily,'send_notification',sender)
    assert daily.deliver(prepared,TARGET,True,NOW)['status']=='UNKNOWN'
    assert daily.retry(prepared,TARGET,True,NOW+timedelta(minutes=30))['status']=='UNKNOWN'
    assert sender.call_count==1


def test_explicit_failed_retry_backoff_and_limit(prepared):
    sender=Mock(return_value=EmailDeliveryResult(False,error='SMTP explicit refusal'))
    assert daily.deliver(prepared,TARGET,True,NOW,sender)['status']=='FAILED'
    assert daily.retry(prepared,TARGET,True,NOW+timedelta(minutes=1),sender)['reason']=='retry_backoff'
    assert daily.retry(prepared,TARGET,True,NOW+timedelta(minutes=5),sender)['status']=='FAILED'
    assert daily.retry(prepared,TARGET,True,NOW+timedelta(minutes=15),sender)['status']=='FAILED'
    assert daily.retry(prepared,TARGET,True,NOW+timedelta(minutes=60),sender)['reason']=='delivery_attempt_limit'
    assert sender.call_count==3


def test_cross_day_before_close_and_changed_source_block(prepared,monkeypatch):
    sender=Mock(return_value=EmailDeliveryResult(True))
    assert daily.deliver(prepared,TARGET,True,NOW+timedelta(days=1),sender)['status']=='SKIPPED'
    assert daily.deliver(prepared,TARGET,True,NOW.replace(hour=15,minute=29),sender)['status']=='SKIPPED'
    monkeypatch.setattr(daily,'source_signature',lambda *args:'b'*64)
    assert daily.deliver(prepared,TARGET,True,NOW,sender)['status']=='BLOCKED'
    sender.assert_not_called()


def test_chan_blocked_never_freezes_complete(db,prepared,monkeypatch):
    with closing(daily._connect(db)) as conn,conn:
        conn.execute("UPDATE three_group_daily_runs SET status='FROZEN'")
    monkeypatch.setattr(daily,'build_report',lambda *args,**kwargs:{'date':TARGET,'groups':kwargs['frozen_groups'],
        'source':{},'status':'READY','missing':[],'stocks':[{'symbol':'600001','chan':{'status':'BLOCKED'}}]})
    assert daily.prepare(db,TARGET)['reason']=='chan_member_coverage_not_ready'
    sender=Mock()
    assert daily.deliver(db,TARGET,True,NOW,sender)['status']=='BLOCKED'
    sender.assert_not_called()


def test_c_historical_repair_invalidates_reuse_and_preserves_other_pool(db,monkeypatch):
    groups=[{'id':'watchlist','pool_id':'watchlist','name':'自选股','symbols':['600001']}]
    with closing(daily._connect(db)) as conn:
        daily._schema(conn)
        StructureMonitor.ensure_schema(conn)
        conn.execute('INSERT INTO structure_monitor_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (TARGET,'600099',VERSION,StructureMonitor(db).config_hash,'其他池','[]','[]','WATCHING','OK','',10,'{}',TARGET))
        conn.commit()
    evaluate=Mock(return_value={'summary':{'status':'ok'},'state':{'status':'OBSERVING'},'markers':[
        {'trade_date':TARGET,'marker_type':'BUY_CONFIRM','reason':'fact'}]})
    monkeypatch.setattr(daily,'evaluate_structure_turning',evaluate)
    daily._ensure_c(db,TARGET,groups)
    daily._ensure_c(db,TARGET,groups)
    assert evaluate.call_count==1
    with closing(daily._connect(db)) as conn,conn:
        conn.execute('UPDATE stock_daily_prices SET high=12 WHERE trade_date=?',( '2026-09-11',))
    daily._ensure_c(db,TARGET,groups)
    assert evaluate.call_count==2
    with closing(daily._connect(db)) as conn:
        assert conn.execute("SELECT 1 FROM structure_monitor_snapshots WHERE symbol='600099'").fetchone()
        event=conn.execute('SELECT trade_date,symbol FROM structure_monitor_events').fetchone()
        assert tuple(event)==(TARGET,'600001')


def test_busy_does_not_mutate_existing_run(prepared):
    with daily._lock(prepared,TARGET):
        result=daily.prepare(prepared,TARGET)
        assert result['reason']=='three_group_daily_busy'
    with closing(daily._connect(prepared)) as conn:
        assert conn.execute('SELECT status FROM three_group_daily_runs').fetchone()[0]=='READY'


def test_prepare_new_source_resets_exhausted_attempts(prepared,monkeypatch):
    with closing(daily._connect(prepared)) as conn,conn:
        conn.execute("UPDATE three_group_daily_runs SET status='BLOCKED',attempt_count=3,last_input_hash='old'")
    assert daily.prepare(prepared,TARGET)['status']=='READY'
    with closing(daily._connect(prepared)) as conn:
        assert conn.execute('SELECT attempt_count FROM three_group_daily_runs').fetchone()[0]==1


def test_empty_three_groups_are_legal(db,monkeypatch):
    monkeypatch.setattr(daily,'_now',lambda:NOW)
    with closing(daily._connect(db)) as conn,conn:
        conn.execute('DELETE FROM quant_preselect_results')
        conn.execute('DELETE FROM stock_pool_members')
        daily._schema(conn)
        groups=daily._freeze(conn,TARGET,NOW)
        assert all(group['symbols']==[] for group in groups)
    chan=Mock()
    monkeypatch.setattr(daily,'ChanDailySnapshotStore',chan)
    daily._ensure_chan(db,TARGET,groups,['2026-09-28','2026-09-29',TARGET])
    chan.assert_not_called()


def test_retention_only_for_real_current_trade_day(prepared):
    with closing(daily._connect(prepared)) as conn,conn:
        conn.execute('INSERT INTO three_group_daily_runs '
            '(trade_date,frozen_at,groups_json,member_hash,membership_source,status,updated_at) '
            "VALUES ('2025-01-01','old','[]','hash','history','READY','old')")
        daily._prune(conn,'2026-01-01',NOW)
        assert conn.execute("SELECT 1 FROM three_group_daily_runs WHERE trade_date='2025-01-01'").fetchone()
        daily._prune(conn,TARGET,NOW)
        assert not conn.execute("SELECT 1 FROM three_group_daily_runs WHERE trade_date='2025-01-01'").fetchone()
        assert conn.execute('SELECT 1 FROM three_group_daily_runs WHERE trade_date=?',(TARGET,)).fetchone()


def test_invalid_sender_outcome_is_unknown(prepared):
    assert daily.deliver(prepared,TARGET,True,NOW,lambda *args,**kwargs:None)['status']=='UNKNOWN'


def test_real_rule_archive_zero_group_prepare_is_idempotent(db,monkeypatch):
    monkeypatch.setattr(daily,'_now',lambda:NOW)
    with closing(daily._connect(db)) as conn,conn:
        conn.execute('DELETE FROM quant_preselect_results')
        conn.execute('DELETE FROM stock_pool_members')
    first=daily.prepare(db,TARGET)
    assert first['status']=='READY',first
    archived=daily.read_archive(db,TARGET)
    assert archived and archived['valid'],archived
    assert archived['report']['source']['publication_mode']=='rules'
    second=daily.prepare(db,TARGET)
    assert second['status']=='READY' and second['reason']=='reused',second
    assert first['report_id']==second['report_id']


def test_missing_calendar_blocks_and_closed_day_skips(db):
    with closing(daily._connect(db)) as conn,conn:
        conn.execute('DROP TABLE trading_calendar')
    assert daily.prepare(db,TARGET)['status']=='BLOCKED'
    with closing(daily._connect(db)) as conn,conn:
        conn.execute('CREATE TABLE trading_calendar(trade_date TEXT,is_open INTEGER)')
        conn.execute('INSERT INTO trading_calendar VALUES (?,0)',(TARGET,))
    assert daily.prepare(db,TARGET)['status']=='SKIPPED'


def test_retry_uses_original_frozen_body_and_report_id(prepared):
    sender=Mock(side_effect=[EmailDeliveryResult(False,error='SMTP explicit refusal'),EmailDeliveryResult(True)])
    assert daily.deliver(prepared,TARGET,True,NOW,sender)['status']=='FAILED'
    with closing(daily._connect(prepared)) as conn,conn:
        conn.execute('UPDATE three_group_daily_runs SET report_id=?,email_html=?',
                     (TARGET+'-newrevision00000','<html>new revision</html>'))
    outcome=daily.retry(prepared,TARGET,True,NOW+timedelta(minutes=5),sender)
    assert outcome['status']=='SENT' and outcome['report_id']==TARGET+'-1234567890abcdef'
    assert sender.call_args_list[0].args[1]==sender.call_args_list[1].args[1]=='<html>frozen</html>'
    with closing(daily._connect(prepared)) as conn:
        content=conn.execute('SELECT email_html,report_id FROM three_group_daily_delivery_content').fetchone()
        assert tuple(content)==('<html>frozen</html>',TARGET+'-1234567890abcdef')


def test_stale_frozen_retry_is_skipped_not_replaced(prepared,monkeypatch):
    sender=Mock(return_value=EmailDeliveryResult(False,error='SMTP explicit refusal'))
    assert daily.deliver(prepared,TARGET,True,NOW,sender)['status']=='FAILED'
    monkeypatch.setattr(daily,'source_signature',lambda *args:'b'*64)
    outcome=daily.retry(prepared,TARGET,True,NOW+timedelta(minutes=5),sender)
    assert outcome['status']=='SKIPPED' and outcome['reason']=='frozen_delivery_source_changed'
    assert sender.call_count==1
    with closing(daily._connect(prepared)) as conn:
        assert conn.execute('SELECT status FROM notification_deliveries').fetchone()[0]=='SKIPPED'
