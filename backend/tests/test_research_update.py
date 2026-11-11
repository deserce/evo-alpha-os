"""Network-free incremental research update regressions."""
from contextlib import closing
from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo
import pytest
from research.update_reports import run_update, EastmoneyClient, SyncError
from research.update_lock import writer_lock


def payload(day='2026-10-23', title='标题'):
    return {'infoCode': 'AP202610230000001', 'title': title, 'stockCode': '000001',
            'stockName': '平安银行', 'publishDate': day, 'orgSName': '机构'}


class Client:
    def __init__(self, fail_page=False, cap=False, title='标题'):
        self.calls = []
        self.fail_page = fail_page
        self.cap = cap
        self.title = title

    def fetch(self, qtype, start, end, page, deadline):
        self.calls.append((qtype, start, end, page))
        if self.fail_page and page == 2:
            raise SyncError('timeout')
        if self.cap and start != end:
            return {'TotalPage': 101, 'data': [payload(end)]}
        if self.fail_page:
            return {'TotalPage': 2, 'data': [payload(end)]}
        return {'TotalPage': 1 if qtype == 0 else 0,
                'data': [payload(end, self.title)] if qtype == 0 else []}


def read(path, sql):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def test_normal_duplicate_update_and_late_lookback(tmp_path):
    path = tmp_path / 'research.db'
    first = run_update(path, '2026-10-23', client=Client())
    assert first['status'] == 'SUCCESS'
    assert first['inserted'] == 1
    second_client = Client(title='修正标题')
    second = run_update(path, '2026-10-24', client=second_client)
    assert second['updated'] == 1
    assert second_client.calls[0][1] == '2026-10-21'
    assert read(path, 'select count(*), title from reports') == [(1, '修正标题')]


def test_page_failure_does_not_advance_cursor(tmp_path):
    path = tmp_path / 'research.db'
    result = run_update(path, '2026-10-23', client=Client(fail_page=True))
    assert result['status'] == 'FAILED'
    assert read(path, 'select cursor_date from sync_state') == []
    assert read(path, 'select status from sync_runs') == [('FAILED',)]


def test_over_limit_splits_window_and_long_outage_is_contiguous(tmp_path):
    path = tmp_path / 'research.db'
    run_update(path, '2026-10-01', client=Client())
    client = Client(cap=True)
    result = run_update(path, '2026-10-23', client=client)
    assert result['status'] == 'SUCCESS'
    assert result['cursor_date'] == '2026-10-23'
    singles = {start for q, start, end, page in client.calls if start == end and q == 0}
    assert '2026-10-02' in singles and '2026-10-22' in singles


def test_schedule_gate_and_once_per_successful_day(tmp_path):
    path = tmp_path / 'research.db'
    tz = ZoneInfo('Asia/Shanghai')
    for now in [datetime(2026,10,26,8,tzinfo=tz), datetime(2026,10,25,8,tzinfo=tz)]:
        assert run_update(path, scheduled=True, now=now, client=Client())['status'] == 'SKIPPED'
    assert not path.exists()
    now = datetime(2026,10,25,10,tzinfo=tz)
    assert run_update(path, scheduled=True, now=now, client=Client())['status'] == 'SUCCESS'
    client = Client()
    assert run_update(path, scheduled=True, now=now, client=client)['status'] == 'SKIPPED'
    assert not client.calls


def test_lock_competition(tmp_path):
    path = tmp_path / 'research.db'
    with writer_lock(path):
        assert run_update(path, '2026-10-23', client=Client())['status'] == 'LOCKED'
    assert not path.exists()


def test_invalid_dates_and_empty_data_schema(tmp_path):
    with pytest.raises(ValueError):
        run_update(tmp_path/'x.db', 'bad', client=Client())
    class Invalid(Client):
        def fetch(self, *args):
            return {'unexpected': []}
    assert run_update(tmp_path/'x.db', '2026-10-23', client=Invalid())['status'] == 'FAILED'


def test_partial_backfill_does_not_claim_whole_run_success(tmp_path):
    path = tmp_path / 'research.db'
    run_update(path, '2026-10-01', client=Client())
    old_success = read(path, 'select last_success from sync_state')[0][0]
    class FailsLater(Client):
        def fetch(self, qtype, start, end, page, deadline):
            if start > '2026-10-08':
                raise SyncError('timeout')
            return super().fetch(qtype, start, end, page, deadline)
    result = run_update(path, '2026-10-23', client=FailsLater())
    assert result['status'] == 'FAILED'
    assert result['cursor_date'] == '2026-10-08'
    assert read(path, 'select last_success from sync_state')[0][0] == old_success
    retry = Client()
    run_update(path, '2026-10-23', client=retry)
    assert retry.calls[0][1] == '2026-10-09'


def test_single_day_page_cap_is_explicit_and_cursor_not_advanced(tmp_path):
    class AlwaysOver(Client):
        def fetch(self, *args):
            return {'TotalPage': 101, 'data': [payload()]}
    path = tmp_path/'research.db'
    result = run_update(path, '2026-10-23', client=AlwaysOver())
    assert result['status'] == 'FAILED'
    assert result['error'] == 'single_day_page_limit:increase_max_pages'
    assert read(path, 'select * from sync_state') == []


