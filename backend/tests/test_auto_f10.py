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
    assert auto_f10.enqueue_current_universe(engine) == 2
    assert auto_f10.pending_symbols(engine) == ['000636', '600519']
    assert auto_f10.enqueue_current_universe(engine) == 2
    assert auto_f10.pending_symbols(engine) == ['000636', '600519']


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


def test_worker_one_batch_and_budget_retains_pending(tmp_path, monkeypatch):
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
    assert len(calls) == 25
    assert len(pending_symbols(engine)) == 5


def test_worker_budget_exhaustion_retains_unprocessed_members(tmp_path, monkeypatch):
    import os
    from data_job import auto_f10
    from app.core import database
    engine = create_engine(f"sqlite:///{tmp_path / 'budget.db'}")
    enqueue_symbols(['000001', '000002'], engine=engine, start=False)
    monkeypatch.setattr(database, 'get_engine', lambda: engine)
    times = iter([0, 601])
    monkeypatch.setattr(auto_f10.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(auto_f10, 'download_symbol', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not download')))
    with (tmp_path / 'worker.lock').open('a') as lock:
        auto_f10.run_worker(os.dup(lock.fileno()))
    assert pending_symbols(engine) == ['000001', '000002']
