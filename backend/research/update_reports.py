"""Bounded Eastmoney metadata sync; never downloads PDF or invokes AI/Data Core."""
import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import signal
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests

from research.import_reports import initialize, normalize
from research.repository import database_path, validate_date
from research.update_lock import UpdateLocked, writer_lock


class SyncError(RuntimeError):
    """Incomplete or invalid source response; cursor must not advance."""


class PageLimit(SyncError):
    """Window must be reduced to stay within the pagination budget."""


class EastmoneyClient:
    """Direct TLS requests with bounded response size, retries and spacing."""

    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.last_request = 0.0

    def close(self):
        self.session.close()

    def fetch(self, qtype, start, end, page, deadline):
        params = {'qType': qtype, 'pageNo': page, 'pageSize': 100,
                  'beginTime': start, 'endTime': end, 'industryCode': '*',
                  'industry': '*', 'code': '*', 'orgCode': '', 'rating': ''}
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                raise SyncError('runtime_budget_exceeded')
            time.sleep(min(max(0, .4 - (time.monotonic() - self.last_request)), remaining))
            self.last_request = time.monotonic()
            remaining = deadline - self.last_request
            try:
                with self.session.get(
                    'https://reportapi.eastmoney.com/report/list', params=params,
                    headers={'Referer': 'https://data.eastmoney.com/report/',
                             'User-Agent': 'Mozilla/5.0'}, stream=True,
                    timeout=(min(5, remaining / 2), min(15, remaining / 2)),
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        if time.monotonic() >= deadline:
                            raise SyncError('runtime_budget_exceeded')
                        body.extend(chunk)
                        if len(body) > 4 * 1024 * 1024:
                            raise SyncError('response_size_limit')
                    return json.loads(body)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if attempt == 1 or (status != 429 and status < 500):
                    raise SyncError(f'source_http_error:{status}') from exc
            except (requests.RequestException, ValueError) as exc:
                if attempt == 1:
                    raise SyncError(f'source_request_failed:{type(exc).__name__}') from exc
        raise SyncError('source_request_failed')


def initialize_sync(conn):
    initialize(conn)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS sync_state (
            source TEXT PRIMARY KEY, cursor_date TEXT NOT NULL,
            last_success TEXT, last_scheduled_date TEXT);
        CREATE TABLE IF NOT EXISTS sync_runs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, target_date TEXT NOT NULL, window_start TEXT,
            window_end TEXT, scanned INTEGER NOT NULL DEFAULT 0,
            inserted INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS ix_sync_runs_started ON sync_runs(started_at DESC);
    ''')


def normalize_api(item, qtype, run_id, timestamp):
    if not isinstance(item, dict):
        raise SyncError('invalid_report_record')
    record = normalize({
        'info_code': item.get('infoCode'), 'title': item.get('title'),
        'publish_date': item.get('publishDate'), 'stock_code': item.get('stockCode'),
        'stock_name': item.get('stockName') or item.get('secuFullName'),
        'industry_name': item.get('industryName') or item.get('indvInduName'),
        'org_name': item.get('orgSName') or item.get('orgName'),
        'rating': item.get('emRatingName') or item.get('sRatingName'),
        'researcher': item.get('researcher'), 'pages': item.get('attachPages'),
        # normalize() retains compatibility with the reversed legacy source labels.
        'report_type': '行业研报' if qtype == 0 else '个股研报',
        'pdf_url': f"https://pdf.dfcfw.com/pdf/H3_{item.get('infoCode', '')}_1.pdf",
    }, 'sync:' + run_id, timestamp)
    if record is None:
        raise SyncError('invalid_report_identity_or_date')
    record['raw_report_type'] = 'qType=0:公司研报' if qtype == 0 else 'qType=1:行业研报'
    if qtype == 1 and not item.get('stockCode'):
        record.update(report_type='industry', classification_reason='api_qtype_1',
                      original_url='https://data.eastmoney.com/report/zw_industry.jshtml?infocode='
                                   + record['source_report_id'])
    elif qtype == 1:
        record.update(report_type='unknown', symbol='', original_url='',
                      classification_reason='api_qtype_identity_conflict')
    return record


def fetch_window(conn, client, start, end, deadline, max_pages, result, run_id):
    for qtype in (0, 1):
        page, total_pages = 1, None
        seen = set()
        while True:
            if time.monotonic() >= deadline:
                raise SyncError('runtime_budget_exceeded')
            response = client.fetch(qtype, start.isoformat(), end.isoformat(), page, deadline)
            if (not isinstance(response, dict) or not isinstance(response.get('data'), list)
                    or type(response.get('TotalPage')) not in (int, str)):
                raise SyncError('invalid_response_schema')
            try:
                count = int(response['TotalPage'])
            except ValueError as exc:
                raise SyncError('invalid_total_pages') from exc
            rows = response['data']
            if count < 0 or len(rows) > 100 or (count == 0 and rows):
                raise SyncError('invalid_pagination')
            if count > max_pages:
                raise PageLimit('page_limit')
            if total_pages is not None and count != total_pages:
                raise SyncError('pagination_changed_during_fetch')
            total_pages = count
            if count > 0 and not rows:
                # A genuinely empty source is represented by TotalPage=0.
                raise SyncError('unexpected_empty_page')
            if count == 0:
                break
            records = [normalize_api(row, qtype, run_id, datetime.now(timezone.utc).isoformat())
                       for row in rows]
            for record in records:
                if not start.isoformat() <= record['publish_date'] <= end.isoformat():
                    raise SyncError('source_date_outside_window')
                if record['id'] in seen:
                    raise SyncError('duplicate_page_record')
                seen.add(record['id'])
            with conn:
                for record in records:
                    old = conn.execute('SELECT * FROM reports WHERE id=?',
                                       (record['id'],)).fetchone()
                    excluded = {'id', 'source', 'source_report_id', 'batch_id', 'imported_at'}
                    fields = [key for key in record if key not in excluded]
                    if old and all(old[field] == record[field] for field in fields):
                        continue
                    if old:
                        conn.execute('UPDATE reports SET ' + ','.join(f'{f}=?' for f in fields)
                                     + ' WHERE id=?', [record[f] for f in fields]+[record['id']])
                        result['updated'] += 1
                    else:
                        conn.execute('INSERT INTO reports (' + ','.join(record) + ') VALUES ('
                                     + ','.join('?' for _ in record) + ')', list(record.values()))
                        result['inserted'] += 1
                result['scanned'] += len(records)
                conn.execute('UPDATE sync_runs SET scanned=?,inserted=?,updated=? WHERE id=?',
                             (result['scanned'], result['inserted'], result['updated'], run_id))
            if page >= total_pages:
                break
            page += 1


def run_update(target=None, as_of_date=None, scheduled=False, now=None, client=None,
               max_pages=100, runtime_seconds=600):
    """Advance only complete two-type windows, replaying two days for late arrivals."""
    if not 1 <= max_pages <= 1000 or not 1 <= runtime_seconds <= 600:
        raise ValueError('invalid resource budget')
    now = (now or datetime.now(ZoneInfo('Asia/Shanghai'))).astimezone(ZoneInfo('Asia/Shanghai'))
    target_day = date.fromisoformat(validate_date(as_of_date)) if as_of_date else now.date()
    if scheduled and now.hour < 9:
        return {'status': 'SKIPPED', 'reason': 'outside_schedule'}
    path = Path(target) if target is not None else database_path()
    result = {'status': 'RUNNING', 'run_id': uuid4().hex, 'scanned': 0,
              'inserted': 0, 'updated': 0, 'cursor_date': None, 'error': ''}
    deadline = time.monotonic() + runtime_seconds
    try:
        with writer_lock(path), closing(sqlite3.connect(path, timeout=3)) as conn:
            conn.row_factory = sqlite3.Row
            initialize_sync(conn)
            with conn:
                conn.execute("UPDATE sync_runs SET status='FAILED',finished_at=?,"
                             "error='interrupted_previous_run' WHERE status='RUNNING'",
                             (datetime.now(timezone.utc).isoformat(),))
                conn.execute('DELETE FROM sync_runs WHERE id IN ('
                             'SELECT id FROM sync_runs ORDER BY started_at DESC, rowid DESC '
                             'LIMIT 1000 OFFSET 364)')
            state = conn.execute("SELECT * FROM sync_state WHERE source='eastmoney'").fetchone()
            if scheduled and state and state['last_scheduled_date'] == now.date().isoformat():
                return {'status': 'SKIPPED', 'reason': 'already_successful_today'}
            cursor = date.fromisoformat(state['cursor_date']) if state else None
            if cursor is None:
                # Existing imported catalogue is the initial baseline, not a new full crawl.
                latest = conn.execute('SELECT max(publish_date) FROM reports').fetchone()[0]
                cursor = date.fromisoformat(latest) if latest else target_day - timedelta(days=6)
            incomplete_start = None
            if not state:
                incomplete_start = conn.execute(
                    "SELECT min(window_start) FROM sync_runs WHERE status='FAILED'"
                ).fetchone()[0]
                if incomplete_start:
                    # Partial upserts must never become a newer bootstrap baseline.
                    cursor = date.fromisoformat(incomplete_start) - timedelta(days=1)
            result['cursor_date'] = state['cursor_date'] if state else None
            start = cursor + timedelta(days=1) if (target_day-cursor).days > 7 else min(
                cursor-timedelta(days=2), target_day-timedelta(days=2))
            # Empty catalogues intentionally bootstrap only the last seven days.
            if not state and not conn.execute('SELECT 1 FROM reports LIMIT 1').fetchone():
                start = target_day - timedelta(days=6)
            if incomplete_start:
                start = date.fromisoformat(incomplete_start)
            with conn:
                conn.execute('INSERT INTO sync_runs(id,status,started_at,target_date) VALUES '
                             '(?,?,?,?)', (result['run_id'], 'RUNNING', now.isoformat(),
                                          target_day.isoformat()))
            owned_client = client is None
            client = client or EastmoneyClient()
            try:
                while start <= target_day:
                    end = min(start+timedelta(days=6), target_day)
                    while True:
                        with conn:
                            conn.execute('UPDATE sync_runs SET window_start=?,window_end=? '
                                         'WHERE id=?', (start.isoformat(), end.isoformat(),
                                                       result['run_id']))
                        try:
                            fetch_window(conn, client, start, end, deadline, max_pages,
                                         result, result['run_id'])
                            break
                        except PageLimit:
                            if start == end:
                                raise SyncError('single_day_page_limit:increase_max_pages')
                            end = start + timedelta(days=(end-start).days//2)
                    advanced = max(cursor, end)
                    with conn:
                        conn.execute('INSERT INTO sync_state(source,cursor_date) '
                                     "VALUES ('eastmoney',?) ON CONFLICT(source) DO UPDATE SET "
                                     'cursor_date=excluded.cursor_date',
                                     (advanced.isoformat(),))
                    cursor = advanced
                    result['cursor_date'] = cursor.isoformat()
                    start = end + timedelta(days=1)
                with conn:
                    conn.execute("UPDATE sync_state SET last_success=? WHERE source='eastmoney'",
                                 (datetime.now(timezone.utc).isoformat(),))
                    if scheduled:
                        conn.execute("UPDATE sync_state SET last_scheduled_date=? "
                                     "WHERE source='eastmoney'", (now.date().isoformat(),))
                result['status'] = 'SUCCESS'
            except (SyncError, sqlite3.Error, OSError) as exc:
                error = str(exc)[:300] if isinstance(exc, SyncError) else type(exc).__name__
                result.update(status='FAILED', error=error)
            finally:
                if owned_client:
                    client.close()
                with conn:
                    conn.execute('UPDATE sync_runs SET status=?,finished_at=?,error=? WHERE id=?',
                                 (result['status'], datetime.now(timezone.utc).isoformat(),
                                  result['error'], result['run_id']))
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except UpdateLocked:
        result.update(status='LOCKED', error='research writer already active')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target')
    parser.add_argument('--as-of-date')
    parser.add_argument('--scheduled', action='store_true')
    parser.add_argument('--max-pages', type=int, default=100)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise SyncError('runtime_budget_exceeded_or_terminated')

    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run_update(args.target, args.as_of_date, args.scheduled, max_pages=args.max_pages)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    print(json.dumps(result, ensure_ascii=False))
    if result['status'] == 'FAILED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
