"""Period replay preserves causal confirmations and complete marker timestamps."""
from datetime import datetime, timedelta

import pytest

from quant_engine.research.structure_turning import evaluate_structure_turning
from tests.test_structure_turning import recovery


def minute_bars():
    rows = recovery()
    for index, row in enumerate(rows):
        row['trade_date'] = (datetime(2026, 1, 5, 9, 30) +
                             timedelta(minutes=30 * index)).strftime('%Y-%m-%d %H:%M')
    return rows


@pytest.mark.parametrize('period', ['30min', '120min'])
def test_minute_markers_preserve_timestamps_and_causality(period):
    rows = minute_bars()
    full = evaluate_structure_turning(rows, period=period)
    assert full['period'] == period
    assert full['window_unit'] == 'bars'
    assert full['chart_only'] is True
    assert any(marker['marker_type'] == 'BUY_CONFIRM' for marker in full['markers'])
    assert all(len(marker['trade_date']) == 16 for marker in full['markers'])
    for size in range(25, len(rows)):
        partial = evaluate_structure_turning(rows[:size], period=period)
        assert partial['markers'] == [marker for marker in full['markers']
                                     if marker['trade_date'] <= rows[size - 1]['trade_date']]
    assert '前日' not in str(full)
    assert '两日' not in str(full)
    assert '日K' not in str(full)


@pytest.mark.parametrize('period', ['weekly', 'monthly'])
def test_calendar_period_preserves_shape_and_has_bar_warmup(period):
    daily = evaluate_structure_turning(recovery())
    replay = evaluate_structure_turning(recovery(), period=period)
    assert [m['marker_type'] for m in replay['markers']] == [
        m['marker_type'] for m in daily['markers']]
    assert replay['config_fingerprint'] != daily['config_fingerprint']
    assert '根有效K线' in evaluate_structure_turning(recovery()[:10], period=period)[
        'state']['waiting_for'][0]


@pytest.mark.parametrize('stamp', ['2026-01-05', '2026-01-05 9:30',
                                    '2026-01-05 09:30+08:00', 'invalid'])
def test_minute_invalid_timestamp_is_rejected(stamp):
    rows = minute_bars()[:1]
    rows[0]['trade_date'] = stamp
    with pytest.raises(ValueError):
        evaluate_structure_turning(rows, period='30min')


def test_unknown_period_and_duplicate_minutes_rejected():
    with pytest.raises(ValueError):
        evaluate_structure_turning([], period='hourly')
    rows = minute_bars()[:2]
    rows[1]['trade_date'] = rows[0]['trade_date']
    with pytest.raises(ValueError):
        evaluate_structure_turning(rows, period='30min')


def test_daily_default_contract_unchanged_and_minute_not_silently_accepted():
    assert evaluate_structure_turning(recovery()) == evaluate_structure_turning(
        recovery(), period='daily')
    with pytest.raises(ValueError):
        evaluate_structure_turning(minute_bars())


def test_non_daily_replay_bound_and_empty_state():
    with pytest.raises(ValueError, match='520'):
        evaluate_structure_turning([{}] * 521, period='monthly')
    result = evaluate_structure_turning([], period='120min')
    assert result['markers'] == []
    assert result['state']['trade_date'] is None
    assert result['summary']['status'] == 'insufficient_history'


def test_minute_right_confirmation_stays_after_pivot():
    result = evaluate_structure_turning(minute_bars(),
                                        {'enable_divergence': False}, period='30min')
    observed = [marker for marker in result['markers']
                if marker['marker_type'] == 'OBSERVE']
    assert observed
    for marker in observed:
        pivots = marker['factors']['pivot_lows']
        assert all(pivot['trade_date'] < pivot['confirmed_date'] <= marker['trade_date']
                   for pivot in pivots)
