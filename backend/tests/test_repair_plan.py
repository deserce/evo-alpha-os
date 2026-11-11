"""Read-only, calendar-driven recent-date repair planning."""
import sqlite3

import pytest

from data_core.repair_plan import build_repair_plan, persist_ready_states


@pytest.fixture()
def ready_database(monkeypatch):
    from data_core import repair_plan
    from quant_engine.watch.structure_monitor import StructureMonitor, VERSION

    conn = database()
    for table in ('stock_daily_prices', 'stock_daily_prices_raw', 'quant_feature_rps'):
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info({table})')]
        fields = ','.join("'2026-10-22'" if key == 'trade_date' else key for key in columns)
        conn.execute(f"INSERT INTO {table} SELECT {fields} FROM {table} WHERE trade_date='2026-10-21'")
    conn.executescript('''
        CREATE TABLE data_quality_report(trade_date TEXT,module TEXT,market TEXT,status TEXT,created_at TEXT);
        CREATE TABLE index_daily(index_code TEXT,trade_date TEXT,close REAL);
        CREATE TABLE etf_info(symbol TEXT); INSERT INTO etf_info VALUES ('510300');
        CREATE TABLE etf_daily_prices(symbol TEXT,trade_date TEXT,close REAL);
        CREATE TABLE quant_feature_etf_rps(symbol TEXT,trade_date TEXT);
        CREATE TABLE sector_list(sector_name TEXT); INSERT INTO sector_list VALUES ('A');
        CREATE TABLE sector_composite_daily(sector_name TEXT,trade_date TEXT,close REAL);
        CREATE TABLE quant_feature_sector_rps_v2(sector_name TEXT,trade_date TEXT);
        CREATE TABLE stock_valuation_daily(code TEXT,trade_date TEXT);
        CREATE TABLE structure_health_daily(symbol TEXT,trade_date TEXT);
        CREATE TABLE stock_lifecycle_state(symbol TEXT,trade_date TEXT);
        CREATE TABLE stock_pool_members(pool_id TEXT,symbol TEXT);
        INSERT INTO stock_pool_members VALUES ('manual','600001'),('manual','000001');
        CREATE TABLE stock_pools(pool_id TEXT,monitor_enabled INTEGER);
        INSERT INTO stock_pools VALUES ('manual',1);
        CREATE TABLE watch_daily_snapshot(symbol TEXT,trade_date TEXT,data_status TEXT,factors TEXT);
        CREATE TABLE structure_monitor_snapshots(symbol TEXT,trade_date TEXT,version TEXT,
            config_hash TEXT,status TEXT,data_status TEXT,state_json TEXT);
        CREATE TABLE structure_monitor_runs(trade_date TEXT,status TEXT);
        CREATE TABLE market_strength_daily(index_code TEXT,trade_date TEXT);
        CREATE TABLE industry_strength_daily(sector_name TEXT,trade_date TEXT);
        CREATE TABLE pipeline_task_log(task_name TEXT,status TEXT,target_dates TEXT);
        CREATE TABLE strategy_run_log(strategy_name TEXT,trade_date TEXT,status TEXT);
        CREATE TABLE three_group_daily_runs(trade_date TEXT,status TEXT,report_json TEXT);
    ''')
    config = StructureMonitor(':memory:').config_hash
    for day in ('2026-10-21', '2026-10-22', '2026-10-23'):
        for market in ('SH', 'SZ', 'BSE'):
            conn.execute("INSERT INTO data_quality_report VALUES (?,'stock_daily',?,'PASS',?)", (day,market,day))
        for code in ('000001','399001','399006','399317','000300','000688','000905','000852'):
            conn.execute('INSERT INTO index_daily VALUES (?,?,3000)', (code,day))
        conn.execute("INSERT INTO etf_daily_prices VALUES ('510300',?,4)", (day,))
        conn.execute("INSERT INTO quant_feature_etf_rps VALUES ('510300',?)", (day,))
        conn.execute("INSERT INTO sector_composite_daily VALUES ('A',?,100)", (day,))
        conn.execute("INSERT INTO quant_feature_sector_rps_v2 VALUES ('A',?)", (day,))
        for symbol in ('600001','000001'):
            for table in ('stock_valuation_daily','structure_health_daily','stock_lifecycle_state'):
                conn.execute(f'INSERT INTO {table} VALUES (?,?)', (symbol,day))
            conn.execute("INSERT INTO watch_daily_snapshot VALUES (?,?,'OK',?)",
                         (symbol,day,'{"watch_rebuild":{"input_signature":"fixture"}}'))
            conn.execute("INSERT INTO structure_monitor_snapshots VALUES (?,?,?,?,'WATCHING','OK',?)",
                         (symbol,day,VERSION,config,'{"_input_digest":"fixture"}'))
        conn.execute("INSERT INTO structure_monitor_runs VALUES (?,'COMPLETED')", (day,))
        for code in ('000001', '000300', '399006', '000852'):
            conn.execute("INSERT INTO market_strength_daily VALUES (?,?)", (code, day))
        conn.execute("INSERT INTO industry_strength_daily VALUES ('A',?)", (day,))
        for task in ('timing_signals','risk_plans','strength_features'):
            conn.execute("INSERT INTO pipeline_task_log VALUES (?,'success',?)", (task,day))
        conn.execute("INSERT INTO strategy_run_log VALUES ('mrgc_v1',?,'success')", (day,))
        conn.execute("INSERT INTO three_group_daily_runs VALUES (?,'READY','{}')", (day,))
    monkeypatch.setattr(repair_plan, '_c_members', lambda conn, day: ({'600001','000001'}, None))
    try:
        assert build_repair_plan(conn,'2026-10-23',limit=3)['complete']
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize('source', ['index','etf','sector'])
@pytest.mark.parametrize('change', ['revision','backfill'])
def test_support_history_propagates_only_to_its_rolling_dependents(ready_database, source, change):
    from data_core.repair_plan import persist_pending_states

    conn = ready_database
    table = {'index':'index_daily','etf':'etf_daily_prices','sector':'sector_composite_daily'}[source]
    if change == 'backfill':
        saved = conn.execute(f"SELECT * FROM {table} WHERE trade_date='2026-10-21'").fetchall()
        conn.execute(f"DELETE FROM {table} WHERE trade_date='2026-10-21'")
    before = build_repair_plan(conn,'2026-10-23',limit=3)
    persist_ready_states(conn,before)
    persist_pending_states(conn,before)
    if change == 'backfill':
        conn.executemany(f'INSERT INTO {table} VALUES (?,?,?)', saved)
    else:
        conn.execute(f"UPDATE {table} SET close=close+1 WHERE trade_date='2026-10-21'")
    changes = conn.total_changes
    after = build_repair_plan(conn,'2026-10-23',limit=3)
    assert conn.total_changes == changes
    affected = ({'etf_rps'} if source == 'etf' else
                {'strength','lifecycle','mrgc','timing','risk','watch','c','report'})
    if source == 'sector':
        affected.add('sector_rps')
    for row in after['dates'][1:]:
        assert affected <= set(row['pending'])
        assert all(row['datasets'][name]['reason'] in ('upstream_changed','pending_repair') for name in affected)
        assert row['datasets']['stock_rps']['ready']
        if source == 'etf':
            assert set(row['pending']) == {'etf_rps'}


