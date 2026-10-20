from contextlib import closing
from datetime import date, timedelta
import sqlite3
import pytest
from quant_engine.watch.smart_mrgc_pool import refresh


def seed():
    c = sqlite3.connect(':memory:')
    c.executescript('''CREATE TABLE stock_pools (pool_id TEXT PRIMARY KEY,name TEXT,
    description TEXT,sort_order INTEGER,is_default INTEGER,monitor_enabled INTEGER,
    legacy_migrated INTEGER,created_at TEXT,updated_at TEXT);
    CREATE TABLE stock_pool_members (pool_id TEXT,symbol TEXT,note TEXT,
    sort_order INTEGER,added_at TEXT,updated_at TEXT,PRIMARY KEY(pool_id,symbol));
    CREATE TABLE stock_daily_prices (trade_date TEXT,symbol TEXT);
    CREATE TABLE strategy_run_log (strategy_name TEXT,trade_date TEXT,status TEXT);
    CREATE TABLE quant_preselect_results (strategy_name TEXT,trade_date TEXT,symbol TEXT);''')
    ds = [(date(2026,1,1)+timedelta(days=n)).isoformat() for n in range(21)]
    c.executemany('INSERT INTO stock_daily_prices VALUES (?,?)',[(d,'600001') for d in ds])
    c.executemany('INSERT INTO strategy_run_log VALUES (?,?,?)',[('mrgc_v1',d,'success') for d in ds])
    c.executemany('INSERT INTO quant_preselect_results VALUES (?,?,?)',
        [('mrgc_v1',ds[0],'600001'),('mrgc_v1',ds[1],'600002'),
         ('mrgc_v1',ds[19],'600002'),('mrgc_v1',ds[19],'600002')])
    c.commit()
    return c, ds


def test_ready_deduplication_baseline_and_no_commit():
    c,ds=seed()
    with closing(c):
        result=refresh(c,ds[19],activation_date=ds[19])
        assert result['status']=='READY' and result['member_count']==2
        assert result['initialized_date']==ds[19] and c.in_transaction
        assert '2 次' in c.execute('SELECT note FROM stock_pool_members WHERE symbol="600002"').fetchone()[0]
        assert c.execute('SELECT monitor_enabled FROM stock_pools').fetchone()[0]==0
        c.rollback()
        assert c.execute('SELECT COUNT(*) FROM stock_pool_members').fetchone()[0]==0


def test_rolling_expiry_preserves_baseline():
    c,ds=seed()
    with closing(c):
        refresh(c,ds[19],activation_date=ds[19])
        result=refresh(c,ds[20],activation_date=ds[20])
        assert result['member_count']==1 and result['window_start']==ds[1]
        assert result['initialized_date']==ds[19]


def test_missing_run_retains_members():
    c,ds=seed()
    with closing(c):
        refresh(c,ds[19],activation_date=ds[19])
        c.execute('DELETE FROM strategy_run_log WHERE trade_date=?',(ds[20],))
        result=refresh(c,ds[20],activation_date=ds[20])
        assert result['status']=='BLOCKED' and result['member_count']==2
        assert result['missing_dates']==[ds[20]]


def test_first_failed_refresh_has_no_baseline():
    c,ds=seed()
    with closing(c):
        c.execute('UPDATE strategy_run_log SET status="failed"')
        assert refresh(c,ds[19],activation_date=ds[19])['initialized_date'] is None


@pytest.mark.parametrize('size,reason',[(301,'c_monitor_capacity_exceeded'),(1001,'candidate_capacity_exceeded')])
def test_capacity_never_truncates(size,reason):
    c,ds=seed()
    with closing(c):
        refresh(c,ds[19],activation_date=ds[19])
        c.executemany('INSERT INTO quant_preselect_results VALUES (?,?,?)',
                     [('mrgc_v1',ds[20],f'{n:06d}') for n in range(size)])
        result=refresh(c,ds[20],activation_date=ds[20])
        assert result['reason']==reason and result['member_count']==2