def test_runtime_budget_fails_before_request(tmp_path, monkeypatch):
    import research.update_reports as updater
    clock = iter([0, 601])
    monkeypatch.setattr(updater.time, 'monotonic', lambda: next(clock))
    client = Client()
    result = run_update(tmp_path/'r.db', '2026-10-23', client=client)
    assert result['error'] == 'runtime_budget_exceeded'
    assert not client.calls


class Response:
    def __init__(self, chunks):
        self.chunks = chunks
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def raise_for_status(self):
        pass
    def iter_content(self, chunk_size):
        yield from self.chunks


class Session:
    def __init__(self, response=None, exception=None):
        self.trust_env = True
        self.response = response
        self.exception = exception
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exception:
            raise self.exception
        return self.response
    def close(self):
        pass


def test_transport_timeout_retries_twice_direct_tls(monkeypatch):
    import research.update_reports as updater
    import requests
    session = Session(exception=requests.Timeout('offline'))
    monkeypatch.setattr(updater.requests, 'Session', lambda: session)
    monkeypatch.setattr(updater.time, 'sleep', lambda seconds: None)
    client = EastmoneyClient()
    with pytest.raises(SyncError, match='source_request_failed:Timeout'):
        client.fetch(0, '2026-10-21', '2026-10-23', 1, updater.time.monotonic()+60)
    assert session.trust_env is False
    assert len(session.calls) == 2
    assert session.calls[0][1]['timeout'] == (5, 15)
    assert 'verify' not in session.calls[0][1]
    assert session.calls[0][1]['stream'] is True


def test_transport_response_size_limit(monkeypatch):
    import research.update_reports as updater
    session = Session(response=Response([b'x' * (4*1024*1024+1)]))
    monkeypatch.setattr(updater.requests, 'Session', lambda: session)
    with pytest.raises(SyncError, match='response_size_limit'):
        EastmoneyClient().fetch(0, '2026-10-21', '2026-10-23', 1,
                               updater.time.monotonic()+60)
    assert len(session.calls) == 1


def test_industry_source_mapping_without_industry_label(tmp_path):
    class Industry(Client):
        def fetch(self, qtype, start, end, page, deadline):
            record = payload(end)
            record.update(stockCode='', stockName='')
            return {'TotalPage': 1 if qtype == 1 else 0,
                    'data': [record] if qtype == 1 else []}
    path = tmp_path/'r.db'
    assert run_update(path, '2026-10-23', client=Industry())['status'] == 'SUCCESS'
    assert read(path, 'select report_type,symbol,raw_report_type from reports') == [
        ('industry', '', 'qType=1:行业研报')]


def test_stale_running_run_recovered_under_lock(tmp_path):
    path = tmp_path/'r.db'
    run_update(path, '2026-10-23', client=Client())
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("update sync_runs set status='RUNNING',finished_at=NULL")
        conn.commit()
    run_update(path, '2026-10-24', client=Client())
    assert read(path, "select status,error from sync_runs where target_date='2026-10-23'") == [
        ('FAILED', 'interrupted_previous_run')]


def test_transport_nonretryable_http(monkeypatch):
    import research.update_reports as updater
    import requests
    response = requests.Response()
    response.status_code = 403
    session = Session(exception=requests.HTTPError(response=response))
    monkeypatch.setattr(updater.requests, 'Session', lambda: session)
    with pytest.raises(SyncError, match='source_http_error:403'):
        EastmoneyClient().fetch(0, '2026-10-21', '2026-10-23', 1,
                               updater.time.monotonic()+60)
    assert len(session.calls) == 1


def test_cli_sigterm_records_failure(tmp_path, monkeypatch):
    import signal
    import research.update_reports as updater
    class Interrupted(Client):
        def fetch(self, *args):
            signal.raise_signal(signal.SIGTERM)
        def close(self):
            pass
    path = tmp_path/'r.db'
    monkeypatch.setattr(updater, 'EastmoneyClient', Interrupted)
    monkeypatch.setattr('sys.argv', ['update_reports', '--target', str(path),
                                    '--as-of-date', '2026-10-23'])
    with pytest.raises(SystemExit) as error:
        updater.main()
    assert error.value.code == 1
    assert read(path, 'select status,error from sync_runs') == [
        ('FAILED', 'runtime_budget_exceeded_or_terminated')]


def test_audit_retention_is_bounded(tmp_path):
    path = tmp_path/'r.db'
    run_update(path, '2026-10-23', client=Client())
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany("INSERT INTO sync_runs(id,status,started_at,target_date) "
                         "VALUES (?,'FAILED','2020-01-01','2020-01-01')",
                         [(f'old-{i}',) for i in range(400)])
        conn.commit()
    run_update(path, '2026-10-24', client=Client())
    assert read(path, 'select count(*) from sync_runs') == [(365,)]


def test_first_failed_import_does_not_bootstrap_from_partial_rows(tmp_path):
    path = tmp_path/'r.db'
    first = run_update(path, '2026-10-23', client=Client(fail_page=True))
    assert first['status'] == 'FAILED'
    assert read(path, 'select count(*) from reports') == [(1,)]
    retry = Client()
    assert run_update(path, '2026-10-23', client=retry)['status'] == 'SUCCESS'
    assert retry.calls[0][1] == '2026-10-17'