@pytest.mark.parametrize('source,dependent', [('index','strength'),('etf','etf_rps'),('sector','sector_rps')])
def test_support_debt_survives_source_commit_and_window_roll(ready_database, source, dependent):
    from data_core.repair_plan import persist_pending_states

    conn = ready_database
    persist_ready_states(conn,build_repair_plan(conn,'2026-10-23',limit=3))
    table = {'index':'index_daily','etf':'etf_daily_prices','sector':'sector_composite_daily'}[source]
    conn.execute(f"UPDATE {table} SET close=close+1 WHERE trade_date='2026-10-21'")
    pending = build_repair_plan(conn,'2026-10-23',limit=3)
    persist_pending_states(conn,pending)
    post = build_repair_plan(conn,'2026-10-23',limit=3,completed={('2026-10-21',source)})
    persist_ready_states(conn,post)
    rolled = build_repair_plan(conn,'2026-10-23',limit=2)
    for row in rolled['dates']:
        assert dependent in row['pending']
        assert conn.execute('SELECT 1 FROM data_repair_pending WHERE trade_date=? AND dataset=?',
                            (row['trade_date'],dependent)).fetchone()


def test_complete_support_noop_and_window_fingerprints_are_stable(ready_database):
    conn = ready_database
    first = build_repair_plan(conn,'2026-10-22',limit=2)
    persist_ready_states(conn,first)
    before = conn.total_changes
    repeated = build_repair_plan(conn,'2026-10-22',limit=2)
    assert repeated == first
    assert repeated['complete']
    rolled = build_repair_plan(conn,'2026-10-23',limit=2)
    assert rolled['complete']
    assert first['dates'][-1]['datasets'] == rolled['dates'][0]['datasets']
    assert conn.total_changes == before


