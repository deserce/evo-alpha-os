"""Isolated post-close C monitor: exact-date, bounded, idempotent and no trades."""
from contextlib import closing
import fcntl
import json
from datetime import date, timedelta
import sqlite3

import pytest

from tests.test_structure_turning import recovery


def make_db(tmp_path):
    path = tmp_path / 'monitor.db'
    prices = recovery()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript('''
            CREATE TABLE stock_pools (pool_id TEXT PRIMARY KEY, name TEXT, monitor_enabled INTEGER);
            CREATE TABLE stock_pool_members (pool_id TEXT, symbol TEXT);
            INSERT INTO stock_pools VALUES ('a','测试池',0),('b','另一池',1);
            INSERT INTO stock_pool_members VALUES ('a','600641'),('b','600641');
            CREATE TABLE stock_info (symbol TEXT, name TEXT);
            INSERT INTO stock_info VALUES ('600641','测试股份');
            CREATE TABLE stock_daily_prices (symbol TEXT,trade_date TEXT,open REAL,high REAL,
                                             low REAL,close REAL,volume REAL);
            CREATE TABLE stock_lifecycle_state (symbol TEXT,trade_date TEXT,data_status TEXT);
            CREATE TABLE data_quality_report (trade_date TEXT,module TEXT,market TEXT,status TEXT,
                                             created_at TEXT);
        ''')
        for row in prices:
            conn.execute('INSERT INTO stock_daily_prices VALUES (?,?,?,?,?,?,?)',
                         ('600641',row['trade_date'],row['open'],row['high'],row['low'],row['close'],row['volume']))
            conn.execute('INSERT INTO stock_lifecycle_state VALUES (?,?,?)',
                         ('600641',row['trade_date'],'OK'))
            conn.execute('INSERT INTO data_quality_report VALUES (?,?,?,?,?)',
                         (row['trade_date'],'stock_daily','SH','PASS',row['trade_date']))
    return path, prices


def monitor(tmp_path):
    from quant_engine.watch.structure_monitor import StructureMonitor
    path, prices = make_db(tmp_path)
    return StructureMonitor(path), path, prices



def _buy_day(prices):
    from quant_engine.research.structure_turning import evaluate_structure_turning
    return next(marker['trade_date'] for marker in evaluate_structure_turning(prices)['markers']
                if marker['marker_type'] == 'BUY_CONFIRM')


def _holding_day(prices):
    from quant_engine.research.structure_turning import evaluate_structure_turning
    result = evaluate_structure_turning(prices)
    trade = result['trades'][0]
    event_days = {marker['trade_date'] for marker in result['markers']}
    return next(row['trade_date'] for row in prices
                if trade['entry_date'] < row['trade_date'] < trade['exit_date']
                and row['trade_date'] not in event_days)


def _next_day(prices, target):
    return prices[next(i for i, row in enumerate(prices) if row['trade_date'] == target)+1]['trade_date']


