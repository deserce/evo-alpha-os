from datetime import date, timedelta
from contextlib import closing
import sqlite3
import pytest
from quant_engine.watch.mrgc_daily_report import build_report, render_email
from quant_engine.watch.structure_monitor import StructureMonitor
from quant_engine.research.structure_turning import VERSION


def seed(tmp_path):
    path = tmp_path / 'report.db'
    monitor = StructureMonitor(path)
    days = [(date(2026, 1, 1) + timedelta(days=n)).isoformat() for n in range(21)]
    with closing(sqlite3.connect(path)) as c:
        c.executescript('''CREATE TABLE stock_pools(pool_id TEXT,name TEXT);
        CREATE TABLE stock_pool_members(pool_id TEXT,symbol TEXT);
        INSERT INTO stock_pools VALUES ('pool_0d1aea597dc4','公司'),('watchlist','自选股');
        CREATE TABLE stock_daily_prices(symbol TEXT,trade_date TEXT,close REAL);
        CREATE TABLE strategy_run_log(strategy_name TEXT,trade_date TEXT,status TEXT);
        CREATE TABLE quant_preselect_results(strategy_name TEXT,trade_date TEXT,symbol TEXT);
        CREATE TABLE stock_lifecycle_state(symbol TEXT,trade_date TEXT,name TEXT,sector_name TEXT,
        rps_20 REAL,structure_score REAL,data_status TEXT);
        CREATE TABLE data_quality_report(trade_date TEXT,module TEXT,market TEXT,status TEXT,created_at TEXT);''')
        StructureMonitor.ensure_schema(c)
        for d in days:
            c.execute('INSERT INTO stock_daily_prices VALUES (?,?,?)', ('600001', d, 10))
            c.execute('INSERT INTO strategy_run_log VALUES (?,?,?)', ('mrgc_v1', d, 'success'))
            c.execute('INSERT INTO stock_lifecycle_state VALUES (?,?,?,?,?,?,?)',
                      ('600001', d, '<恶意>', '机械', 90, 75, 'OK'))
            c.execute('INSERT INTO data_quality_report VALUES (?,?,?,?,?)', (d, 'stock_daily', 'SH', 'PASS', d))
            c.execute('INSERT INTO structure_monitor_runs VALUES (?,?,?,?,?,?,?,?,?)',
                      (d, VERSION, monitor.config_hash, 'COMPLETED', 'all', '{}', '{}', 1, d))
            c.execute('INSERT INTO structure_monitor_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (d, '600001', VERSION, monitor.config_hash, '<恶意>', '[]', '[]', 'HOLDING',
                       'OK', '', 10, '{"status":"HOLDING","entry_price":9,"stop_price":8}', d))
        c.execute('INSERT INTO quant_preselect_results VALUES (?,?,?)', ('mrgc_v1', days[0], '600001'))
        c.execute('INSERT INTO quant_preselect_results VALUES (?,?,?)', ('mrgc_v1', days[20], '600002'))
        c.execute('INSERT INTO structure_monitor_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  ('x', days[19], '600001', VERSION, monitor.config_hash, '股', '[]', '[]',
                   'BUY_CONFIRM', None, None, '{"reason":"<确认>","price":10}', days[19]))
        c.commit()
    return path, days


def test_happy_exact_day_events_and_preview(tmp_path):
    path, ds = seed(tmp_path)
    before = path.read_bytes()
    report = build_report(path, ds[19])
    assert report['status'] == 'READY'
    assert report['counts']['total'] == report['counts']['buy_confirm'] == 1
    assert report['counts']['existing_holding'] == 0
    assert report['stocks'][0]['price'] == 10
    preview = render_email(report)
    assert '&lt;恶意&gt;' in preview and '<恶意>' not in preview
    assert path.read_bytes() == before


def test_historical_members_never_borrow_current_pool(tmp_path):
    path, ds = seed(tmp_path)
    old, new = build_report(path, ds[19]), build_report(path, ds[20])
    assert [s['symbol'] for s in old['stocks']] == ['600001']
    assert [s['symbol'] for s in new['stocks']] == ['600002']
    assert new['status'] == 'BLOCKED' and new['counts']['buy_confirm'] == 0