@pytest.mark.parametrize('kind', ['real','oldlocal','cleanlocal'])
def test_valuation_authenticity_distinguishes_legacy_and_clean_degradation(ready_database, kind):
    conn = ready_database
    for column, definition in (('source','TEXT'),('pe_ttm','REAL'),('pb','REAL'),('total_mv','REAL')):
        conn.execute(f'ALTER TABLE stock_valuation_daily ADD COLUMN {column} {definition}')
    values = {'real':('ifind',10,2,1000), 'oldlocal':('local_kline',0,0,1000),
              'cleanlocal':('local_kline',None,None,None)}[kind]
    conn.execute('UPDATE stock_valuation_daily SET source=?,pe_ttm=?,pb=?,total_mv=?', values)
    row = build_repair_plan(conn,'2026-10-23',limit=1)['dates'][0]['datasets']['valuation']
    assert row['ready'] is (kind != 'oldlocal')
    assert row['degraded'] == (0 if kind == 'real' else 2)
    if kind == 'oldlocal':
        assert row['reason'] == 'legacy_valuation_placeholder'
        conn.execute('UPDATE stock_valuation_daily SET pe_ttm=NULL,pb=NULL,total_mv=NULL')
        migrated = build_repair_plan(conn,'2026-10-23',limit=1)
        persist_ready_states(conn,migrated)
        assert build_repair_plan(conn,'2026-10-23',limit=1)['dates'][0]['datasets']['valuation']['ready']
    if kind == 'cleanlocal':
        assert not build_repair_plan(conn,'2026-10-23',limit=1)['complete']


def test_valuation_degraded_counts_missing_and_local_intersection(ready_database):
    conn = ready_database
    conn.executescript('''ALTER TABLE stock_valuation_daily ADD COLUMN source TEXT;
        ALTER TABLE stock_valuation_daily ADD COLUMN pe_ttm REAL;
        ALTER TABLE stock_valuation_daily ADD COLUMN pb REAL;
        ALTER TABLE stock_valuation_daily ADD COLUMN total_mv REAL;
        UPDATE stock_valuation_daily SET source='local_kline';
        DELETE FROM stock_valuation_daily WHERE code='000001';
        INSERT INTO stock_valuation_daily VALUES ('999999','2026-10-23','local_kline',0,0,1000);''')
    row = build_repair_plan(conn,'2026-10-23',limit=1)['dates'][0]['datasets']['valuation']
    assert row['degraded'] == 2
    assert row['ready']


@pytest.mark.parametrize('table', ['stock_daily_prices','stock_daily_prices_raw'])
def test_stock_amount_changes_invalidate_later_analytics(ready_database, table):
    conn = ready_database
    conn.execute(f'ALTER TABLE {table} ADD COLUMN amount REAL DEFAULT 100')
    persist_ready_states(conn,build_repair_plan(conn,'2026-10-23',limit=3))
    conn.execute(f"UPDATE {table} SET amount=200 WHERE trade_date='2026-10-21'")
    plan = build_repair_plan(conn,'2026-10-23',limit=3)
    assert plan['dates'][0]['source_changed']
    assert 'stock_rps' in plan['dates'][-1]['pending']


def test_valuation_migration_clears_only_completed_debt_and_keeps_real_rows(ready_database):
    from data_core.repair_plan import persist_pending_states

    conn = ready_database
    conn.executescript('''ALTER TABLE stock_valuation_daily ADD COLUMN source TEXT;
        ALTER TABLE stock_valuation_daily ADD COLUMN pe_ttm REAL;
        ALTER TABLE stock_valuation_daily ADD COLUMN pb REAL;
        ALTER TABLE stock_valuation_daily ADD COLUMN total_mv REAL;
        UPDATE stock_valuation_daily SET source='ifind',pe_ttm=10,pb=2,total_mv=1000;
        UPDATE stock_valuation_daily SET source='local_kline',pe_ttm=0,pb=0,total_mv=1000
            WHERE code='000001';''')
    before = build_repair_plan(conn,'2026-10-23',limit=3)
    persist_pending_states(conn,before)
    real = [tuple(r) for r in conn.execute("SELECT * FROM stock_valuation_daily WHERE source='ifind'")]
    conn.execute("UPDATE stock_valuation_daily SET pe_ttm=NULL,pb=NULL,total_mv=NULL WHERE source='local_kline'")
    completed = {('2026-10-23','valuation')}
    post = build_repair_plan(conn,'2026-10-23',limit=3,completed=completed)
    persist_ready_states(conn,post)
    again = build_repair_plan(conn,'2026-10-23',limit=3)
    assert 'valuation' not in again['dates'][-1]['pending']
    assert again['dates'][-1]['datasets']['valuation']['degraded'] == 1
    assert not again['complete']
    assert all('valuation' in row['pending'] for row in again['dates'][:-1])
    assert [tuple(r) for r in conn.execute("SELECT * FROM stock_valuation_daily WHERE source='ifind'")] == real


