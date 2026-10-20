"""Frozen, rule-only three-group daily workflow. Mail requires explicit --send.

No models, no resident process and no manual scan substitution. SMTP outcomes
are recorded separately from existing low-buy/portfolio reminder dedupe keys.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.notifications.email_notifier import EmailDeliveryResult, send_notification
from quant_engine.research.structure_turning import VERSION, evaluate_structure_turning
from quant_engine.watch.chan_daily_snapshot import ChanDailySnapshotStore
from quant_engine.watch.engine import DB_PATH
from quant_engine.watch.mrgc_daily_report import GROUP_DEFS, build_report, render_email
from quant_engine.watch.mrgc_report_archive import read_archive, source_signature, write_archive
from quant_engine.watch.smart_mrgc_pool import resolve_members
from quant_engine.watch.structure_monitor import StructureMonitor, _day


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _now() -> datetime:
    return datetime.now(ZoneInfo('Asia/Shanghai'))


def _connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _schema(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS three_group_daily_runs (
        trade_date TEXT PRIMARY KEY, frozen_at TEXT NOT NULL, groups_json TEXT NOT NULL,
        member_hash TEXT NOT NULL, membership_source TEXT NOT NULL,
        status TEXT NOT NULL, report_id TEXT, signature TEXT, report_json TEXT, email_html TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0, reason TEXT, updated_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS notification_deliveries (
        delivery_id TEXT PRIMARY KEY,event_id TEXT,channel TEXT NOT NULL DEFAULT 'email',
        delivery_type TEXT NOT NULL DEFAULT 'event',dedupe_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'PENDING',attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,next_retry_at TEXT,sent_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(three_group_daily_runs)')}
    if 'last_input_hash' not in columns:
        conn.execute('ALTER TABLE three_group_daily_runs ADD COLUMN last_input_hash TEXT')
    conn.execute('''CREATE TABLE IF NOT EXISTS three_group_daily_delivery_content (
        delivery_id TEXT PRIMARY KEY,trade_date TEXT NOT NULL,report_id TEXT NOT NULL,
        signature TEXT NOT NULL,report_json TEXT NOT NULL,email_html TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS three_group_c_inputs (
        trade_date TEXT NOT NULL,symbol TEXT NOT NULL,input_hash TEXT NOT NULL,
        PRIMARY KEY(trade_date,symbol))''')
    conn.commit()


@contextmanager
def _lock(path: Path | str, target: str, *, workflow_child: bool = False, writer: bool = False):
    root = Path(path).parent
    with (root / '.three_group_daily.lock').open('a+') as own:
        try:
            fcntl.flock(own, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('three_group_daily_busy') from exc
        if not writer:
            yield
            return
        with (root / '.daily_data_workflow.lock').open('a+') as data_lock:
            inherited = False
            try:
                fcntl.flock(data_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                data_lock.seek(0)
                try:
                    metadata = json.load(data_lock)
                except (ValueError, TypeError):
                    metadata = {}
                inherited = (workflow_child and isinstance(metadata, dict)
                    and metadata.get('pid') == os.getppid()
                    and metadata.get('target_trade_date') == target
                    and metadata.get('stage') == 'three_group_report_prepare')
                if not inherited:
                    raise RuntimeError('daily_workflow_writer_busy') from exc
            try:
                yield
            finally:
                if not inherited:
                    fcntl.flock(data_lock, fcntl.LOCK_UN)


def _calendar(conn: sqlite3.Connection, target: str) -> list[str]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trading_calendar'").fetchone():
        raise ValueError('trading_calendar_missing')
    target_rows = conn.execute('SELECT DISTINCT is_open FROM trading_calendar WHERE '
                               'substr(trade_date,1,10)=? LIMIT 2',(target,)).fetchall()
    if len(target_rows) != 1:
        raise ValueError('target_calendar_missing_or_ambiguous')
    if target_rows[0][0] != 1:
        return []
    days = [r[0] for r in conn.execute('SELECT DISTINCT substr(trade_date,1,10) FROM trading_calendar '
        'WHERE is_open=1 AND trade_date<? ORDER BY 1 DESC LIMIT 3', (target + 'T',))]
    return sorted(days) if days and days[0] == target else []


def _prune(conn: sqlite3.Connection, target: str, now: datetime) -> None:
    if target != now.date().isoformat():
        return
    cutoff = (now.date()-timedelta(days=180)).isoformat()
    with conn:
        for table in ('three_group_daily_runs','three_group_c_inputs','three_group_daily_delivery_content'):
            conn.execute(f'DELETE FROM {table} WHERE rowid IN '
                f'(SELECT rowid FROM {table} WHERE trade_date<? LIMIT 5000)',(cutoff,))
        conn.execute("DELETE FROM notification_deliveries WHERE rowid IN (SELECT rowid FROM notification_deliveries "
            "WHERE delivery_type='three_group_daily' AND created_at<? LIMIT 1000)",(cutoff,))


def _freeze(conn: sqlite3.Connection, target: str, now: datetime) -> list[dict]:
    prior = conn.execute('SELECT groups_json FROM three_group_daily_runs WHERE trade_date=?', (target,)).fetchone()
    if prior:
        return json.loads(prior[0])
    resolved = resolve_members(conn, target)
    if resolved['status'] != 'READY':
        raise ValueError('mrgc_membership_not_ready')
    groups = []
    for gid, name, pid in GROUP_DEFS:
        if gid == 'mrgc':
            symbols = [row['symbol'] for row in resolved['members']]
        else:
            pool = conn.execute('SELECT pool_id FROM stock_pools WHERE pool_id=?', (pid,)).fetchone()
            if not pool and gid == 'company':
                matches = conn.execute('SELECT pool_id FROM stock_pools WHERE name=? LIMIT 2', ('公司',)).fetchall()
                pool = matches[0] if len(matches) == 1 else None
            if not pool:
                raise ValueError('group_membership_not_ready')
            pid = pool[0]
            symbols = [row[0] for row in conn.execute('SELECT DISTINCT symbol FROM stock_pool_members '
                                                    'WHERE pool_id=? ORDER BY symbol LIMIT 301', (pid,))]
        groups.append(dict(id=gid, name=name, pool_id=pid, symbols=symbols))
    unique = {symbol for group in groups for symbol in group['symbols']}
    if len(unique) > 300 or any(not isinstance(s, str) or not re.fullmatch(r'\d{6}', s) for s in unique):
        raise ValueError('member_capacity_or_format_invalid')
    raw = _json(groups)
    stamp = now.isoformat()
    membership_source = 'daily_frozen' if target == now.date().isoformat() else 'backfill_time_members'
    conn.execute('INSERT INTO three_group_daily_runs '
        '(trade_date,frozen_at,groups_json,member_hash,membership_source,status,updated_at) VALUES (?,?,?,?,?,?,?)',
        (target, stamp, raw, hashlib.sha256(raw.encode()).hexdigest(), membership_source, 'FROZEN', stamp))
    conn.commit()  # Membership becomes durable before C or Chan calculation begins.
    return groups


def _ensure_c(path: Path | str, target: str, groups: list[dict]) -> None:
    """Supplement only frozen members; never delete another pool's formal state."""
    monitor = StructureMonitor(path)
    start = time.monotonic()
    with closing(_connect(path)) as conn:
        monitor.ensure_schema(conn)
        conn.commit()
        quality = monitor._quality_statuses(conn, target)
        members = {}
        for group in groups:
            for symbol in group['symbols']:
                member = members.setdefault(symbol, dict(symbol=symbol, name=symbol, pool_ids=[], pool_names=[]))
                member['pool_ids'].append(group['pool_id'])
                member['pool_names'].append(group['name'])
        has_info = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_info'").fetchone()
        for symbol, member in members.items():
            if time.monotonic() - start > 90:
                raise ValueError('c_calculation_budget_exceeded')
            prior = conn.execute('SELECT data_status,current_price FROM structure_monitor_snapshots '
                'WHERE trade_date=? AND symbol=? AND version=? AND config_hash=?',
                (target, symbol, VERSION, monitor.config_hash)).fetchone()
            raw = conn.execute('SELECT substr(trade_date,1,10) AS trade_date,open,high,low,close,volume '
                'FROM stock_daily_prices WHERE symbol=? AND trade_date>=? AND trade_date<? '
                'ORDER BY trade_date LIMIT 1501', (symbol, '2023-01-01', target + 'T')).fetchall()
            input_hash = hashlib.sha256(_json([VERSION,monitor.config_hash,quality,
                [list(row) for row in raw]]).encode()).hexdigest()
            saved_hash = conn.execute('SELECT input_hash FROM three_group_c_inputs WHERE trade_date=? AND symbol=?',
                                      (target,symbol)).fetchone()
            if (not monitor._quality_reason(quality,symbol)) and prior and prior['data_status'] == 'OK' \
                    and saved_hash and saved_hash[0] == input_hash:
                continue
            if has_info:
                info = conn.execute('SELECT name FROM stock_info WHERE symbol=? LIMIT 1', (symbol,)).fetchone()
                member['name'] = info[0] or symbol if info else symbol
            reason = monitor._quality_reason(quality, symbol)
            if not reason and (not raw or raw[-1]['trade_date'] != target):
                reason = 'target_daily_bar_missing'
            if not reason and len(raw) > 1500:
                reason = 'history_bar_limit_exceeded'
            calculated = None
            if not reason:
                try:
                    calculated = evaluate_structure_turning([dict(row) for row in raw], monitor.config)
                    if calculated['summary']['status'] != 'ok':
                        reason = 'insufficient_history'
                except ValueError:
                    reason = 'invalid_ohlcv'
            state = calculated['state'] if not reason else {'status': 'BLOCKED', 'trade_date': target}
            now = _now().isoformat()
            price = float(raw[-1]['close']) if not reason else None
            with conn:
                conn.execute('INSERT OR REPLACE INTO three_group_c_inputs VALUES (?,?,?)',
                             (target,symbol,input_hash))
                conn.execute('INSERT INTO structure_monitor_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) '
                    'ON CONFLICT(trade_date,symbol,version,config_hash) DO UPDATE SET '
                    'name=excluded.name,pool_ids=excluded.pool_ids,pool_names=excluded.pool_names,'
                    'status=excluded.status,data_status=excluded.data_status,reason=excluded.reason,'
                    'current_price=excluded.current_price,state_json=excluded.state_json,updated_at=excluded.updated_at',
                    (target,symbol,VERSION,monitor.config_hash,member['name'],_json(member['pool_ids']),
                     _json(member['pool_names']),state['status'],'BLOCKED' if reason else 'OK', reason or '',
                     price,_json(state),now))
                conn.execute('DELETE FROM structure_monitor_events WHERE trade_date=? AND symbol=? '
                             'AND version=? AND config_hash=?', (target,symbol,VERSION,monitor.config_hash))
                for marker in calculated['markers'] if not reason else []:
                    if marker['trade_date'] != target:
                        continue
                    event_key = [symbol,target,marker['marker_type'],marker.get('setup_id'),
                                 marker.get('trade_id'),VERSION,monitor.config_hash]
                    payload = {**marker,'simulation':True,'execution':'NONE','source':'structure_monitor_daily'}
                    conn.execute('INSERT INTO structure_monitor_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (hashlib.sha256(_json(event_key).encode()).hexdigest(),target,symbol,VERSION,
                         monitor.config_hash,member['name'],_json(member['pool_ids']),_json(member['pool_names']),
                         marker['marker_type'],marker.get('setup_id'),marker.get('trade_id'),_json(payload),now))


