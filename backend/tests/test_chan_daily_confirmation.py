"""Fixed TS formula parity, first-visibility semantics and input guards."""
from datetime import date, timedelta
import copy
import pytest
from quant_engine.watch import chan_daily_confirmation as chan

# Exported from real buildChanBuySellPoints Zhihu, 2026-10-03. No Node process in production.
CLOSE = [100] * 60 + [110,125,130,120,110,95,80,85,95,105,115,125,120,110,100,90,
                       80,85,100,115,130,120,100,90,80]
TS_EXPECTED = [
    ('2026-03-05', '2026-03-05', 124.63000000000001, '[1 卖]', 'sell'),
    ('2026-03-09', '2026-03-09', 81.48, '[1 买]', 'buy'),
    ('2026-03-14', '2026-03-14', 124.63000000000001, '[1 卖]', 'sell'),
    ('2026-03-19', '2026-03-19', 81.48, '[1 买]', 'buy'),
    ('2026-03-23', '2026-03-23', 124.63000000000001, '[1 卖]', 'sell'),
]


def make_rows(close=CLOSE):
    return [{'trade_date': (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
             'open': c, 'close': c, 'low': c-1, 'high': c+1} for i, c in enumerate(close)]


def test_real_ts_formula_parity():
    rows = make_rows()
    bars = tuple((r['trade_date'], r['open'], r['high'], r['low'], r['close']) for r in rows)
    assert chan._confirmed(bars) == tuple(TS_EXPECTED)
    result = chan.confirm_daily(rows, '2026-03-26')
    assert result['status'] == 'READY'
    assert result['source']['baseline_date'] == '2026-03-01'
    assert result['source']['loaded_window_start'] == '2026-01-01'
    assert '[3 买]' not in [event[3] for event in chan._confirmed(bars)]


def test_future_append_cannot_change_past_and_returns_no_shared_objects():
    rows = make_rows()
    target = rows[67]['trade_date']
    before = chan.confirm_daily(rows[:68], target)
    after = chan.confirm_daily(rows, target)
    assert before == after
    assert before['today'][0]['confirmation_date'] == target
    before['today'][0]['label'] = 'changed'
    assert chan.confirm_daily(rows, target) == after


def test_recent_uses_report_calendar_and_warmup_never_alerts():
    rows = make_rows()
    baseline = chan.confirm_daily(rows[:60], rows[59]['trade_date'])
    assert baseline['today'] == baseline['recent'] == []
    target = rows[68]['trade_date']
    actual = chan.confirm_daily(rows, target, [rows[66]['trade_date'], rows[67]['trade_date'], target])
    assert actual['recent'][0]['label'] == '[1 买]'
    assert all(event['candidate'] and event['repainting'] for event in actual['recent'])
    assert chan.confirm_daily(rows, target, [target, target])['status'] == 'BLOCKED'


@pytest.mark.parametrize('case', ['empty','duplicate','nan','range','missing','short','oversized'])
def test_invalid_data_never_fabricates_candidates(case):
    rows = make_rows()
    target = rows[-1]['trade_date']
    if case == 'empty': rows = []
    if case == 'duplicate': rows[61]['trade_date'] = rows[60]['trade_date']
    if case == 'nan': rows[61]['close'] = float('nan')
    if case == 'range': rows[61]['low'] = 999
    if case == 'missing': rows.pop()
    if case == 'short': rows = rows[:30]; target = rows[-1]['trade_date']
    if case == 'oversized': rows = make_rows([100] * 521); target = rows[-1]['trade_date']
    result = chan.confirm_daily(rows, target)
    assert result['status'] == 'BLOCKED'
    assert result['today'] == result['recent'] == []


def test_historical_plot_date_does_not_become_confirmation_date(monkeypatch):
    rows = make_rows()
    chan._confirmed.cache_clear()
    def points(bars):
        if len(bars) == 60:
            return [(bars[1][0], '[1 买]', 'buy', 97)]
        return [(bars[1][0], '[1 买]', 'buy', 97),
                (bars[5][0], '[2 买]', 'buy', 98)]
    monkeypatch.setattr(chan, '_points', points)
    result = chan.confirm_daily(rows[:61], rows[60]['trade_date'])
    assert len(result['today']) == 1
    assert result['today'][0]['pivot_date'] == rows[5]['trade_date']
    assert result['today'][0]['confirmation_date'] == rows[60]['trade_date']
    chan._confirmed.cache_clear()


def test_ts_seeded_fixture_all_five_formula_labels():
    # JS fixture: seed=12345; s=(imul(s,1664525)+1013904223)>>>0;
    # c=Math.round(c*(.85+(s/4294967296)*.3)*100)/100, 100 bars.
    import math
    seed, close, prices = 12345, 100, []
    for _ in range(100):
        seed = (seed * 1664525 + 1013904223) & 0xffffffff
        close = math.floor(close * (.85 + seed / 4294967296 * .3) * 100 + .5) / 100
        prices.append(close)
    rows = make_rows(prices)
    bars = tuple((r['trade_date'], r['open'], r['high'], r['low'], r['close']) for r in rows)
    events = chan._confirmed(bars)
    assert [(r[1], r[3], r[4]) for r in events] == [
        ('2026-03-02','[1 卖]','sell'), ('2026-03-05','[1 卖]','sell'),
        ('2026-03-09','[类 2 买]','buy'), ('2026-03-11','[1 卖]','sell'),
        ('2026-03-14','[1 买]','buy'), ('2026-03-17','[2 卖]','sell'),
        ('2026-03-19','[2 买]','buy'), ('2026-03-21','[1 卖]','sell'),
        ('2026-03-28','[1 卖]','sell'), ('2026-03-31','[1 卖]','sell'),
        ('2026-04-04','[类 2 买]','buy'), ('2026-04-06','[1 卖]','sell'),
        ('2026-04-08','[1 买]','buy'), ('2026-04-10','[2 卖]','sell')]
    assert events[2][2] == pytest.approx(132.8221)
    assert events[6][2] == pytest.approx(133.9182)


def test_concurrent_cold_requests_compute_once(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import time
    chan._confirmed.cache_clear()
    original = chan._points
    calls = []
    def slow_points(bars):
        calls.append(len(bars))
        if len(bars) == 60:
            time.sleep(0.03)
        return original(bars)
    monkeypatch.setattr(chan, '_points', slow_points)
    rows = make_rows()[:65]
    gate = Barrier(4)
    def run():
        gate.wait(timeout=2)
        return chan.confirm_daily(rows, rows[-1]['trade_date'])
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: run(), range(4)))
    assert all(result == results[0] for result in results)
    assert calls == list(range(60, 66))
    assert chan._confirmed.cache_info().misses == 1
    assert chan._confirmed.cache_info().hits == 3
    chan._confirmed.cache_clear()
