from sqlalchemy import create_engine, text

from data_job.auto_f10 import enqueue_symbols, pending_symbols, finish_symbol


def test_queue_deduplicates_and_validates(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    assert enqueue_symbols(['600519', '600519', 'bad'], engine=engine, start=False) == 1
    assert pending_symbols(engine) == ['600519']


def test_success_has_cache_cooldown(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    enqueue_symbols(['600519'], engine=engine, start=False)
    finish_symbol(engine, '600519', True)
    assert enqueue_symbols(['600519'], engine=engine, start=False) == 0
    assert pending_symbols(engine) == []


def test_failure_retained_with_bounded_retry(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    enqueue_symbols(['600519'], engine=engine, start=False)
    finish_symbol(engine, '600519', False)
    assert pending_symbols(engine) == []
    with engine.begin() as conn:
        conn.execute(text("UPDATE stock_f10_download_queue SET next_attempt_at='2000-01-01'"))
    assert pending_symbols(engine) == ['600519']


def test_bundle_downloads_recent_earnings_and_real_f10():
    from unittest.mock import Mock
    from data_job.auto_f10 import refresh_bundle
    collector = Mock()
    collector._refresh_finance_summary.return_value = 8
    collector._refresh_financial_reports.return_value = 8
    collector._refresh_finance_snapshot.return_value = 1
    collector._refresh_f10.return_value = 9
    assert refresh_bundle(collector, '600519')
    collector._refresh_finance_summary.assert_called_once_with('600519', years=6)
    collector._refresh_f10.assert_called_once_with('600519', remote_first=True)


def test_bundle_finance_failure_still_downloads_f10():
    from unittest.mock import Mock
    from data_job.auto_f10 import refresh_bundle
    collector = Mock()
    collector._refresh_finance_summary.side_effect = RuntimeError('offline')
    collector._refresh_f10.return_value = 9
    assert not refresh_bundle(collector, '600519')
    collector._refresh_financial_reports.assert_not_called()
    collector._refresh_f10.assert_called_once()


def test_bundle_empty_f10_is_not_success():
    from unittest.mock import Mock
    from data_job.auto_f10 import refresh_bundle
    collector = Mock()
    collector._refresh_finance_summary.return_value = 0
    collector._refresh_financial_reports.return_value = 8
    collector._refresh_finance_snapshot.return_value = 1
    collector._refresh_f10.return_value = 0
    assert not refresh_bundle(collector, '600519')


def test_download_has_hard_timeout(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from data_job import auto_f10
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    calls = []
    monkeypatch.setattr(auto_f10.subprocess, 'run', lambda *a, **kw: (
        calls.append((a, kw)) or SimpleNamespace(returncode=0)))
    assert auto_f10.download_symbol(engine, '600519')
    assert calls[0][1]['timeout'] == 120


def test_remote_f10_does_not_accept_local_summary(monkeypatch):
    from data_job.collectors.stock_enrichment_collector import StockEnrichmentCollector
    collector = object.__new__(StockEnrichmentCollector)
    monkeypatch.setattr(collector, '_local_f10_sections', lambda symbol: {'summary': 'local'})
    monkeypatch.setattr(collector, '_mootdx_f10', lambda symbol: {})
    monkeypatch.setattr(collector, '_eastmoney_f10', lambda symbol: {})
    assert collector._refresh_f10('600519', remote_first=True) == 0


def test_empty_earnings_does_not_pass_with_old_caches():
    from unittest.mock import Mock
    from data_job.auto_f10 import refresh_bundle
    collector = Mock()
    collector._refresh_finance_summary.return_value = 0
    collector._refresh_financial_reports.return_value = 8
    collector._refresh_finance_snapshot.return_value = 1
    collector._refresh_f10.return_value = 9
    assert not refresh_bundle(collector, '600519')
    collector._refresh_financial_reports.assert_not_called()


def test_existing_mrgc_results_are_discovered_without_recalculation(tmp_path, monkeypatch):
    from data_job import auto_f10
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE quant_preselect_results (symbol TEXT, strategy_name TEXT, trade_date TEXT)'))
        conn.execute(text("INSERT INTO quant_preselect_results VALUES ('600519','mrgc_v1','2026-09-30'),('000703','mrgc_v1','2026-09-29'),('000001','other','2026-10-01')"))
        conn.execute(text('CREATE TABLE stock_pool_members (symbol TEXT)'))
        conn.execute(text("INSERT INTO stock_pool_members VALUES ('600519'),('000636')"))
    monkeypatch.setattr(auto_f10, 'start_worker', lambda engine: None)
    assert auto_f10.enqueue_current_universe(engine) == 3
    assert auto_f10.pending_symbols(engine) == ['000001', '000636', '600519']
    assert auto_f10.enqueue_current_universe(engine) == 3
    assert auto_f10.pending_symbols(engine) == ['000001', '000636', '600519']


def test_download_child_uses_explicit_database_url(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from data_job import auto_f10
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    calls = []
    monkeypatch.setattr(auto_f10.subprocess, 'run', lambda *a, **kw: (
        calls.append(kw) or SimpleNamespace(returncode=0)))
    assert auto_f10.download_symbol(engine, '600519')
    assert calls[0]['env']['LOCAL_DATABASE_URL'] == str(engine.url)
    assert calls[0]['env']['LOCAL_DB_PATH'] == str(tmp_path / 'isolated.db')


def test_universe_without_source_tables_is_safe(tmp_path, monkeypatch):
    from data_job import auto_f10
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setattr(auto_f10, 'start_worker', lambda engine: None)
    assert auto_f10.enqueue_current_universe(engine) == 0
    assert auto_f10.pending_symbols(engine) == []


def test_old_two_year_success_is_requeued_for_six_year_bundle(tmp_path):
    from data_job import auto_f10
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text('''CREATE TABLE stock_f10_download_queue (
            symbol TEXT PRIMARY KEY,status TEXT,next_attempt_at TEXT,updated_at TEXT)'''))
        conn.execute(text("INSERT INTO stock_f10_download_queue VALUES ('688072','done','2099-01-01','2026-10-03')"))
    assert auto_f10.enqueue_symbols(['688072'], engine=engine, start=False) == 1
    assert auto_f10.pending_symbols(engine) == ['688072']


def test_finance_bundle_matches_six_year_chart():
    from unittest.mock import Mock
    from data_job.auto_f10 import refresh_bundle
    collector = Mock()
    collector._refresh_finance_summary.return_value = 22
    collector._refresh_financial_reports.return_value = 22
    collector._refresh_finance_snapshot.return_value = 1
    collector._refresh_f10.return_value = 6
    assert refresh_bundle(collector, '688072')
    collector._refresh_finance_summary.assert_called_once_with('688072', years=6)


def test_worker_deferred_while_datacore_holds_lock(tmp_path, monkeypatch):
    import fcntl
    from data_job import auto_f10
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    enqueue_symbols(['600519'], engine=engine, start=False)
    calls = []
    monkeypatch.setattr(auto_f10.subprocess, 'Popen', lambda *a, **kw: calls.append(a))
    with (tmp_path / '.daily_data_workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        auto_f10.start_worker(engine)
    assert calls == []
    assert pending_symbols(engine) == ['600519']


def test_worker_drains_multiple_batches_within_budget(tmp_path, monkeypatch):
    import os
    from data_job import auto_f10
    from app.core import database
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    enqueue_symbols([f'{i:06d}' for i in range(30)], engine=engine, start=False)
    monkeypatch.setattr(database, 'get_engine', lambda: engine)
    calls = []
    monkeypatch.setattr(auto_f10, 'download_symbol', lambda engine, symbol, **kw: calls.append(symbol) or True)
    with (tmp_path / 'worker.lock').open('a') as lock:
        auto_f10.run_worker(os.dup(lock.fileno()))
    assert len(calls) == 30
    assert pending_symbols(engine) == []


def test_worker_budget_exhaustion_retains_unprocessed_members(tmp_path, monkeypatch):
    import os
    from data_job import auto_f10
    from app.core import database
    engine = create_engine(f"sqlite:///{tmp_path / 'budget.db'}")
    enqueue_symbols(['000001', '000002'], engine=engine, start=False)
    monkeypatch.setattr(database, 'get_engine', lambda: engine)
    times = iter([0, 1801])
    monkeypatch.setattr(auto_f10.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(auto_f10, 'download_symbol', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not download')))
    with (tmp_path / 'worker.lock').open('a') as lock:
        auto_f10.run_worker(os.dup(lock.fileno()))
    assert pending_symbols(engine) == ['000001', '000002']


def test_success_expires_at_next_shanghai_day(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'daily.db'}")
    enqueue_symbols(['600519'], engine=engine, start=False)
    finish_symbol(engine, '600519', True)
    with engine.connect() as conn:
        remaining = conn.execute(text("""SELECT
            (julianday(next_attempt_at)-julianday('now'))*24
            FROM stock_f10_download_queue""")).scalar_one()
    assert 0 < remaining <= 24
    with engine.connect() as conn:
        assert conn.execute(text("""SELECT strftime('%H:%M:%S',next_attempt_at,'+8 hours')
            FROM stock_f10_download_queue""")).scalar_one() == '00:00:00'


def test_kick_rediscovers_universe(monkeypatch):
    import sys
    from unittest.mock import Mock
    from data_job import auto_f10
    from app.core import database
    engine = Mock()
    discover = Mock()
    monkeypatch.setattr(database, 'get_engine', lambda: engine)
    monkeypatch.setattr(auto_f10, 'enqueue_current_universe', discover)
    monkeypatch.setattr(sys, 'argv', ['auto_f10', '--kick'])
    assert auto_f10.main() == 0
    discover.assert_called_once_with(engine)
    engine.dispose.assert_called_once()


def test_bundle_exposes_individual_module_failures():
    from unittest.mock import Mock
    from data_job.auto_f10 import refresh_bundle
    collector = Mock()
    collector._refresh_finance_summary.side_effect = RuntimeError('source unavailable')
    collector._refresh_f10.return_value = 4
    modules = {}
    assert not refresh_bundle(collector, '600519', statuses=modules)
    assert modules['finance_summary']['status'] == 'failed'
    assert modules['financial_reports']['status'] == 'skipped'
    assert modules['f10'] == {'status': 'ok', 'rows': 4}


def test_worker_stock_limit_retains_backlog(tmp_path, monkeypatch):
    import os
    from data_job import auto_f10
    from app.core import database
    engine = create_engine(f"sqlite:///{tmp_path / 'limit.db'}")
    enqueue_symbols([f'{i:06d}' for i in range(510)], engine=engine, start=False)
    monkeypatch.setattr(database, 'get_engine', lambda: engine)
    monkeypatch.setattr(auto_f10.time, 'monotonic', lambda: 0)
    calls = []
    monkeypatch.setattr(auto_f10, 'download_symbol',
                        lambda *args, **kwargs: calls.append(args[1]) or True)
    with (tmp_path / 'worker.lock').open('a') as lock:
        auto_f10.run_worker(os.dup(lock.fileno()))
    assert len(calls) == len(set(calls)) == 500
    assert len(pending_symbols(engine)) == 10


def test_worker_partial_budget_retains_unprocessed(tmp_path, monkeypatch):
    import os
    from data_job import auto_f10
    from app.core import database
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    enqueue_symbols(['000001', '000002'], engine=engine, start=False)
    monkeypatch.setattr(database, 'get_engine', lambda: engine)
    times = iter([0, 1, 1801])
    monkeypatch.setattr(auto_f10.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(auto_f10, 'download_symbol', lambda *args, **kwargs: True)
    with (tmp_path / 'worker.lock').open('a') as lock:
        auto_f10.run_worker(os.dup(lock.fileno()))
    assert pending_symbols(engine) == ['000002']


def test_download_persists_module_diagnostics(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from data_job import auto_f10
    engine = create_engine(f"sqlite:///{tmp_path / 'diagnostics.db'}")
    enqueue_symbols(['600519'], engine=engine, start=False)
    modules = {'finance_summary': {'status': 'failed', 'rows': 0, 'error': 'offline'},
               'f10': {'status': 'ok', 'rows': 6}}

    def download(*args, **kwargs):
        kwargs['stdout'].write(('MATERIAL_RESULT=' + json.dumps(modules) + '\n').encode())
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(auto_f10.subprocess, 'run', download)
    assert not auto_f10.download_symbol(engine, '600519')
    with engine.connect() as conn:
        row = conn.execute(text('SELECT module_status,last_error FROM stock_f10_download_queue')).one()
    assert json.loads(row[0]) == modules
    assert row[1] == 'finance_summary: offline'


def test_formula_discovery_uses_latest_exact_source_day_and_success(tmp_path, monkeypatch):
    from data_job import auto_f10

    engine = create_engine(f"sqlite:///{tmp_path / 'formula.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_daily_prices (trade_date TEXT)'))
        conn.execute(text("INSERT INTO stock_daily_prices VALUES ('2026-11-06'), ('2026-11-09')"))
        conn.execute(text('CREATE TABLE quant_formula_results (symbol TEXT, strategy_name TEXT, trade_date TEXT)'))
        conn.execute(text('CREATE TABLE strategy_run_log (strategy_name TEXT, trade_date TEXT, status TEXT, finished_at TEXT)'))
        conn.execute(text("""INSERT INTO quant_formula_results VALUES
            ('000001','formula_1','2026-11-06'), ('000001','formula_1','2026-11-09'),
            ('000002','formula_2','2026-11-09'), ('000003','formula_1','2026-11-06'),
            ('000004','formula_3','2026-11-09'), ('000005','formula_1','2026-11-10')
        """))
        conn.execute(text("""INSERT INTO strategy_run_log VALUES
            ('formula_1','2026-11-06','success','2026-11-06 16:00:00'),
            ('formula_1','2026-11-09','success','2026-11-09 16:00:00'),
            ('formula_2','2026-11-09','success','2026-11-09 16:00:00'),
            ('formula_2','2026-11-09','failed','2026-11-09 16:01:00'),
            ('formula_1','2026-11-10','success','2026-11-10 16:00:00')
        """))
    monkeypatch.setattr(auto_f10, 'start_worker', lambda engine: None)
    assert auto_f10.enqueue_current_universe(engine) == 1
    assert auto_f10.pending_symbols(engine) == ['000001']
    assert auto_f10.enqueue_formula_range(engine, '2026-11-06', '2026-11-09', kick=False) == (2, 2)
    assert auto_f10.pending_symbols(engine) == ['000001', '000003']


def test_formula_range_rejects_reversed_and_oversized_windows(tmp_path):
    import pytest
    from data_job import auto_f10

    engine = create_engine(f"sqlite:///{tmp_path / 'invalid.db'}")
    with pytest.raises(ValueError, match='formula range'):
        auto_f10.enqueue_formula_range(engine, '2026-11-09', '2026-11-06', kick=False)
    with pytest.raises(ValueError, match='formula range'):
        auto_f10.enqueue_formula_range(engine, '2026-09-01', '2026-11-06', kick=False)


def test_retry_reuses_fresh_remote_f10_and_retries_failed_finance(tmp_path):
    import json
    from unittest.mock import Mock
    from data_job import auto_f10

    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    enqueue_symbols(['600519'], engine=engine, start=False)
    finish_symbol(engine, '600519', False)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_f10_sections (symbol TEXT, source TEXT, content TEXT, fetched_at TEXT)'))
        conn.execute(text("""INSERT INTO stock_f10_sections VALUES
            ('600519','mootdx','overview',DATETIME('now','+8 hours'))
        """))
        conn.execute(text("""
            UPDATE stock_f10_download_queue
            SET module_status=:modules, next_attempt_at='2000-01-01'
        """), {'modules': json.dumps({
            'f10': {'status': 'ok', 'rows': 1},
            'finance_summary': {'status': 'failed', 'rows': 0},
        })})
    enqueue_symbols(['600519'], engine=engine, start=False)
    reusable = auto_f10._reusable_modules(engine, '600519')
    assert reusable == {'f10': {'status': 'ok', 'rows': 1}}
    collector = Mock()
    collector._refresh_finance_summary.return_value = 8
    collector._refresh_financial_reports.return_value = 8
    collector._refresh_finance_snapshot.return_value = 1
    statuses = {}
    assert auto_f10.refresh_bundle(collector, '600519', statuses=statuses, reusable=reusable)
    collector._refresh_f10.assert_not_called()
    collector._refresh_finance_summary.assert_called_once()
    assert statuses['f10'] == reusable['f10']
    with engine.begin() as conn:
        conn.execute(text("UPDATE stock_f10_sections SET source='local_cache'"))
    assert auto_f10._reusable_modules(engine, '600519') == {}


def test_formula_backfill_reuses_valid_cache_before_first_download(tmp_path):
    from data_job import auto_f10

    engine = create_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    enqueue_symbols(['600519'], engine=engine, start=False, formula_priorities={'600519': 1})
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE stock_finance_summary (
            code TEXT, source TEXT, report_date TEXT, total_revenue REAL,
            net_profit REAL, fetched_at TEXT
        )"""))
        conn.execute(text("""INSERT INTO stock_finance_summary VALUES
            ('600519','eastmoney_f10','2026-09-30',300,20,DATETIME('now','+8 hours','-12 hours'))
        """))
        conn.execute(text('CREATE TABLE stock_f10_sections (symbol TEXT, source TEXT, content TEXT, fetched_at TEXT)'))
        conn.execute(text("""INSERT INTO stock_f10_sections VALUES
            ('600519','mootdx','overview',DATETIME('now','+8 hours','-6 days')),
            ('600519','mootdx','stale',DATETIME('now','+8 hours','-8 days')),
            ('600519','local_cache','synthetic',DATETIME('now','+8 hours'))
        """))
    assert auto_f10._reusable_modules(engine, '600519') == {
        'finance_summary': {'status': 'ok', 'rows': 1, 'cached': True},
        'f10': {'status': 'ok', 'rows': 1, 'cached': True},
    }
    with engine.begin() as conn:
        conn.execute(text("UPDATE stock_finance_summary SET net_profit=NULL"))
    assert 'finance_summary' not in auto_f10._reusable_modules(engine, '600519')


def test_latest_formula_first_attempt_precedes_history_but_retry_does_not(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'priority.db'}")
    enqueue_symbols(
        ['000001', '600519'], engine=engine, start=False,
        formula_priorities={'000001': 1, '600519': 2},
    )
    assert pending_symbols(engine) == ['600519', '000001']
    finish_symbol(engine, '600519', False)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE stock_f10_download_queue SET next_attempt_at='2000-01-01'
            WHERE symbol='600519'
        """))
    assert pending_symbols(engine) == ['000001', '600519']


def test_formula_worker_marks_fully_cached_bundle_without_child(tmp_path, monkeypatch):
    import os
    from app.core import database
    from data_job import auto_f10

    engine = create_engine(f"sqlite:///{tmp_path / 'cached.db'}")
    enqueue_symbols(['600519'], engine=engine, start=False, formula_priorities={'600519': 2})
    monkeypatch.setattr(database, 'get_engine', lambda: engine)
    cached = {
        module: {'status': 'ok', 'rows': 1, 'cached': True}
        for module in ('finance_summary', 'financial_reports', 'finance_snapshot', 'f10')
    }
    monkeypatch.setattr(auto_f10, '_reusable_modules', lambda *a, **kw: cached)

    def unexpected_download(*args, **kwargs):
        raise AssertionError('fully cached bundle should not start a subprocess')

    monkeypatch.setattr(auto_f10, 'download_symbol', unexpected_download)
    with (tmp_path / 'worker.lock').open('a') as lock:
        auto_f10.run_worker(os.dup(lock.fileno()))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM stock_f10_download_queue")).scalar_one() == 'done'