@pytest.mark.parametrize('pe,pb,mv', [(0,None,None),(None,0,None),(None,None,0)])
def test_any_legacy_local_valuation_placeholder_triggers_migration(ready_database, pe, pb, mv):
    conn = ready_database
    conn.executescript('''ALTER TABLE stock_valuation_daily ADD COLUMN source TEXT;
        ALTER TABLE stock_valuation_daily ADD COLUMN pe REAL;
        ALTER TABLE stock_valuation_daily ADD COLUMN pb REAL;
        ALTER TABLE stock_valuation_daily ADD COLUMN total_mv REAL;''')
    conn.execute("UPDATE stock_valuation_daily SET source='local_kline',pe=?,pb=?,total_mv=?", (pe,pb,mv))
    row = build_repair_plan(conn,'2026-10-23',limit=1)['dates'][0]['datasets']['valuation']
    assert not row['ready']
    assert row['reason'] == 'legacy_valuation_placeholder'


@pytest.mark.parametrize('source,dependent', [('index','strength'),('etf','etf_rps'),('sector','sector_rps')])
def test_source_only_debt_propagates_even_if_source_task_completed(ready_database, source, dependent):
    from data_core.repair_plan import persist_pending_states

    conn = ready_database
    persist_ready_states(conn,build_repair_plan(conn,'2026-10-23',limit=3))
    persist_pending_states(conn,{'dates': []}, additional={('2026-10-21',source)})
    plan = build_repair_plan(conn,'2026-10-23',limit=3,completed={('2026-10-21',source)})
    assert plan['dates'][0]['datasets'][source]['ready']
    assert all(dependent in row['pending'] for row in plan['dates'])