def test_calendar_keeps_missing_source_day_in_window():
    c,ds=seed()
    with closing(c):
        c.execute('CREATE TABLE trading_calendar(trade_date TEXT,is_open INTEGER)')
        c.executemany('INSERT INTO trading_calendar VALUES (?,1)',[(d,) for d in ds])
        c.execute('DELETE FROM stock_daily_prices WHERE trade_date=?',(ds[3],))
        c.execute('DELETE FROM strategy_run_log WHERE trade_date=?',(ds[3],))
        result=refresh(c,ds[20],activation_date=ds[20])
        assert result['calendar_source']=='trading_calendar'
        assert result['missing_dates']==[ds[3]]


def test_bad_symbol_and_date():
    c,ds=seed()
    with closing(c):
        with pytest.raises(ValueError):
            refresh(c,'2026-1-1')
        c.execute('INSERT INTO quant_preselect_results VALUES (?,?,?)',('mrgc_v1',ds[19],'bad'))
        assert refresh(c,ds[19],activation_date=ds[19])['reason']=='invalid_candidate_symbol'


def test_activation_after_target_suppresses_pre_activation_dates():
    c,ds=seed()
    with closing(c):
        result=refresh(c,ds[19],activation_date='2026-02-01')
        assert result['initialized_date']=='2026-02-01'
        assert refresh(c,ds[20],activation_date='2026-02-02')['initialized_date']=='2026-02-01'


def test_historical_refresh_keeps_current_state_and_members():
    c,ds=seed()
    with closing(c):
        current=refresh(c,ds[20],activation_date=ds[20])
        before=list(c.execute('SELECT * FROM stock_pool_members'))
        state=list(c.execute('SELECT * FROM smart_watchlist_state'))
        result=refresh(c,ds[19],activation_date=ds[20])
        assert result['target_trade_date']==current['target_trade_date']
        assert result['reason']==current['reason']
        assert list(c.execute('SELECT * FROM stock_pool_members'))==before
        assert list(c.execute('SELECT * FROM smart_watchlist_state'))==state


def test_invalid_activation_date_rejected():
    c,ds=seed()
    with closing(c), pytest.raises(ValueError):
        refresh(c,ds[19],activation_date='2026-2-1')


def test_resolve_historical_members_is_read_only_and_excludes_future():
    from quant_engine.watch.smart_mrgc_pool import resolve_members
    c, ds = seed()
    with closing(c):
        refresh(c, ds[20], activation_date=ds[20])
        c.commit()
        c.execute('INSERT INTO quant_preselect_results VALUES (?,?,?)',
                  ('mrgc_v1', ds[20], '600099'))
        c.commit()
        before = c.total_changes
        result = resolve_members(c, ds[19])
        assert result['status'] == 'READY'
        assert result['dates'] == list(reversed(ds[:20]))
        assert result['members'] == [
            {'symbol': '600001', 'last_hit_date': ds[0], 'hit_count': 1},
            {'symbol': '600002', 'last_hit_date': ds[19], 'hit_count': 2},
        ]
        assert c.total_changes == before and not c.in_transaction
        assert c.execute('SELECT target_trade_date FROM smart_watchlist_state').fetchone()[0] == ds[20]


def test_resolve_reports_incomplete_runs_without_partial_members():
    from quant_engine.watch.smart_mrgc_pool import resolve_members
    c, ds = seed()
    with closing(c):
        c.execute('DELETE FROM strategy_run_log WHERE trade_date=?', (ds[3],))
        result = resolve_members(c, ds[19])
        assert result['reason'] == 'mrgc_dates_missing'
        assert result['missing_dates'] == [ds[3]]
        assert result['members'] == []


def test_resolve_candidate_capacity_is_bounded():
    from quant_engine.watch.smart_mrgc_pool import resolve_members
    c, ds = seed()
    with closing(c):
        c.executemany('INSERT INTO quant_preselect_results VALUES (?,?,?)',
                      [('mrgc_v1', ds[19], f'{n:06d}') for n in range(1002)])
        result = resolve_members(c, ds[19])
        assert result['reason'] == 'candidate_capacity_exceeded'
        assert result['members'] == []