def _ensure_chan(path: Path | str, target: str, groups: list[dict], days: list[str]) -> None:
    symbols = sorted({symbol for group in groups for symbol in group['symbols']})
    if not symbols:
        return  # Legal zero-members report has no missing Chan dependency.
    histories = {}
    with closing(_connect(path)) as conn:
        for symbol in symbols:
            rows = conn.execute('SELECT substr(trade_date,1,10) AS trade_date,open,high,low,close '
                'FROM stock_daily_prices WHERE symbol=? AND trade_date<? ORDER BY trade_date DESC LIMIT 400',
                (symbol,target+'T')).fetchall()
            histories[symbol] = [dict(row) for row in reversed(rows)]
    ChanDailySnapshotStore(path).run(target, histories, days)


def prepare(db_path: Path | str, trade_date: str, workflow_child: bool = False) -> dict:
    """Freeze membership first, complete formal facts and archive a rule-only report."""
    target = _day(trade_date)
    try:
        with _lock(db_path, target, workflow_child=workflow_child, writer=True):
            with closing(_connect(db_path)) as conn:
                _schema(conn)
                days = _calendar(conn, target)
                if not days:
                    return dict(status='SKIPPED', reason='not_an_open_calendar_day', trade_date=target)
                _prune(conn,target,_now())
                groups = _freeze(conn, target, _now())
                run = conn.execute('SELECT * FROM three_group_daily_runs WHERE trade_date=?', (target,)).fetchone()
                prior = read_archive(db_path,target)
                if run['status'] == 'READY' and prior and prior.get('valid') and prior['archive_id'] == run['report_id']:
                    return dict(status='READY', reason='reused', report_id=run['report_id'], trade_date=target)
                initial = build_report(db_path,target,frozen_groups=groups,freeze_mrgc=True,compute_chan=False)
                incoming_hash = hashlib.sha256(_json([source_signature(db_path,initial),
                    initial.get('missing'),initial.get('status')]).encode()).hexdigest()
                changed = run['last_input_hash'] != incoming_hash
                if run['attempt_count'] >= 3 and not changed:
                    return dict(status='BLOCKED',reason='prepare_attempt_limit',trade_date=target)
                with conn:
                    conn.execute('UPDATE three_group_daily_runs SET status=?,attempt_count=?,'
                                 'last_input_hash=?,updated_at=? WHERE trade_date=?',
                                 ('PREPARING',1 if changed else run['attempt_count']+1,incoming_hash,_now().isoformat(),target))
            _ensure_c(db_path,target,groups)
            seed = build_report(db_path,target,frozen_groups=groups,freeze_mrgc=True,compute_chan=False)
            input_signature = source_signature(db_path,seed)
            _ensure_chan(db_path,target,groups,days)
            report = build_report(db_path,target,frozen_groups=groups,freeze_mrgc=True)
            if source_signature(db_path,report) != input_signature:
                raise ValueError('source_changed_during_preparation')
            report['source']['publication_mode'] = 'rules'
            report['source']['membership_source'] = run['membership_source']
            report['source']['member_hash'] = run['member_hash']
            report['source']['frozen_at'] = run['frozen_at']
            report['ai'] = dict(status='UNCONFIGURED', model='', summary=[],sector_notes={},stock_notes={},
                                reason='自动日报使用规则事实；在线模型尚未启用')
            expected = {symbol for group in groups for symbol in group['symbols']}
            if {stock.get('symbol') for stock in report.get('stocks', [])} != expected:
                raise ValueError('report_member_coverage_mismatch')
            if any(stock.get('chan', {}).get('status') != 'READY' for stock in report.get('stocks', [])):
                raise ValueError('chan_member_coverage_not_ready')
            if report.get('status') != 'READY' or report.get('missing'):
                raise ValueError('formal_report_not_trusted')
            signature = source_signature(db_path,report)
            envelope = write_archive(db_path,report,signature)
            body = render_email(report)
            if len(body.encode()) > 2 * 1024 * 1024 or len(_json(report).encode()) > 2 * 1024 * 1024:
                raise ValueError('report_payload_budget_exceeded')
            with closing(_connect(db_path)) as conn, conn:
                stored_bytes = conn.execute('SELECT COALESCE(SUM(length(CAST(report_json AS BLOB))+'
                    'length(CAST(email_html AS BLOB))),0) FROM three_group_daily_runs WHERE trade_date<>?',(target,)).fetchone()[0]
                if stored_bytes + len(body.encode()) + len(_json(report).encode()) > 50*1024*1024:
                    raise ValueError('frozen_ledger_storage_budget_exceeded')
                conn.execute('UPDATE three_group_daily_runs SET status=?,report_id=?,signature=?,report_json=?,'
                             'email_html=?,reason=?,updated_at=? WHERE trade_date=?',
                             ('READY',envelope['archive_id'],signature,_json(report),body,'',_now().isoformat(),target))
            return dict(status='READY',report_id=envelope['archive_id'],trade_date=target,reason='')
    except RuntimeError as exc:
        return dict(status='BLOCKED',reason=str(exc)[:160],trade_date=target)
    except (ValueError,sqlite3.Error,OSError) as exc:
        reason = str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
        try:
            with closing(_connect(db_path)) as conn, conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='three_group_daily_runs'").fetchone():
                    conn.execute('UPDATE three_group_daily_runs SET status=?,reason=?,updated_at=? WHERE trade_date=?',
                                 ('BLOCKED',reason[:160],_now().isoformat(),target))
        except (sqlite3.Error,OSError):
            reason = reason[:120] + ':failed_to_persist_blocked_status'
        return dict(status='BLOCKED',reason=reason[:160],trade_date=target)