def test_daily_monitor_dedupes_all_groups_and_idempotent_current_events(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    first = worker.run(target)
    second = worker.run(target)
    assert first['counts']['total'] == 1
    assert first['rows'][0]['pool_ids'] == ['a', 'b']
    assert first['rows'][0]['status'] == 'HOLDING'
    assert [e['event_type'] for e in first['events']] == ['BUY_CONFIRM']
    assert [e['event_id'] for e in first['events']] == [e['event_id'] for e in second['events']]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM structure_monitor_events').fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'portfolio%'").fetchone()[0] == 0


@pytest.mark.parametrize('historical', [False, True])
def test_input_change_without_timestamps_invalidates_saved_result(tmp_path, historical):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    worker.run(target)
    changed_day = prices[0]['trade_date'] if historical else target
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('UPDATE stock_daily_prices SET volume=volume+1 WHERE trade_date=?',
                     (changed_day,))
    assert worker.read_status(target)['freshness'] == {
        'status': 'STALE', 'reason': 'price_data_changed'}


def test_same_second_price_repair_invalidates_saved_result(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('ALTER TABLE stock_daily_prices ADD COLUMN updated_at TEXT')
    saved = worker.run(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('UPDATE stock_daily_prices SET volume=volume+1,updated_at=? '
                     'WHERE trade_date=?', (saved['run']['updated_at'], target))
    assert worker.read_status(target)['freshness']['status'] == 'STALE'


def test_unchanged_inputs_without_timestamps_are_verifiable_and_future_rows_ignored(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    saved = worker.run(target)
    assert saved['freshness']['status'] == 'CURRENT'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('UPDATE stock_daily_prices SET volume=volume+1 WHERE trade_date>?',
                     (target,))
    assert worker.read_status(target)['freshness']['status'] == 'CURRENT'


def test_first_enable_does_not_backfill_historical_buy_events(tmp_path):
    worker, _, prices = monitor(tmp_path)
    data = worker.run(_holding_day(prices))
    assert data['rows'][0]['status'] == 'HOLDING'
    assert data['rows'][0]['state']['entry_date'] < data['trade_date']
    assert data['events'] == []


def test_missing_target_bar_preserves_last_state_as_blocked_not_exit(tmp_path):
    worker, path, prices = monitor(tmp_path)
    worker.run(_holding_day(prices))
    target = _next_day(prices, _holding_day(prices))
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM stock_daily_prices WHERE trade_date=?', (target,))
    data = worker.run(target)
    row = data['rows'][0]
    assert row['status'] == 'BLOCKED' and row['current_price'] is None
    assert row['state']['last_known_state']['status'] == 'HOLDING'
    assert row['state']['last_known_state']['trade_date'] == _holding_day(prices)
    assert data['events'] == []


def test_daily_exit_is_simulated_and_never_account_execution(tmp_path):
    from quant_engine.research.structure_turning import evaluate_structure_turning
    worker, _, prices = monitor(tmp_path)
    exit_day = next(m['trade_date'] for m in evaluate_structure_turning(prices)['markers']
                    if m['marker_type'] == 'EXIT')
    data = worker.run(exit_day)
    exit_event = next(e for e in data['events'] if e['event_type'] == 'EXIT')
    assert exit_event['payload']['simulation'] is True
    assert exit_event['payload']['execution'] == 'NONE'
    assert exit_event['trade_date'] == exit_day


def test_quality_fail_closed_but_unrelated_lifecycle_is_not_required(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET status='FAIL' WHERE trade_date=?", (target,))
    result = worker.run(target)
    assert result['rows'][0]['status'] == 'BLOCKED'
    assert result['events'] == []
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET status='PASS' WHERE trade_date=?", (target,))
        conn.execute('DELETE FROM stock_lifecycle_state WHERE trade_date=?', (target,))
    restored = worker.run(target)
    assert restored['rows'][0]['status'] == 'HOLDING'
    assert restored['rows'][0]['data_status'] == 'OK'


def test_removed_symbol_is_not_kept_active_or_sent_as_exit(tmp_path):
    worker, path, prices = monitor(tmp_path)
    worker.run(_buy_day(prices))
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM stock_pool_members')
    result = worker.run(_next_day(prices, _buy_day(prices)))
    assert result['counts']['total'] == 0
    assert result['counts']['REMOVED'] == 1
    assert result['rows'][0]['status'] == 'REMOVED'
    assert result['events'] == []


def test_monitor_rejects_busy_workflow_and_invalid_dates(tmp_path):
    worker, _, prices = monitor(tmp_path)
    with worker.lock_path.open('a+') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='workflow'):
            worker.run(prices[-1]['trade_date'])
    for invalid in ('0001-01-01','9999-12-31','20260212'):
        with pytest.raises(ValueError):
            worker.run(invalid)


def test_read_monitor_is_read_only_exact_date_and_pool_filter(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    worker.run(target)
    before = path.stat().st_mtime_ns
    assert worker.read_status(target, pool_id='a')['counts']['total'] == 1
    assert worker.read_status(target, pool_id='missing')['rows'] == []
    assert worker.read_status(_next_day(prices, _buy_day(prices)))['run'] is None
    assert path.stat().st_mtime_ns == before


def test_quality_recovery_marks_old_snapshot_stale_without_writing(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM data_quality_report WHERE trade_date=?', (target,))
    worker.run(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('INSERT INTO data_quality_report VALUES (?,?,?,?,?)',
                     (target, 'stock_daily', 'SH', 'PASS', target))
    before = path.stat().st_mtime_ns

    data = worker.read_status(target)

    assert data['freshness'] == {'status': 'STALE', 'reason': 'quality_changed'}
    assert data['run']['status'] == 'BLOCKED'
    assert data['rows'][0]['reason'] == 'stock_daily_quality_missing:SH'
    assert data['events'] == []
    assert path.stat().st_mtime_ns == before
    restored = worker.run(target)
    assert restored['freshness'] == {'status': 'CURRENT', 'reason': None}
    assert restored['rows'][0]['data_status'] == 'OK'


def test_current_quality_failure_invalidates_saved_buy_without_rerunning(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    worker.run(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET status='FAIL' WHERE trade_date=?", (target,))
    before = path.stat().st_mtime_ns
    data = worker.read_status(target)
    assert data['freshness'] == {'status': 'STALE', 'reason': 'quality_changed'}
    assert data['run']['status'] == 'COMPLETED'
    assert path.stat().st_mtime_ns == before


def test_unchanged_quality_failure_stays_blocked_and_current(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET status='FAIL' WHERE trade_date=?", (target,))
    worker.run(target)
    data = worker.read_status(target)
    assert data['freshness'] == {'status': 'CURRENT', 'reason': None}
    assert data['rows'][0]['data_status'] == 'BLOCKED'
    assert data['events'] == []


def test_timestamp_only_refresh_does_not_invalidate_unchanged_inputs(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    worker.run(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('ALTER TABLE stock_daily_prices ADD COLUMN updated_at TEXT')
        conn.execute('UPDATE stock_daily_prices SET updated_at=? WHERE trade_date=?',
                     ('2099-01-01T00:00:00+08:00', target))
    data = worker.read_status(target)
    assert data['freshness'] == {'status': 'CURRENT', 'reason': None}


def test_recovered_target_bar_marks_prior_missing_bar_snapshot_stale(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        saved = conn.execute('SELECT * FROM stock_daily_prices WHERE trade_date=?', (target,)).fetchone()
        conn.execute('DELETE FROM stock_daily_prices WHERE trade_date=?', (target,))
    worker.run(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('INSERT INTO stock_daily_prices VALUES (?,?,?,?,?,?,?)', saved)
    data = worker.read_status(target)
    assert data['freshness'] == {'status': 'STALE', 'reason': 'price_data_changed'}


def test_blocked_recalculation_withdraws_stale_current_event_then_recovers(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    first = worker.run(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET status='FAIL' WHERE trade_date=?", (target,))
    blocked = worker.run(target)
    assert blocked['events'] == []
    assert blocked['rows'][0]['state']['last_known_state']['status'] == 'HOLDING'
    assert worker.run(target)['rows'][0]['state']['last_known_state']['status'] == 'HOLDING'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET status='PASS' WHERE trade_date=?", (target,))
    recovered = worker.run(target)
    assert recovered['events'][0]['event_id'] == first['events'][0]['event_id']


def test_same_day_removal_is_idempotent_and_removes_old_event(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    worker.run(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM stock_pool_members')
    first = worker.run(target)
    second = worker.run(target)
    assert first['counts'] == second['counts'] == {'REMOVED': 1, 'total': 0, 'events': 0}


def test_scope_limit_fails_without_partial_snapshots(tmp_path):
    worker, path, prices = monitor(tmp_path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executemany('INSERT INTO stock_pool_members VALUES (?,?)',
                         [('a',f'{number:06d}') for number in range(301)])
    with pytest.raises(ValueError, match='300'):
        worker.run(prices[-1]['trade_date'])
    assert worker.read_status()['run'] is None


def test_child_cannot_bypass_unrelated_workflow_lock(tmp_path):
    import os
    worker, _, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with worker.lock_path.open('w+') as handle:
        json.dump({'pid': os.getppid(), 'stage': 'watch_engine', 'target_trade_date': target}, handle)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='workflow'):
            worker.run(target, workflow_child=True)


def test_pipeline_child_reuses_parent_lock_only_with_matching_stage(tmp_path):
    import os
    worker, _, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with worker.lock_path.open('w+') as handle:
        json.dump({'pid': os.getppid(), 'stage': 'structure_monitor',
                   'target_trade_date': target}, handle)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert worker.run(target, workflow_child=True)['counts']['total'] == 1


def test_workflow_c_is_best_effort_before_digest_and_in_morning(tmp_path, monkeypatch):
    import scripts.run_daily_data_workflow as workflow
    path, prices = make_db(tmp_path)
    monkeypatch.setattr(workflow, 'DB_PATH', path)
    args = workflow.build_parser().parse_args([
        '--skip-kline','--force-valuation','--kline-end', prices[-1]['trade_date'],
    ])
    tasks = workflow.make_tasks(args)
    names = [task.name for task in tasks]
    task = next(task for task in tasks if task.name == 'structure_monitor')
    assert not task.critical and task.independent and task.timeout_seconds == 120
    assert '--workflow-child' in task.command
    assert names.index('watch_email_immediate') < names.index('structure_monitor')
    assert names.index('structure_monitor') < names.index('watch_email_digest')
    args.morning = True
    morning = next(t for t in workflow.make_tasks(args) if t.name == 'structure_monitor')
    assert not morning.critical and morning.timeout_seconds == 120
    assert morning.command[morning.command.index('--trade-date') + 1] == prices[-1]['trade_date']


def test_monitor_new_version_never_reads_old_run_or_events(tmp_path, monkeypatch):
    from quant_engine.watch import structure_monitor as module
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    worker.run(target)
    previous_version = module.VERSION
    monkeypatch.setattr(module, 'VERSION', 'isolated-next-test-version')
    assert worker.read_status(target)['run'] is None
    assert worker.read_status(target)['events'] == []
    new = worker.run(target)
    assert new['version'] == 'isolated-next-test-version'
    with closing(sqlite3.connect(path)) as conn:
        versions = {row[0] for row in conn.execute('SELECT version FROM structure_monitor_runs')}
    assert versions == {previous_version, 'isolated-next-test-version'}


def test_config_fingerprint_includes_new_default_parameters(tmp_path):
    from quant_engine.research.structure_turning import evaluate_structure_turning
    from quant_engine.watch.structure_monitor import StructureMonitor
    worker, path, prices = monitor(tmp_path)
    assert worker.config == evaluate_structure_turning([])['config']
    worker.run(_buy_day(prices))
    different = StructureMonitor(path, {'support_tolerance_atr': .8})
    assert different.config_hash != worker.config_hash
    assert different.read_status()['run'] is None


def test_each_symbol_requires_its_own_market_report(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO stock_pool_members VALUES ('a','000001')")
        conn.execute("INSERT INTO stock_daily_prices SELECT '000001',trade_date,open,high,low,close,volume FROM stock_daily_prices WHERE symbol='600641'")
    result = worker.run(target)
    by_symbol = {row['symbol']: row for row in result['rows']}
    assert by_symbol['600641']['data_status'] == 'OK'
    assert by_symbol['000001']['reason'] == 'stock_daily_quality_missing:SZ'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('INSERT INTO data_quality_report VALUES (?,?,?,?,?)',
                     (target,'stock_daily','SZ','FAIL',target))
    result = worker.run(target)
    by_symbol = {row['symbol']: row for row in result['rows']}
    assert by_symbol['600641']['data_status'] == 'OK'
    assert by_symbol['000001']['reason'] == 'stock_daily_quality_failed:SZ'
    assert all(event['symbol'] != '000001' for event in result['events'])


def test_bj_alias_is_normalized_and_latest_market_result_wins(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO stock_pool_members VALUES ('a','920001')")
        conn.execute("INSERT INTO stock_daily_prices SELECT '920001',trade_date,open,high,low,close,volume FROM stock_daily_prices WHERE symbol='600641'")
        conn.execute('INSERT INTO data_quality_report VALUES (?,?,?,?,?)',
                     (target,'stock_daily','BSE','FAIL',target+' 15:30:00'))
        conn.execute('INSERT INTO data_quality_report VALUES (?,?,?,?,?)',
                     (target,'stock_daily','BJ','PASS',target+' 16:30:00'))
    rows = worker.run(target)['rows']
    assert all(row['data_status'] == 'OK' for row in rows)


def test_all_report_does_not_substitute_missing_market_and_global_fail_blocks(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET market='ALL'")
    assert worker.run(target)['rows'][0]['reason'] == 'stock_daily_quality_missing:SH'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_quality_report SET market='SH'")
        conn.execute('INSERT INTO data_quality_report VALUES (?,?,?,?,?)',
                     (target,'stock_daily','ALL','FAIL',target))
    assert worker.run(target)['rows'][0]['reason'] == 'stock_daily_quality_failed:ALL'


def test_bad_current_ohlcv_blocks_without_previous_price_reuse(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('UPDATE stock_daily_prices SET high=0 WHERE trade_date=?', (target,))
    row = worker.run(target)['rows'][0]
    assert row['data_status'] == 'BLOCKED'
    assert row['reason'].startswith('invalid_ohlcv:')
    assert row['current_price'] is None


def historical_db(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    days = [row['trade_date'] for row in prices if row['trade_date'] <= target][-20:]
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript('''
            CREATE TABLE strategy_run_log(strategy_name TEXT,trade_date TEXT,status TEXT);
            CREATE TABLE quant_preselect_results(strategy_name TEXT,trade_date TEXT,symbol TEXT);
            INSERT INTO stock_pools VALUES ('smart_mrgc_20d','MRGC',0);
            INSERT INTO stock_pool_members VALUES ('smart_mrgc_20d','600999');
            CREATE TABLE watch_events(id TEXT);
            INSERT INTO watch_events VALUES ('untouched');
            CREATE TABLE portfolio_positions(id TEXT);
            INSERT INTO portfolio_positions VALUES ('untouched');
            CREATE TABLE notification_deliveries(id TEXT);
            INSERT INTO notification_deliveries VALUES ('untouched');
        ''')
        conn.executemany("INSERT INTO strategy_run_log VALUES ('mrgc_v1',?,'success')",
                         [(day,) for day in days])
        conn.execute("INSERT INTO quant_preselect_results VALUES ('mrgc_v1',?,'600641')", (target,))
        conn.execute("INSERT INTO quant_preselect_results VALUES ('mrgc_v1','2099-01-01','600998')")
    return worker, path, prices


def test_historical_scope_is_read_only_exact_date_and_current_pool_unchanged(tmp_path, monkeypatch):
    from quant_engine.watch import smart_mrgc_pool
    worker, path, prices = historical_db(tmp_path)
    target = _buy_day(prices)
    monkeypatch.setattr(smart_mrgc_pool, 'refresh', lambda *a: pytest.fail('historical refresh'))
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        scope = worker.resolve_scope(conn, target, historical=True)
        assert scope['status'] == 'READY'
        assert set(scope['members']) == {'600641'}
        assert scope['membership_source'] == 'backfill_time_members'
    first = worker.run(target, historical=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("DELETE FROM stock_pool_members WHERE pool_id='a'")
        conn.execute("INSERT INTO stock_pool_members VALUES ('a','600997')")
        conn.execute('UPDATE stock_daily_prices SET volume=999 WHERE trade_date>?', (target,))
        before = conn.iterdump()
        protected = [line for line in before if any(table in line for table in
            ('stock_pool', 'watch_events', 'portfolio_positions', 'notification_deliveries'))]
    second = worker.run(target, historical=True)
    assert second['counts'] == first['counts']
    assert second['rows'][0]['pool_ids'] == ['a', 'b', 'smart_mrgc_20d']
    assert second['rows'][0]['state']['_input_digest'] == first['rows'][0]['state']['_input_digest']
    assert second['run']['stats']['historical'] is True
    assert second['run']['stats']['membership_source'] == 'historical_snapshot'
    assert second['freshness']['status'] == 'CURRENT'
    with closing(sqlite3.connect(path)) as conn:
        assert protected == [line for line in conn.iterdump() if any(table in line for table in
            ('stock_pool', 'watch_events', 'portfolio_positions', 'notification_deliveries'))]
        assert conn.execute('SELECT COUNT(*) FROM structure_monitor_runs').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM structure_monitor_events').fetchone()[0] == 1


def test_historical_scope_reuses_manual_snapshot_even_when_current_pool_is_over_capacity(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    worker.run(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM stock_pool_members')
        conn.executemany("INSERT INTO stock_pool_members VALUES ('a',?)",
                         [(f'{600000+i}',) for i in range(301)])
    data = worker.run(target, historical=True)
    assert [row['symbol'] for row in data['rows']] == ['600641']
    assert data['rows'][0]['pool_ids'] == ['a', 'b']


def test_historical_membership_failure_has_blocked_run_ledger(tmp_path):
    worker, path, prices = historical_db(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM strategy_run_log WHERE trade_date=?', (target,))
    result = worker.run(target, historical=True)
    assert result['run']['status'] == 'BLOCKED'
    assert result['run']['stats']['membership_reason'] == 'mrgc_dates_missing'
    assert result['run']['stats']['historical'] is True
    assert result['rows'] == []


@pytest.mark.parametrize('status,exit_code', [('BLOCKED', 1), ('COMPLETED', 0)])
def test_cli_historical_flag_and_blocked_exit_status(tmp_path, monkeypatch, capsys, status, exit_code):
    import scripts.run_structure_monitor as cli
    from unittest.mock import Mock
    result = dict(trade_date='2026-09-30', version='test', config_hash='test', scope='test',
                  counts={'total': 0}, run={'status': status})
    run = Mock(return_value=result)
    monkeypatch.setattr(cli.StructureMonitor, 'run', run)
    monkeypatch.setattr('sys.argv', ['run_structure_monitor.py', '--trade-date', '2026-09-30',
                                   '--db', str(tmp_path / 'cli.db'), '--historical', '--workflow-child'])
    assert cli.main() == exit_code
    run.assert_called_once_with('2026-09-30', workflow_child=True, historical=True)
    assert json.loads(capsys.readouterr().out)['run']['status'] == status


def test_historical_child_inherits_only_matching_parent_lock(tmp_path, monkeypatch):
    import os
    worker, _, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with worker.lock_path.open('w+') as handle:
        handle.write(json.dumps(dict(pid=os.getppid(), stage='structure_monitor',
                                     target_trade_date=target)))
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert worker.run(target, historical=True, workflow_child=True)['run']['status'] == 'COMPLETED'
        with pytest.raises(RuntimeError, match='busy'):
            worker.run(_next_day(prices, target), historical=True, workflow_child=True)


def test_historical_manual_daily_snapshot_is_used_without_mutation(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    groups = [dict(pool_id='a', name='冻结池', symbols=['600642'])]
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('CREATE TABLE three_group_daily_runs(trade_date TEXT,groups_json TEXT)')
        conn.execute('INSERT INTO three_group_daily_runs VALUES (?,?)', (target, json.dumps(groups)))
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        scope = worker.resolve_scope(conn, target, historical=True)
    assert scope['members']['600641']['pool_ids'] == ['b']
    assert scope['members']['600642']['pool_ids'] == ['a']
    assert scope['membership_sources'] == {'a': 'historical_snapshot', 'b': 'backfill_time_members'}


def test_historical_scope_limit_and_invalid_member_fail_without_partial_run(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM stock_pool_members')
        conn.executemany("INSERT INTO stock_pool_members VALUES ('a',?)",
                         [(f'{600000+i}',) for i in range(301)])
    with pytest.raises(ValueError, match='300'):
        worker.run(target, historical=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute('SELECT COUNT(*) FROM structure_monitor_runs').fetchone()[0] == 0
        conn.execute('DELETE FROM stock_pool_members')
        conn.execute("INSERT INTO stock_pool_members VALUES ('a','bad')")
    with pytest.raises(ValueError, match='invalid'):
        worker.run(target, historical=True)


def test_historical_run_does_not_prune_other_dates_and_digest_remains_read_only(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        worker.ensure_schema(conn)
        conn.execute('INSERT INTO structure_monitor_runs VALUES (?,?,?,?,?,?,?,?,?)',
            ('2023-01-01', 'old', 'old', 'COMPLETED', 'old', '{}', '{}', 0, 'old'))
    worker.run(target, historical=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute("SELECT 1 FROM structure_monitor_runs WHERE version='old'").fetchone()
        conn.execute('UPDATE stock_daily_prices SET volume=volume+1 WHERE trade_date=?',
                     (prices[0]['trade_date'],))
    before = path.stat().st_mtime_ns
    assert worker.read_status(target)['freshness']['status'] == 'STALE'
    assert before == path.stat().st_mtime_ns


def test_historical_membership_recovery_keeps_original_manual_scope(tmp_path):
    worker, path, prices = historical_db(tmp_path)
    target = _buy_day(prices)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM strategy_run_log WHERE trade_date=?', (target,))
    assert worker.run(target, historical=True)['run']['status'] == 'BLOCKED'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE stock_pool_members SET symbol='600997' WHERE pool_id IN ('a','b')")
        conn.execute("INSERT INTO strategy_run_log VALUES ('mrgc_v1',?,'success')", (target,))
    data = worker.run(target, historical=True)
    assert data['run']['status'] == 'COMPLETED'
    assert [row['symbol'] for row in data['rows']] == ['600641']
    assert data['rows'][0]['pool_ids'] == ['a', 'b', 'smart_mrgc_20d']


def test_partial_daily_c_run_does_not_claim_full_historical_membership(tmp_path):
    worker, path, prices = monitor(tmp_path)
    target = _buy_day(prices)
    worker.run(target, historical=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE structure_monitor_runs SET scope='three_group_daily_supplement'")
        conn.execute("INSERT INTO stock_pool_members VALUES ('a','600642')")
        conn.row_factory = sqlite3.Row
        scope = worker.resolve_scope(conn, target, historical=True)
    assert set(scope['members']) == {'600641', '600642'}