def database():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE trading_calendar(trade_date TEXT,is_open INTEGER);
        INSERT INTO trading_calendar VALUES
            ('2026-10-21',1),('2026-10-22',1),('2026-10-23',1),('2026-10-24',0);
        CREATE TABLE stock_info(symbol TEXT,list_date TEXT);
        INSERT INTO stock_info VALUES ('600001','2020-01-01'),('000001','2020-01-01');
        CREATE TABLE stock_daily_prices(symbol TEXT,trade_date TEXT,open REAL,high REAL,
            low REAL,close REAL,volume REAL);
        CREATE TABLE stock_daily_prices_raw AS SELECT * FROM stock_daily_prices;
        CREATE TABLE quant_feature_rps(symbol TEXT,trade_date TEXT,rps_250 REAL);
    ''')
    for day in ('2026-10-21','2026-10-23'):
        for symbol in ('600001','000001'):
            conn.execute('INSERT INTO stock_daily_prices VALUES (?,?,1,2,1,2,100)',(symbol,day))
            conn.execute('INSERT INTO stock_daily_prices_raw VALUES (?,?,1,2,1,2,100)',(symbol,day))
            conn.execute('INSERT INTO quant_feature_rps VALUES (?,?,90)',(symbol,day))
    return conn


def test_calendar_keeps_internal_whole_date_gap_and_is_read_only():
    with database() as conn:
        before = conn.total_changes
        plan = build_repair_plan(conn,'2026-10-23',limit=3)
        assert [day['trade_date'] for day in plan['dates']] == [
            '2026-10-21','2026-10-22','2026-10-23']
        assert plan['dates'][1]['datasets']['stock']['actual'] == 0
        assert 'stock' in plan['dates'][1]['pending']
        assert conn.total_changes == before
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='data_repair_state'").fetchone()


def test_partial_rps_is_pending_even_with_latest_date_present():
    with database() as conn:
        conn.execute("DELETE FROM quant_feature_rps WHERE symbol='000001'")
        row = build_repair_plan(conn,'2026-10-23',limit=1)['dates'][0]
        assert row['datasets']['stock_rps']['actual'] == 1
        assert not row['datasets']['stock_rps']['ready']
        assert 'stock_rps' in row['pending']


def test_source_changes_invalidate_later_analytics_without_timestamp_columns():
    with database() as conn:
        first = build_repair_plan(conn,'2026-10-23',limit=3)
        persist_ready_states(conn,first)
        conn.execute("UPDATE stock_daily_prices SET volume=999 WHERE trade_date='2026-10-21'")
        plan = build_repair_plan(conn,'2026-10-23',limit=3)
        assert plan['dates'][0]['source_changed']
        assert plan['dates'][-1]['datasets']['stock_rps']['reason'] == 'upstream_changed'


def test_closed_day_and_invalid_window_are_rejected():
    with database() as conn:
        with pytest.raises(ValueError):
            build_repair_plan(conn,'2026-10-24')
        with pytest.raises(ValueError):
            build_repair_plan(conn,'2026-10-23',limit=1000)


def test_sector_coverage_uses_independent_metadata_not_its_own_partial_output():
    with database() as conn:
        conn.executescript('''CREATE TABLE sector_list(sector_name TEXT);
            INSERT INTO sector_list VALUES ('A'),('B');
            CREATE TABLE sector_composite_daily(sector_name TEXT,trade_date TEXT);
            CREATE TABLE quant_feature_sector_rps_v2(sector_name TEXT,trade_date TEXT);
            INSERT INTO sector_composite_daily VALUES ('A','2026-10-23');
            INSERT INTO quant_feature_sector_rps_v2 VALUES ('A','2026-10-23');''')
        row = build_repair_plan(conn,'2026-10-23',limit=1)['dates'][0]
        for name in ('sector','sector_rps'):
            assert row['datasets'][name]['expected'] == 2
            assert not row['datasets'][name]['ready']


def test_rolling_window_does_not_change_unchanged_date_fingerprint():
    with database() as conn:
        conn.execute("INSERT INTO stock_daily_prices SELECT symbol,'2026-10-22',open,high,low,close,volume "
                     "FROM stock_daily_prices WHERE trade_date='2026-10-21'")
        conn.execute("INSERT INTO stock_daily_prices_raw SELECT * FROM stock_daily_prices WHERE trade_date='2026-10-22'")
        conn.execute("INSERT INTO quant_feature_rps SELECT symbol,'2026-10-22',rps_250 "
                     "FROM quant_feature_rps WHERE trade_date='2026-10-21'")
        first = build_repair_plan(conn,'2026-10-22',limit=2)
        persist_ready_states(conn,first)
        old = first['dates'][-1]['datasets']['stock_rps']['input_hash']
        new = build_repair_plan(conn,'2026-10-23',limit=2)['dates'][0]
        assert new['datasets']['stock_rps']['input_hash'] == old


def test_etf_and_index_value_revisions_invalidate_their_dependents():
    with database() as conn:
        conn.executescript('''CREATE TABLE etf_info(symbol TEXT); INSERT INTO etf_info VALUES ('510300');
            CREATE TABLE etf_daily_prices(symbol TEXT,trade_date TEXT,close REAL);
            CREATE TABLE quant_feature_etf_rps(symbol TEXT,trade_date TEXT);
            INSERT INTO etf_daily_prices VALUES ('510300','2026-10-23',4);
            INSERT INTO quant_feature_etf_rps VALUES ('510300','2026-10-23');
            CREATE TABLE index_daily(index_code TEXT,trade_date TEXT,close REAL);
            INSERT INTO index_daily VALUES ('000001','2026-10-23',3000);''')
        before = build_repair_plan(conn,'2026-10-23',limit=1)
        persist_ready_states(conn,before)
        conn.execute('UPDATE etf_daily_prices SET close=5')
        conn.execute('UPDATE index_daily SET close=3100')
        after = build_repair_plan(conn,'2026-10-23',limit=1)['dates'][0]['datasets']
        for name in ('etf','etf_rps','index','strength'):
            assert after[name]['input_hash'] != before['dates'][0]['datasets'][name]['input_hash']


def test_deferred_dependent_remains_pending_after_source_is_committed():
    from data_core.repair_plan import persist_pending_states
    with database() as conn:
        before = build_repair_plan(conn,'2026-10-23',limit=3)
        persist_ready_states(conn,before)
        conn.execute("UPDATE stock_daily_prices SET volume=volume+1 WHERE trade_date='2026-10-21'")
        pending = build_repair_plan(conn,'2026-10-23',limit=3)
        persist_pending_states(conn,pending)
        post = build_repair_plan(conn,'2026-10-23',limit=3,
                                 completed={('2026-10-21','stock')})
        persist_ready_states(conn,post)
        again = build_repair_plan(conn,'2026-10-23',limit=3)
        assert 'stock_rps' in again['dates'][-1]['pending']


def test_sparse_zero_outputs_require_exact_date_success_audit():
    with database() as conn:
        conn.executescript('''CREATE TABLE pipeline_task_log(task_name TEXT,status TEXT,target_dates TEXT);
            INSERT INTO pipeline_task_log VALUES ('timing_signals@2026-10-23','success','2026-10-23');
            CREATE TABLE data_quality_report(trade_date TEXT,module TEXT,market TEXT,status TEXT,created_at TEXT);
            INSERT INTO data_quality_report VALUES
                ('2026-10-23','stock_daily','SH','PASS','2026-10-23'),
                ('2026-10-23','stock_daily','SZ','PASS','2026-10-23'),
                ('2026-10-23','stock_daily','BSE','PASS','2026-10-23');''')
        row = build_repair_plan(conn,'2026-10-23',limit=1)['dates'][0]
        assert row['datasets']['timing']['ready']
        assert not row['datasets']['risk']['ready']


def test_pool_changes_do_not_recompute_unrelated_stock_rps():
    with database() as conn:
        conn.execute('CREATE TABLE stock_pool_members(pool_id TEXT,symbol TEXT)')
        first = build_repair_plan(conn,'2026-10-23',limit=1)
        persist_ready_states(conn,first)
        conn.execute("INSERT INTO stock_pool_members VALUES ('manual','600002')")
        next_plan = build_repair_plan(conn,'2026-10-23',limit=1)
        assert (next_plan['dates'][0]['datasets']['stock_rps']['input_hash'] ==
                first['dates'][0]['datasets']['stock_rps']['input_hash'])


def test_missing_current_calendar_is_planned_without_writes():
    with database() as conn:
        before = conn.total_changes
        plan = build_repair_plan(conn, '2026-10-26', limit=3)
        assert [row['trade_date'] for row in plan['dates']] == [
            '2026-10-22', '2026-10-23', '2026-10-26']
        assert 'stock' in plan['dates'][-1]['pending']
        assert plan['dates'][-1]['datasets']['stock']['actual'] == 0
        assert conn.total_changes == before
        assert not conn.execute(
            "SELECT 1 FROM trading_calendar WHERE trade_date='2026-10-26'").fetchone()


def test_missing_calendar_window_is_bounded_and_skips_published_holidays():
    with database() as conn:
        conn.execute('DELETE FROM trading_calendar')
        plan = build_repair_plan(conn, '2026-10-08', limit=7)
        assert [row['trade_date'] for row in plan['dates']] == [
            '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-28',
            '2026-09-29', '2026-09-30', '2026-10-08']
        assert all('stock' in row['pending'] for row in plan['dates'])


@pytest.mark.parametrize('target', ['2027-01-04', '2026-10-01', '2026-10-24'])
def test_missing_calendar_unknown_year_or_closed_date_fails_closed(target):
    with database() as conn:
        with pytest.raises(ValueError, match='target_calendar_not_open'):
            build_repair_plan(conn, target)


@pytest.mark.parametrize('states', [(0,), (0, 1)])
def test_explicit_closed_or_conflicting_calendar_is_never_overridden(states):
    with database() as conn:
        conn.executemany('INSERT INTO trading_calendar VALUES (?, ?)',
                         [('2026-10-26', state) for state in states])
        with pytest.raises(ValueError, match='target_calendar_not_open'):
            build_repair_plan(conn, '2026-10-26')


def test_inferred_window_preserves_explicit_closed_session():
    with database() as conn:
        conn.execute("UPDATE trading_calendar SET is_open=0 WHERE trade_date='2026-10-23'")
        conn.execute('PRAGMA query_only=ON')
        plan = build_repair_plan(conn, '2026-10-26', limit=3)
        assert [row['trade_date'] for row in plan['dates']] == [
            '2026-10-21', '2026-10-22', '2026-10-26']


def test_inferred_window_rejects_conflicting_internal_session():
    with database() as conn:
        conn.execute("INSERT INTO trading_calendar VALUES ('2026-10-23',0)")
        with pytest.raises(ValueError, match='calendar_conflicting_records'):
            build_repair_plan(conn, '2026-10-26', limit=3)


@pytest.mark.parametrize('scenario,ready', [
    ('quality_pass_with_listing_gap', True),
    ('stale_quality_count', False),
    ('missing_raw', False),
    ('quality_fail', False),
])
def test_stock_readiness_uses_current_quality_gate(ready_database, scenario, ready):
    from data_core.quality import actual_stock_count

    conn = ready_database
    conn.execute('ALTER TABLE data_quality_report ADD COLUMN actual_count INTEGER')
    for market in ('SH', 'SZ', 'BSE'):
        conn.execute('UPDATE data_quality_report SET actual_count=? WHERE market=?',
                     (actual_stock_count(conn, '2026-10-23', market), market))
    if scenario == 'quality_pass_with_listing_gap':
        conn.execute("INSERT INTO stock_info VALUES ('600002','2020-01-01')")
    elif scenario == 'stale_quality_count':
        conn.execute("UPDATE data_quality_report SET actual_count=0 WHERE market='SH'")
    elif scenario == 'missing_raw':
        conn.execute("DELETE FROM stock_daily_prices_raw WHERE trade_date='2026-10-23' AND symbol='600001'")
    else:
        conn.execute("UPDATE data_quality_report SET status='FAIL' WHERE market='SH'")
    plan = build_repair_plan(conn, '2026-10-23', limit=1)
    stock = plan['dates'][0]['datasets']['stock']
    assert stock['ready'] is ready
    if ready:
        assert stock['missing'] == 1
        assert 'stock_rps' not in plan['dates'][0]['pending']


@pytest.mark.parametrize('change', ['add', 'remove'])
def test_completed_historical_membership_is_frozen(ready_database, change):
    conn = ready_database
    conn.executescript('''CREATE TABLE quant_stock_pool(symbol TEXT,pool_name TEXT,is_active INTEGER);
        INSERT INTO quant_stock_pool VALUES ('600001','core_pool',1),('000001','core_pool',1);
        DELETE FROM stock_pool_members;
        INSERT INTO pipeline_task_log VALUES ('lifecycle_state','success','2026-10-23');''')
    first = build_repair_plan(conn, '2026-10-23', limit=1)
    persist_ready_states(conn, first)
    if change == 'add':
        conn.execute("INSERT INTO quant_stock_pool VALUES ('600003','core_pool',1)")
    else:
        conn.execute("DELETE FROM quant_stock_pool WHERE symbol='600001'")
    after = build_repair_plan(conn, '2026-10-23', limit=1)
    for name in ('lifecycle', 'mrgc'):
        assert after['dates'][0]['datasets'][name]['ready']
        assert after['dates'][0]['datasets'][name]['input_hash'] == first['dates'][0]['datasets'][name]['input_hash']
    conn.execute("DELETE FROM stock_lifecycle_state WHERE trade_date='2026-10-23' AND symbol='600001'")
    assert 'lifecycle' in build_repair_plan(conn, '2026-10-23', limit=1)['dates'][0]['pending']


def test_frozen_historical_members_do_not_hide_source_changes(ready_database):
    conn = ready_database
    conn.execute("INSERT INTO pipeline_task_log VALUES ('lifecycle_state','success','2026-10-23')")
    persist_ready_states(conn, build_repair_plan(conn, '2026-10-23', limit=1))
    conn.execute("UPDATE stock_daily_prices SET close=close+1 WHERE trade_date='2026-10-23'")
    pending = build_repair_plan(conn, '2026-10-23', limit=1)['dates'][0]['pending']
    assert {'lifecycle', 'mrgc'} <= set(pending)


def test_legacy_history_can_freeze_exact_date_members_after_current_pool_change(ready_database):
    conn = ready_database
    conn.execute("INSERT INTO pipeline_task_log VALUES ('lifecycle_state','success','2026-10-23')")
    first = build_repair_plan(conn, '2026-10-23', limit=1)
    persist_ready_states(conn, first)
    if 'members_json' in [row[1] for row in conn.execute('PRAGMA table_info(data_repair_state)')]:
        conn.execute('UPDATE data_repair_state SET members_json=NULL')
    conn.execute("INSERT INTO stock_pool_members VALUES ('manual','600003')")
    after = build_repair_plan(conn, '2026-10-23', limit=1)
    assert all(after['dates'][0]['datasets'][name]['ready'] for name in ('lifecycle','mrgc'))
    persist_ready_states(conn, after)
    conn.execute("DELETE FROM stock_lifecycle_state WHERE symbol='600001' AND trade_date='2026-10-23'")
    assert 'lifecycle' in build_repair_plan(conn, '2026-10-23', limit=1)['dates'][0]['pending']


def test_completed_historical_watch_membership_is_frozen(ready_database):
    conn = ready_database
    conn.execute("INSERT INTO pipeline_task_log VALUES ('watch_engine','success','2026-10-23')")
    first = build_repair_plan(conn, '2026-10-23', limit=1)
    persist_ready_states(conn, first)
    conn.execute("INSERT INTO stock_pool_members VALUES ('manual','600003')")
    after = build_repair_plan(conn, '2026-10-23', limit=1)
    assert after['dates'][0]['datasets']['watch']['ready']
    conn.execute("DELETE FROM watch_daily_snapshot WHERE symbol='600001' AND trade_date='2026-10-23'")
    assert 'watch' in build_repair_plan(conn, '2026-10-23', limit=1)['dates'][0]['pending']


@pytest.mark.parametrize('table', ['index_daily', 'sector_composite_daily'])
def test_frozen_history_still_invalidates_changed_support_source(ready_database, table):
    conn = ready_database
    conn.execute("INSERT INTO pipeline_task_log VALUES ('lifecycle_state','success','2026-10-23')")
    persist_ready_states(conn, build_repair_plan(conn, '2026-10-23', limit=1))
    conn.execute(f"UPDATE {table} SET close=close+1 WHERE trade_date='2026-10-23'")
    pending = build_repair_plan(conn, '2026-10-23', limit=1)['dates'][0]['pending']
    assert {'lifecycle', 'mrgc'} <= set(pending)


@pytest.mark.parametrize('obstacle', ['missing_lifecycle_audit', 'missing_mrgc_audit',
                                      'old_hash_mismatch', 'pending_debt'])
def test_legacy_freeze_does_not_bypass_missing_proof_or_debt(ready_database, obstacle):
    from data_core.repair_plan import persist_pending_states

    conn = ready_database
    conn.execute("INSERT INTO pipeline_task_log VALUES ('lifecycle_state','success','2026-10-23')")
    first = build_repair_plan(conn, '2026-10-23', limit=1)
    persist_ready_states(conn, first)
    conn.execute('UPDATE data_repair_state SET members_json=NULL')
    conn.execute("INSERT INTO stock_pool_members VALUES ('manual','600003')")
    if obstacle == 'missing_lifecycle_audit':
        conn.execute("DELETE FROM pipeline_task_log WHERE task_name='lifecycle_state'")
    elif obstacle == 'missing_mrgc_audit':
        conn.execute('DELETE FROM strategy_run_log')
    elif obstacle == 'old_hash_mismatch':
        conn.execute("UPDATE data_repair_state SET input_hash='unknown' WHERE dataset IN ('lifecycle','mrgc')")
    else:
        persist_pending_states(conn, first, additional={('2026-10-23', 'lifecycle'),
                                                       ('2026-10-23', 'mrgc')})
    after = build_repair_plan(conn, '2026-10-23', limit=1)
    assert {'lifecycle', 'mrgc'} <= set(after['dates'][0]['pending'])


def test_current_day_membership_remains_live_and_can_roll_into_frozen_history(ready_database, monkeypatch):
    from datetime import date as real_date
    from data_core import repair_plan

    class FixedDate(real_date):
        current = real_date(2026, 10, 23)

        @classmethod
        def today(cls):
            return cls.current

    monkeypatch.setattr(repair_plan, 'date', FixedDate)
    conn = ready_database
    conn.execute("INSERT INTO pipeline_task_log VALUES ('lifecycle_state','success','2026-10-23')")
    persist_ready_states(conn, build_repair_plan(conn, '2026-10-23', limit=1))
    conn.execute("INSERT INTO stock_pool_members VALUES ('manual','600003')")
    live = build_repair_plan(conn, '2026-10-23', limit=1)
    assert {'lifecycle', 'mrgc'} <= set(live['dates'][0]['pending'])
    FixedDate.current = real_date(2026, 10, 26)
    prior = build_repair_plan(conn, '2026-10-23', limit=1)
    assert all(prior['dates'][0]['datasets'][name]['ready'] for name in ('lifecycle', 'mrgc'))


@pytest.mark.parametrize('scenario,ready', [('daily_universe', True),
                                           ('missing_eligible', False),
                                           ('missing_audit', False)])
def test_strength_coverage_uses_daily_eligible_universe(ready_database, scenario, ready):
    conn = ready_database
    conn.executescript('''ALTER TABLE sector_list ADD COLUMN sector_type TEXT DEFAULT 'Industry';
        ALTER TABLE sector_composite_daily ADD COLUMN quality_flag TEXT DEFAULT 'PASS';
        DELETE FROM industry_strength_daily;
        DELETE FROM sector_composite_daily;
        DELETE FROM sector_list;''')
    for number in range(468):
        sector = f'industry_{number}'
        conn.execute("INSERT INTO sector_list VALUES (?,'Industry')", (sector,))
        conn.execute("INSERT INTO industry_strength_daily VALUES (?,'2026-10-22')", (sector,))
        conn.execute("INSERT INTO sector_composite_daily VALUES (?,'2026-10-23',100,?)",
                     (sector, 'FAIL' if number == 467 else 'PASS'))
        if number < 467:
            conn.execute("INSERT INTO industry_strength_daily VALUES (?,'2026-10-23')", (sector,))
    if scenario == 'missing_audit':
        conn.execute("DELETE FROM pipeline_task_log WHERE task_name='strength_features'")
    if scenario == 'missing_eligible':
        conn.execute("DELETE FROM industry_strength_daily WHERE trade_date='2026-10-23' AND sector_name='industry_1'")
        conn.execute("INSERT INTO industry_strength_daily VALUES ('unrelated','2026-10-23')")
    state = build_repair_plan(conn, '2026-10-23', limit=1)['dates'][0]['datasets']['strength']
    assert state['ready'] is ready
    assert state['expected'] == 471
    if ready:
        assert state['actual'] == 471


def test_strength_expected_market_accepts_prior_available_index_for_ffill(ready_database):
    from data_core.repair_plan import _strength_universes

    conn = ready_database
    conn.execute("DELETE FROM index_daily WHERE trade_date='2026-10-23' AND index_code='000852'")
    market, _ = _strength_universes(conn, '2026-10-23')
    assert market == {'000001', '000300', '399006', '000852'}