def _recipient_id() -> str:
    recipients = sorted({value.strip().lower() for value in settings.SMTP_RECIPIENTS.split(',') if value.strip()})
    return hashlib.sha256(_json(recipients).encode()).hexdigest()[:16]


def _delivery_update(path: Path | str, delivery_id: str, status: str, reason: str,
                     now: datetime, *, sent: bool = False) -> None:
    with closing(_connect(path)) as conn, conn:
        conn.execute('UPDATE notification_deliveries SET status=?,last_error=?,updated_at=?,sent_at=? '
                     'WHERE delivery_id=?', (status,reason,now.isoformat(),now.isoformat() if sent else None,delivery_id))


def deliver(db_path: Path | str, trade_date: str, send: bool = False, now: datetime | None = None,
            sender=None, *, retry_only: bool = False) -> dict:
    """Deliver exact-day frozen content once, preserving uncertainty after interruption."""
    target = _day(trade_date)
    now = now.astimezone(ZoneInfo('Asia/Shanghai')) if now and now.tzinfo else now.replace(tzinfo=ZoneInfo('Asia/Shanghai')) if now else _now()
    try:
        with _lock(db_path,target):
            with closing(_connect(db_path)) as conn:
                _schema(conn)
                if target != now.date().isoformat() or not _calendar(conn,target) or (now.hour,now.minute) < (15,30):
                    return dict(status='SKIPPED',reason='not_current_post_close_trading_day',trade_date=target)
                run = conn.execute('SELECT * FROM three_group_daily_runs WHERE trade_date=?',(target,)).fetchone()
                if not run or run['status'] != 'READY':
                    return dict(status='BLOCKED',reason='frozen_report_not_ready',trade_date=target)
                report = json.loads(run['report_json'])
                if report.get('date') != target or report.get('status') != 'READY' or report.get('missing'):
                    return dict(status='BLOCKED',reason='untrusted_frozen_report',trade_date=target)
                current_valid = source_signature(db_path,report) == run['signature']
                key = f'three_group_daily:{target}:{_recipient_id()}'
                delivery = conn.execute('SELECT * FROM notification_deliveries WHERE dedupe_key=?',(key,)).fetchone()
                if delivery and delivery['status'] in ('SENDING','UNKNOWN'):
                    with conn:
                        conn.execute('UPDATE notification_deliveries SET status=?,last_error=?,updated_at=? WHERE delivery_id=?',
                            ('UNKNOWN','SMTP投递结果待人工核对',now.isoformat(),delivery['delivery_id']))
                    return dict(status='UNKNOWN',reason='delivery_requires_reconciliation',trade_date=target)
                if delivery and delivery['status'] in ('SENT','SKIPPED'):
                    return dict(status='SKIPPED',reason='already_delivered_or_invalidated',trade_date=target)
                if retry_only and (not delivery or delivery['status'] != 'FAILED'):
                    return dict(status='SKIPPED',reason='no_explicit_failed_delivery',trade_date=target)
                content = conn.execute('SELECT * FROM three_group_daily_delivery_content WHERE delivery_id=?',
                                       (delivery['delivery_id'],)).fetchone() if delivery else None
                if content:
                    mailed_report = json.loads(content['report_json'])
                    content_valid = (mailed_report.get('date') == target
                        and mailed_report.get('status') == 'READY' and not mailed_report.get('missing')
                        and source_signature(db_path,mailed_report) == content['signature'])
                    if not content_valid:
                        with conn:
                            conn.execute('UPDATE notification_deliveries SET status=?,last_error=?,updated_at=? '
                                'WHERE delivery_id=?',('SKIPPED','已冻结邮件的源数据失效',now.isoformat(),delivery['delivery_id']))
                        return dict(status='SKIPPED',reason='frozen_delivery_source_changed',trade_date=target)
                elif not current_valid:
                    return dict(status='BLOCKED',reason='frozen_source_changed',trade_date=target)
                if delivery and delivery['attempt_count'] >= 3:
                    return dict(status='SKIPPED',reason='delivery_attempt_limit',trade_date=target)
                if delivery and delivery['next_retry_at'] and now.isoformat() < delivery['next_retry_at']:
                    return dict(status='SKIPPED',reason='retry_backoff',trade_date=target)
                if not send:
                    return dict(status='READY',reason='dry_run_no_send',trade_date=target,report_id=run['report_id'])
                stamp = now.isoformat()
                if not delivery:
                    with conn:
                        conn.execute('INSERT INTO notification_deliveries '
                            '(delivery_id,event_id,channel,delivery_type,dedupe_key,status,created_at,updated_at) '
                            "VALUES (?,NULL,'email','three_group_daily',?,'PENDING',?,?)",(uuid4().hex,key,stamp,stamp))
                    delivery = conn.execute('SELECT * FROM notification_deliveries WHERE dedupe_key=?',(key,)).fetchone()
                delivery_id = delivery['delivery_id']
                if not content:
                    used = conn.execute('SELECT COALESCE(SUM(length(CAST(report_json AS BLOB))+'
                        'length(CAST(email_html AS BLOB))),0) FROM three_group_daily_delivery_content').fetchone()[0]
                    if used + len(run['report_json'].encode()) + len(run['email_html'].encode()) > 50*1024*1024:
                        raise ValueError('delivery_content_storage_budget_exceeded')
                    with conn:
                        conn.execute('INSERT INTO three_group_daily_delivery_content VALUES (?,?,?,?,?,?)',
                            (delivery_id,target,run['report_id'],run['signature'],run['report_json'],run['email_html']))
                    content = conn.execute('SELECT * FROM three_group_daily_delivery_content WHERE delivery_id=?',
                                           (delivery_id,)).fetchone()
                attempts = delivery['attempt_count'] + 1
                with conn:
                    conn.execute('UPDATE notification_deliveries SET status=?,attempt_count=?,next_retry_at=?,updated_at=? '
                        'WHERE delivery_id=?',('SENDING',attempts,(now+timedelta(minutes=5*2**(attempts-1))).isoformat(),stamp,delivery_id))
                body = content['email_html']
            # No database connection/transaction remains open across SMTP I/O.
            try:
                result = (sender or send_notification)(f'三分组日报 · {target} · 规则版',body,is_html=True)
            except Exception:
                _delivery_update(db_path,delivery_id,'UNKNOWN','SMTP调用中断，结果待人工核对',now)
                return dict(status='UNKNOWN',reason='sender_outcome_unknown',trade_date=target)
            if not isinstance(result,EmailDeliveryResult):
                _delivery_update(db_path,delivery_id,'UNKNOWN','SMTP返回结果无效，待人工核对',now)
                return dict(status='UNKNOWN',reason='invalid_sender_outcome',trade_date=target)
            if result.success:
                _delivery_update(db_path,delivery_id,'SENT','',now,sent=True)
                return dict(status='SENT',trade_date=target,report_id=content['report_id'])
            if result.skipped:
                _delivery_update(db_path,delivery_id,'PENDING','邮件未启用或配置不完整',now)
                with closing(_connect(db_path)) as conn, conn:
                    conn.execute('UPDATE notification_deliveries SET attempt_count=?,next_retry_at=NULL WHERE delivery_id=?',
                                 (delivery['attempt_count'],delivery_id))
                return dict(status='SKIPPED',reason='email_not_configured',trade_date=target)
            if result.error not in {'SMTP explicit refusal','smtp_refused','smtp_auth_refused',
                                     'smtp_recipient_refused','smtp_data_refused'}:
                _delivery_update(db_path,delivery_id,'UNKNOWN','SMTP结果不确定，待人工核对',now)
                return dict(status='UNKNOWN',reason='smtp_generic_failure_requires_reconciliation',trade_date=target)
            _delivery_update(db_path,delivery_id,'FAILED','邮件投递明确失败',now)
            return dict(status='FAILED',reason='email_delivery_failed',trade_date=target)
    except (ValueError,RuntimeError,sqlite3.Error,OSError) as exc:
        return dict(status='BLOCKED',reason=type(exc).__name__,trade_date=target)


def retry(db_path: Path | str, trade_date: str, send: bool = False,
          now: datetime | None = None, sender=None) -> dict:
    return deliver(db_path,trade_date,send,now,sender,retry_only=True)


def main() -> int:
    parser = argparse.ArgumentParser(description='规则版三分组交易日日报，默认不发送邮件')
    parser.add_argument('--trade-date',default=None)
    parser.add_argument('--mode',choices=('prepare','deliver','retry'),required=True)
    parser.add_argument('--db','--db-path',dest='db',default=str(DB_PATH))
    parser.add_argument('--send',action='store_true')
    parser.add_argument('--workflow-child',action='store_true')
    args = parser.parse_args()
    target = args.trade_date or _now().date().isoformat()
    try:
        outcome = prepare(args.db,target,args.workflow_child) if args.mode == 'prepare' else \
            (retry if args.mode == 'retry' else deliver)(args.db,target,send=args.send)
    except ValueError:
        outcome = dict(status='BLOCKED',reason='invalid_trade_date')
    print(_json(outcome))
    return 0 if outcome['status'] in ('READY','SENT','SKIPPED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