def test_missing_run_and_current_c_config_block(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.execute('DELETE FROM strategy_run_log WHERE trade_date=?', (ds[4],))
        # Incomplete MRGC windows expose no speculative members; company remains independently visible.
        c.execute('INSERT INTO stock_pool_members VALUES (?,?)', ('pool_0d1aea597dc4', '600001'))
        c.execute('UPDATE structure_monitor_snapshots SET config_hash="old" WHERE trade_date=?', (ds[19],))
        c.commit()
    r = build_report(path, ds[19])
    assert r['status'] == 'BLOCKED'
    assert any(ds[4] + '：MRGC 筛选尚未成功完成' in text for text in r['missing'])
    assert r['stocks'][0]['data_status'] == 'BLOCKED'


def test_invalid_date_empty_db_and_no_date_fallback(tmp_path):
    path, ds = seed(tmp_path)
    with pytest.raises(ValueError):
        build_report(path, '2026-13-01')
    assert build_report(tmp_path / 'missing.db', ds[19])['missing'] == ['本地行情数据库尚未准备好']
    assert any('2026-02-01：不足完整20个交易日' in text for text in build_report(path, '2026-02-01')['missing'])


def test_old_holding_not_new_buy(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.execute('DELETE FROM structure_monitor_events')
        c.commit()
    r = build_report(path, ds[19])
    assert r['counts']['holding'] == 1 and r['counts']['buy_confirm'] == 0
    assert r['counts']['existing_holding'] == 1
    assert '此前确认' in r['stocks'][0]['analysis']


def test_capacity_is_blocked_not_silently_truncated(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.executemany('INSERT INTO quant_preselect_results VALUES (?,?,?)',
                      [('mrgc_v1', ds[19], f'{n:06d}') for n in range(301)])
        c.commit()
    report = build_report(path, ds[19])
    assert report['status'] == 'BLOCKED' and report['stocks'] == []
    assert '成员超过300只报告容量，暂停生成以防遗漏' in report['missing']


def test_quality_failure_blocks_and_removes_action_language(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.execute('UPDATE data_quality_report SET status="FAILED" WHERE trade_date=?', (ds[19],))
        c.commit()
    report = build_report(path, ds[19])
    assert report['status'] == 'BLOCKED'
    assert report['stocks'][0]['analysis'] == '数据不完整，暂停机会判断'
    assert report['stocks'][0]['events'][0]['event_type'] == 'BUY_CONFIRM'


def test_optional_lifecycle_missing_keeps_trusted_c_report_ready(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.execute('DELETE FROM stock_lifecycle_state WHERE trade_date=?', (ds[19],))
        c.commit()
    report = build_report(path, ds[19])
    assert report['status'] == 'READY' and report['missing'] == []
    assert '600001：生命周期背景指标缺失，RPS / 结构分数暂不评价' in report['warnings']
    assert report['stocks'][0]['structure_score'] is None
    assert report['stocks'][0]['data_status'] == 'OK'
    assert '背景指标缺失' in report['stocks'][0]['analysis']


def test_mail_matches_ai_notes_and_has_prices_link_and_warning(tmp_path):
    path, ds = seed(tmp_path)
    report = build_report(path, ds[19])
    report['ai'] = dict(summary=['<AI结论>'], sector_notes={'机械': '行业说明'},
                        stock_notes={'600001': '股票说明'})
    report['warnings'] = ['背景缺失']
    mail = render_email(report)
    for text in ['&lt;AI结论&gt;', '行业说明', '股票说明', '背景缺失', '收盘', '涨跌幅', '失效线',
                 '今日新增买入确认', '查看个股与信号', 'trade_date=2026-01-20']:
        assert text in mail
    assert '<AI结论>' not in mail


def test_three_groups_union_overlap_and_current_membership(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.executemany('INSERT INTO stock_pool_members VALUES (?,?)',
                      [('pool_0d1aea597dc4', '600001'), ('watchlist', '600001')])
        c.commit()
    report = build_report(path, ds[19])
    assert report['status'] == 'READY'
    assert report['counts']['total'] == 1
    assert report['stocks'][0]['group_ids'] == ['mrgc', 'company', 'watchlist']
    assert report['stocks'][0]['overlap_count'] == 3
    assert [g['count'] for g in report['groups']] == [1, 1, 1]
    assert report['groups'][1]['membership_as_of'] == 'current'
    mail = render_email(report)
    assert '当前成员（历史日期不代表历史成员）' in mail
    assert '/a/stock/600001?trade_date=' in mail


def test_missing_mrgc_window_does_not_block_company_core_facts(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.execute('DELETE FROM strategy_run_log WHERE trade_date=?', (ds[2],))
        c.execute('INSERT INTO stock_pool_members VALUES (?,?)', ('pool_0d1aea597dc4', '600001'))
        c.commit()
    report = build_report(path, ds[19])
    assert report['status'] == 'BLOCKED' and report['partial_usable']
    assert report['groups'][0]['status'] == 'BLOCKED'
    assert report['groups'][1]['status'] == 'READY'
    assert report['groups'][1]['opportunity_counts']['c_only_buy'] == 1
    assert report['stocks'][0]['data_status'] == 'OK'


def test_company_unique_name_fallback_and_ambiguous_rejected(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.execute('UPDATE stock_pools SET pool_id="custom_company" WHERE name="公司"')
        c.execute('INSERT INTO stock_pool_members VALUES (?,?)', ('custom_company', '600001'))
        c.commit()
    assert build_report(path, ds[19])['groups'][1]['pool_id'] == 'custom_company'
    with closing(sqlite3.connect(path)) as c:
        c.execute('INSERT INTO stock_pools VALUES ("duplicate", "公司")')
        c.commit()
    report = build_report(path, ds[19])
    assert report['groups'][1]['status'] == 'BLOCKED'
    assert report['groups'][1]['symbols'] == []


def test_resonance_direction_conflict_and_old_chan_not_new_opportunity():
    from quant_engine.watch.mrgc_daily_report import _opportunities
    events = [dict(event_type='BUY_CONFIRM', reason='C真实确认')]
    chan = dict(status='READY', recent=[dict(direction='buy', label='1买候选',
                confirmation_date='2026-01-19', pivot_date='2026-01-15')])
    opportunities = _opportunities(events, chan, '2026-01-20')
    assert opportunities[0]['level'] == 'resonance'
    assert 'ZIG会回绘' in opportunities[0]['evidence'][1]
    assert _opportunities([], chan, '2026-01-20') == []
    chan['recent'].append(dict(direction='sell', label='1卖候选',
        confirmation_date='2026-01-20', pivot_date='2026-01-18'))
    opportunities = _opportunities(events, chan, '2026-01-20')
    assert all(o['conflict'] for o in opportunities)
    assert not any(o['level'] == 'resonance' for o in opportunities)
    assert {o['source'] for o in opportunities} == {'C', 'CHAN'}


def test_blocked_stock_c_events_preserved_but_not_counted_as_opportunity(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.execute('UPDATE data_quality_report SET status="FAILED" WHERE trade_date=?', (ds[19],))
        c.commit()
    report = build_report(path, ds[19])
    assert report['stocks'][0]['events'][0]['event_type'] == 'BUY_CONFIRM'
    assert report['stocks'][0]['opportunities'] == []
    assert report['opportunity_counts']['buy'] == report['counts']['buy_confirm'] == 0


def test_chan_call_uses_ascending_market_window_and_preserves_raw_c_events(tmp_path, monkeypatch):
    from quant_engine.watch import chan_daily_confirmation
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        for column in ('open', 'high', 'low'):
            c.execute(f'ALTER TABLE stock_daily_prices ADD COLUMN {column} REAL')
        c.execute('UPDATE stock_daily_prices SET open=close, high=close+1, low=close-1')
        c.commit()
    def confirm(rows, target_date, recent_dates=None):
        assert recent_dates == ds[17:20]
        assert rows[-1]['trade_date'] == target_date
        signal = dict(direction='buy', label='1买候选', pivot_date=ds[16],
                      confirmation_date=ds[18], candidate=True, repainting=True)
        return dict(status='READY', recent=[signal], today=[], source={'formula': 'fixture'})
    monkeypatch.setattr(chan_daily_confirmation, 'confirm_daily', confirm)
    report = build_report(path, ds[19])
    stock = report['stocks'][0]
    assert stock['events'] == [dict(event_type='BUY_CONFIRM', reason='<确认>', price=10)]
    assert stock['opportunities'][0]['level'] == 'resonance'
    assert stock['chan_signals'][0]['pivot_date'] == ds[16]
    assert report['opportunity_counts']['resonance_buy'] == 1


def test_sqlite_transaction_closed_before_chan_cpu_replay(tmp_path, monkeypatch):
    from quant_engine.watch import mrgc_daily_report as module, chan_daily_confirmation
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        for column in ('open', 'high', 'low'):
            c.execute(f'ALTER TABLE stock_daily_prices ADD COLUMN {column} REAL')
        c.execute('UPDATE stock_daily_prices SET open=close,high=close+1,low=close-1')
        c.commit()
    connections = []
    original = sqlite3.connect
    class TrackedConnection(sqlite3.Connection):
        closed = False
        def close(self):
            self.closed = True
            super().close()
    def connect(*args, **kwargs):
        connection = original(*args, factory=TrackedConnection, **kwargs)
        connections.append(connection)
        return connection
    def confirm(rows, target_date, recent_dates=None):
        assert connections and all(connection.closed for connection in connections)
        return dict(status='READY', recent=[], today=[], source={})
    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    monkeypatch.setattr(chan_daily_confirmation, 'confirm_daily', confirm)
    assert build_report(path, ds[19])['status'] == 'READY'


def test_frozen_groups_ignore_changed_current_members(tmp_path):
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        c.execute('INSERT INTO stock_pool_members VALUES (?,?)', ('watchlist', '600099'))
        c.commit()
    report = build_report(path, ds[19], frozen_groups=[
        dict(id='company', pool_id='saved_company', symbols=['600001']),
        dict(id='watchlist', pool_id='watchlist', symbols=[])], compute_chan=False)
    assert report['groups'][1]['symbols'] == ['600001']
    assert report['groups'][1]['pool_id'] == 'saved_company'
    assert report['groups'][1]['membership_as_of'] == 'generation_snapshot'
    assert report['groups'][2]['symbols'] == []
    assert report['counts']['total'] == 1
    assert not any('缠论候选暂不可评' in text for text in report['warnings'])


def test_history_c_results_override_formal_snapshots_and_events(tmp_path):
    path, ds = seed(tmp_path)
    report = build_report(path, ds[19], history_results=(
        {'600001': dict(data_status='OK', state_json='{"status":"WATCHING"}')},
        {'600001': [dict(event_type='EXIT', reason='历史独立退出', price=10)]}), compute_chan=False)
    assert report['stocks'][0]['status'] == 'WATCHING'
    assert report['counts']['exit'] == 1 and report['counts']['buy_confirm'] == 0


def test_missing_c_still_computes_trusted_chan_and_has_one_missing_item(tmp_path, monkeypatch):
    from quant_engine.watch import chan_daily_confirmation
    path, ds = seed(tmp_path)
    with closing(sqlite3.connect(path)) as c:
        for column in ('open', 'high', 'low'):
            c.execute(f'ALTER TABLE stock_daily_prices ADD COLUMN {column} REAL')
        c.execute('UPDATE stock_daily_prices SET open=close,high=close+1,low=close-1')
        c.execute('DELETE FROM structure_monitor_snapshots')
        c.execute('INSERT INTO stock_pool_members VALUES (?,?)', ('watchlist', '600001'))
        c.commit()
    calls = []
    def confirm(rows, target_date, recent_dates=None):
        calls.append(rows)
        return dict(status='READY', recent=[], today=[])
    monkeypatch.setattr(chan_daily_confirmation, 'confirm_daily', confirm)
    report = build_report(path, ds[19])
    assert calls and report['stocks'][0]['chan']['status'] == 'READY'
    assert not any('历史行情不足' in message for message in report['warnings'])
    assert len(report['missing']) == 1 and '尚未计算' in report['missing'][0]


def test_fifty_seven_missing_c_results_are_not_duplicated_across_groups(tmp_path):
    path, ds = seed(tmp_path)
    symbols = [f'{600100 + n:06d}' for n in range(57)]
    with closing(sqlite3.connect(path)) as c:
        c.execute('DELETE FROM quant_preselect_results')
        for symbol in symbols:
            c.execute('INSERT INTO quant_preselect_results VALUES (?,?,?)', ('mrgc_v1', ds[19], symbol))
            c.executemany('INSERT INTO stock_daily_prices VALUES (?,?,?)',
                          [(symbol, ds[18], 10), (symbol, ds[19], 11)])
            c.execute('INSERT INTO stock_pool_members VALUES (?,?)', ('watchlist', symbol))
        c.commit()
    report = build_report(path, ds[19], compute_chan=False)
    assert report['counts']['total'] == 57
    assert len(report['missing']) == 57
    assert all('尚未计算' in message for message in report['missing'])
    assert len(report['groups'][0]['missing']) == len(report['groups'][2]['missing']) == 57


def test_failed_history_c_result_keeps_explicit_quality_reason(tmp_path):
    path, ds = seed(tmp_path)
    report = build_report(path, ds[19], history_results=(
        {'600001': dict(data_status='BLOCKED', reason='invalid_ohlcv', state_json='{}')},
        {}), compute_chan=False)
    assert report['missing'] == [f'600001 {ds[19]}：C 类质量校验未通过：invalid_ohlcv']
    assert report['stocks'][0]['opportunities'] == []


def test_frozen_mrgc_members_cannot_override_historical_hits(tmp_path):
    path, ds = seed(tmp_path)
    report = build_report(path, ds[19], frozen_groups=[
        dict(id='mrgc', pool_id='fake', symbols=['600002'])], compute_chan=False)
    assert report['groups'][0]['symbols'] == ['600001']



def test_regeneration_updates_mrgc_members_after_historical_screen_repair(tmp_path):
    path, ds = seed(tmp_path)
    frozen = build_report(path, ds[19], compute_chan=False)['groups']
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM quant_preselect_results')
        conn.execute('INSERT INTO quant_preselect_results VALUES (?,?,?)', ('mrgc_v1', ds[19], '600002'))
    report = build_report(path, ds[19], frozen_groups=frozen, compute_chan=False)
    assert report['groups'][0]['symbols'] == ['600002']
    assert report['groups'][0]['membership_as_of'] == 'historical_20d'
