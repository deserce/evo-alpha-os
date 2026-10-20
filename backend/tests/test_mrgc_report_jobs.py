"""Date archive jobs must coalesce work and reuse completed facts."""
from unittest.mock import Mock
import pytest
from quant_engine.watch import mrgc_report_jobs as jobs


@pytest.fixture(autouse=True)
def isolated_worker_log(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, '_log_path', lambda: tmp_path / 'worker.log')


def test_completed_archive_returns_without_start_or_build(tmp_path, monkeypatch):
    report = {'date': '2026-09-29', 'status': 'READY', 'ai': {'status': 'READY'}}
    monkeypatch.setattr(jobs, 'read_archive', lambda *a, **k: {
        'report': report, 'valid': True, 'generated_at': 'now', 'archive_id': 'x'})
    monkeypatch.setattr(jobs, 'render_email', lambda r: 'preview')
    launch = Mock(side_effect=AssertionError('must reuse archive'))
    monkeypatch.setattr(jobs.subprocess, 'Popen', launch)
    result = jobs.start_generation(tmp_path / 'db', '2026-09-29')
    assert result['report'] == report
    assert result['job']['status'] == 'COMPLETED'
    launch.assert_not_called()


def test_same_date_jobs_share_single_process(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'read_archive', lambda *a, **k: None)
    monkeypatch.setattr(jobs, 'read_partial', lambda *a, **k: None)
    processes = []
    def launch(*args, **kwargs):
        assert kwargs['start_new_session'] is True
        import os
        processes.append(os.dup(kwargs['pass_fds'][0]))
        return Mock(pid=123, wait=Mock(return_value=0))
    monkeypatch.setattr(jobs.subprocess, 'Popen', launch)
    first = jobs.start_generation(tmp_path / 'db', '2026-09-29')
    second = jobs.start_generation(tmp_path / 'db', '2026-09-29')
    assert first['job']['status'] == second['job']['status'] == 'RUNNING'
    assert len(processes) == 1
    import os
    os.close(processes[0])


def test_other_date_busy_and_abandoned_job_is_recoverable(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'read_archive', lambda *a, **k: None)
    monkeypatch.setattr(jobs, 'read_partial', lambda *a, **k: None)
    import os
    held = []
    monkeypatch.setattr(jobs.subprocess, 'Popen', lambda *a, **k:
        held.append(os.dup(k['pass_fds'][0])) or Mock(pid=123, wait=Mock(return_value=0)))
    jobs.start_generation(tmp_path / 'db', '2026-09-29')
    assert jobs.start_generation(tmp_path / 'db', '2026-09-30')['job']['status'] == 'BUSY'
    os.close(held[0])
    assert jobs.get_report(tmp_path / 'db', '2026-09-29')['job']['status'] == 'FAILED'


def test_invalid_dates_do_not_spawn(tmp_path):
    for value in ['../../escape', '2026-99-01', '2026-1-01']:
        with pytest.raises(ValueError):
            jobs.start_generation(tmp_path / 'db', value)


def _source_db(tmp_path):
    import sqlite3
    from contextlib import closing
    from tests.test_structure_monitor import make_db, _buy_day
    path, prices = make_db(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript('''
            CREATE TABLE strategy_run_log(strategy_name TEXT,trade_date TEXT,status TEXT);
            CREATE TABLE quant_preselect_results(strategy_name TEXT,trade_date TEXT,symbol TEXT);
            INSERT INTO stock_pools VALUES ('watchlist','自选股',1),('pool_0d1aea597dc4','公司',1);
            INSERT INTO stock_pool_members VALUES ('watchlist','600641');
        ''')
        conn.executemany('INSERT INTO strategy_run_log VALUES (?,?,?)',
                         [('mrgc_v1', bar['trade_date'], 'success') for bar in prices])
        conn.execute('INSERT INTO quant_preselect_results VALUES (?,?,?)', ('mrgc_v1',target,'600641'))
    return path, target


def test_strategy_archive_is_complete_and_second_read_never_replays(tmp_path, monkeypatch):
    from quant_engine.watch import mrgc_report_ai as ai
    path, target = _source_db(tmp_path)
    before = path.read_bytes()
    provider = Mock(side_effect=AssertionError('user deferred AI'))
    monkeypatch.setattr(ai, '_provider', provider)
    jobs.run_generation(path, target)
    result = jobs.get_report(path, target)
    assert result['job']['status'] == 'COMPLETED'
    assert result['archive']['complete'] and result['archive']['valid']
    assert result['report']['ai']['status'] == 'DEFERRED'
    assert result['report']['counts']['blocked'] == 0
    assert '_history_results' not in result['report']
    monkeypatch.setattr(jobs, 'calculate_history', Mock(side_effect=AssertionError('no replay')))
    monkeypatch.setattr(jobs, 'build_report', Mock(side_effect=AssertionError('no rebuild')))
    monkeypatch.setattr(jobs.subprocess, 'Popen', Mock(side_effect=AssertionError('no worker')))
    assert jobs.start_generation(path, target)['job']['status'] == 'COMPLETED'
    assert path.read_bytes() == before
    provider.assert_not_called()


def test_progressive_retry_only_calculates_missing_members(tmp_path, monkeypatch):
    path, target = _source_db(tmp_path)
    actual = jobs.calculate_history
    def insufficient(db, date, symbols, **kwargs):
        snapshots, events = actual(db, date, symbols, **kwargs)
        snapshots['600641']['data_status'] = 'BLOCKED'
        snapshots['600641']['reason'] = 'history_time_budget_exceeded'
        return snapshots, events
    monkeypatch.setattr(jobs, 'calculate_history', insufficient)
    jobs.run_generation(path, target)
    assert jobs.get_report(path, target)['job']['status'] == 'PARTIAL'
    calls = []
    def restored(db, date, symbols, **kwargs):
        calls.append(symbols)
        return actual(db, date, symbols, **kwargs)
    monkeypatch.setattr(jobs, 'calculate_history', restored)
    jobs.run_generation(path, target)
    assert calls == [['600641']]
    assert jobs.get_report(path, target)['job']['status'] == 'COMPLETED'


def test_reaper_does_not_fail_a_new_task_with_same_date(tmp_path):
    jobs._write_job(tmp_path / 'db', {'date': '2026-09-29', 'job_id': 'new', 'status': 'RUNNING'})
    jobs._watch_process(Mock(wait=Mock(return_value=1)), tmp_path / 'db', '2026-09-29', 'old')
    assert jobs._load_job(tmp_path / 'db')['status'] == 'RUNNING'


def test_failed_refresh_keeps_old_archive_explicitly_stale(tmp_path, monkeypatch):
    report = {'date': '2026-09-29', 'status': 'READY'}
    monkeypatch.setattr(jobs, 'read_archive', lambda *a, **k: {
        'report': report, 'valid': False, 'generated_at': 'now', 'archive_id': 'x'})
    monkeypatch.setattr(jobs, 'render_email', lambda r: '')
    jobs._write_job(tmp_path / 'db', {'date': '2026-09-29', 'status': 'FAILED'})
    result = jobs.get_report(tmp_path / 'db', '2026-09-29')
    assert result['job']['status'] == 'STALE'
    assert result['report'] == report and not result['archive']['valid']
    assert '旧版' in result['job']['reason']
