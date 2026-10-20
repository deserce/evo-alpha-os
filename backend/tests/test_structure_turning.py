"""Causal, isolated structure strategy research contract."""
from datetime import date, timedelta
import pytest
from quant_engine.research.structure_turning import evaluate_structure_turning


def bars(closes):
    return [dict(trade_date=str(date(2026, 1, 1) + timedelta(days=i)),
                 open=c, high=c + .35, low=c - .35, close=c, volume=1000)
            for i, c in enumerate(closes)]


def recovery():
    return bars([30 - i * .25 for i in range(30)] +
                [22, 21, 20, 20.5, 21, 22, 23, 22, 21.5, 21, 21.5, 22,
                 23, 24, 25, 26, 26, 25, 24, 23, 22, 21, 20])


def test_recovery_has_confirmed_structure_and_exit():
    result = evaluate_structure_turning(recovery())
    kinds = [m['marker_type'] for m in result['markers']]
    assert 'OBSERVE' in kinds
    assert 'BUY_CONFIRM' in kinds
    assert 'EXIT' in kinds
    assert result['trades'][0]['exit_date'] > result['trades'][0]['entry_date']
    assert result['trades'][0]['mfe_pct'] >= 0
    assert result['summary']['closed_trades'] >= 1


def test_falling_prices_never_buy():
    result = evaluate_structure_turning(bars([100 - i * .5 for i in range(100)]))
    assert not any(m['marker_type'] == 'BUY_CONFIRM' for m in result['markers'])


def test_future_bars_never_change_prefix_markers():
    prices = recovery()
    complete = evaluate_structure_turning(prices)
    for length in range(25, len(prices)):
        partial = evaluate_structure_turning(prices[:length])
        assert [t for t in partial['trades'] if t['status'] == 'CLOSED'] == [
            t for t in complete['trades'] if t['exit_date'] and
            t['exit_date'] <= prices[length - 1]['trade_date']]
        assert partial['markers'] == [m for m in complete['markers']
                                      if m['trade_date'] <= prices[length - 1]['trade_date']]


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, 0])
def test_bad_prices_fail_explicitly(bad):
    prices = bars([20] * 30)
    prices[-1]['close'] = bad
    with pytest.raises(ValueError):
        evaluate_structure_turning(prices)


def test_short_history_returns_no_signals():
    result = evaluate_structure_turning(bars([20] * 10))
    assert result['markers'] == []
    assert result['summary']['status'] == 'insufficient_history'


def test_duplicate_date_and_bad_ohlc_rejected():
    prices = bars([20] * 30)
    prices[-1]['trade_date'] = prices[-2]['trade_date']
    with pytest.raises(ValueError):
        evaluate_structure_turning(prices)
    prices = bars([20] * 30)
    prices[-1]['low'] = 21
    with pytest.raises(ValueError):
        evaluate_structure_turning(prices)


def test_resource_limit_and_unknown_config_rejected():
    with pytest.raises(ValueError):
        evaluate_structure_turning(bars([20] * 3001))
    with pytest.raises(ValueError):
        evaluate_structure_turning([], {'unexpected': 1})


def test_open_trade_is_not_reported_as_realized_return():
    result = evaluate_structure_turning(recovery()[:45])
    assert result['summary']['open_trades'] == 1
    trade = result['trades'][0]
    assert trade['status'] == 'OPEN'
    assert trade['exit_date'] is None and trade['return_pct'] is None
    assert trade['holding_bars'] > 0
    assert 'mark_to_market_pct' in trade


def test_confirmations_follow_observation_without_backdating():
    result = evaluate_structure_turning(recovery(), {'enable_divergence': False})
    observe = next(m for m in result['markers'] if m['marker_type'] == 'OBSERVE')
    confirm = next(m for m in result['markers'] if m['marker_type'] == 'BUY_CONFIRM')
    assert observe['trade_date'] < confirm['trade_date']
    assert confirm['stop_price'] < confirm['price']


def test_invalid_configuration_and_volume():
    for config in ({'pivot_right': 0}, {'atr_period': True},
                   {'support_tolerance_atr': float('nan')}, {'setup_window': 101}):
        with pytest.raises(ValueError):
            evaluate_structure_turning([], config)
    prices = recovery()
    prices[-1]['volume'] = -1
    with pytest.raises(ValueError):
        evaluate_structure_turning(prices)


def test_boolean_prices_and_nonpositive_stop_are_rejected():
    prices = recovery()
    prices[-1]['close'] = True
    with pytest.raises(ValueError):
        evaluate_structure_turning(prices)
    result = evaluate_structure_turning(recovery(), {'stop_buffer_atr': 10})
    assert all(m['stop_price'] > 0 for m in result['markers'])


def test_active_observation_is_not_overwritten_by_new_pivots():
    result = evaluate_structure_turning(recovery(), {'max_entry_risk_atr': .01})
    active = False
    for marker in result['markers']:
        if marker['marker_type'] == 'OBSERVE':
            assert not active
            active = True
        elif marker['marker_type'] in ('BUY_CONFIRM', 'INVALIDATED'):
            active = False


def test_large_atr_cannot_create_negative_stop():
    prices = recovery()
    for row in prices:
        row['high'] = 500
    result = evaluate_structure_turning(prices)
    assert result['markers'] == []


def test_replacement_observation_explicitly_invalidates_prior_setup():
    import math
    prices = bars([20 + math.sin(i) for i in range(80)])
    result = evaluate_structure_turning(prices, {'max_entry_risk_atr': .01})
    assert any('替代旧观察' in m['reason'] for m in result['markers'])
    active = False
    for marker in result['markers']:
        if marker['marker_type'] == 'OBSERVE':
            assert not active
            active = True
        elif marker['marker_type'] in ('BUY_CONFIRM', 'INVALIDATED'):
            active = False


def test_explanation_matches_confirmation_thresholds_and_dates():
    result = evaluate_structure_turning(recovery(), {'enable_divergence': False})
    observation = next(m for m in result['markers'] if m['marker_type'] == 'OBSERVE')
    confirm = next(m for m in result['markers'] if m['marker_type'] == 'BUY_CONFIRM')
    factors = confirm['factors']
    assert len(factors['pivot_lows']) == 2
    assert all(p['trade_date'] < p['confirmed_date'] <= observation['trade_date']
               for p in factors['pivot_lows'])
    assert confirm['price'] > factors['ma5']
    assert confirm['price'] > factors['previous_high']
    assert factors['macd_hist'] > factors['macd_hist_previous']
    assert 0 < factors['entry_risk_atr'] <= factors['thresholds']['max_entry_risk_atr']
    assert factors['stop_price'] == confirm['stop_price']
    assert factors['setup_age_bars'] > 0
    assert confirm['setup_id'] == observation['setup_id']
    assert observation['trade_id'] is None
    assert confirm['trade_id'] == result['trades'][0]['trade_id']


def test_latest_state_tracks_observation_holding_and_exit():
    prices = recovery()
    result = evaluate_structure_turning(prices)
    observe = next(m for m in result['markers'] if m['marker_type'] == 'OBSERVE')
    confirm = next(m for m in result['markers'] if m['marker_type'] == 'BUY_CONFIRM')
    exit_marker = next(m for m in result['markers'] if m['marker_type'] == 'EXIT')
    expected = [(observe, 'OBSERVING'), (confirm, 'HOLDING'), (exit_marker, 'WATCHING')]
    for marker, status in expected:
        prefix = [r for r in prices if r['trade_date'] <= marker['trade_date']]
        state = evaluate_structure_turning(prefix)['state']
        assert state['status'] == status
        assert state['trade_date'] == marker['trade_date']
        assert state['waiting_for']
        if status == 'HOLDING':
            assert state['entry_price'] == confirm['price']
            assert state['entry_date'] == confirm['trade_date']
            assert state['trade_id'] == confirm['trade_id']
            assert state['stop_price'] == confirm['stop_price']
        elif status == 'WATCHING':
            assert state['trade_id'] is None
            assert state['entry_price'] is None
            assert state['stop_price'] is None
    assert exit_marker['setup_id'] == confirm['setup_id']
    assert exit_marker['trade_id'] == confirm['trade_id']
    assert exit_marker['factors']['weak_days'] >= 2 or (
        exit_marker['price'] < exit_marker['stop_price'])


def test_short_history_state_does_not_invent_factors():
    result = evaluate_structure_turning(bars([20] * 10))
    assert result['state']['status'] == 'WATCHING'
    assert result['state']['factors'] == {}
    assert result['state']['trade_id'] is None
    assert evaluate_structure_turning([])['state']['trade_date'] is None


def test_observation_explains_fixed_stop_and_support_floor():
    result = evaluate_structure_turning(recovery(), {'enable_divergence': False})
    marker = next(m for m in result['markers'] if m['marker_type'] == 'OBSERVE')
    factors = marker['factors']
    first, second = factors['pivot_lows']
    assert second['price'] >= factors['support_floor']
    assert factors['support_floor'] == pytest.approx(
        first['price'] - factors['thresholds']['support_tolerance_atr'] * factors['setup_atr'])
    assert marker['stop_price'] == pytest.approx(
        second['price'] - factors['thresholds']['stop_buffer_atr'] * factors['setup_atr'],
        abs=1e-6)


def test_observation_state_waits_for_next_bar_and_ids_are_repeatable():
    prices = recovery()
    result = evaluate_structure_turning(prices, {'enable_divergence': False})
    observe = next(m for m in result['markers'] if m['marker_type'] == 'OBSERVE')
    prefix = [r for r in prices if r['trade_date'] <= observe['trade_date']]
    state = evaluate_structure_turning(prefix, {'enable_divergence': False})['state']
    assert '观察后至少等待下一根日K线' in state['waiting_for']
    assert state['setup_id'] == observe['setup_id']
    assert evaluate_structure_turning(prices, {'enable_divergence': False}) == result


def test_factor_output_is_json_serializable_and_has_no_mutable_trade_alias():
    import json
    result = evaluate_structure_turning(recovery())
    encoded = json.dumps(result, allow_nan=False)
    assert encoded
    confirm = next(m for m in result['markers'] if m['marker_type'] == 'BUY_CONFIRM')
    original = confirm['factors']['pivot_lows'][0]['price']
    result['trades'][0]['pivot_lows'][0]['price'] = -100
    assert confirm['factors']['pivot_lows'][0]['price'] == original


def divergence_reversal_bars():
    prices = bars([100] * 30 + [100 - 2 * i for i in range(1, 16)] +
                  [69, 68, 67, 66, 65, 64, 63, 62, 61, 60, 60.5])
    prices[-1].update(open=59.5, low=59.0, high=61.0, close=60.5, volume=1200)
    return prices


def test_v2_early_reversal_requires_divergence_and_independent_confirmation():
    from quant_engine.research.structure_turning import VERSION
    result = evaluate_structure_turning(divergence_reversal_bars())
    assert result['version'] == VERSION == 'structure_turning_experimental_v2'
    buy = next(m for m in result['markers'] if m['marker_type'] == 'BUY_CONFIRM')
    assert buy['setup_path'] == 'DIVERGENCE_REVERSAL'
    evidence = buy['factors']['divergence']
    assert evidence['bottom_hist_divergence']
    assert evidence['price_stabilizing'] and evidence['oscillator_improving']
    assert buy['factors']['checks']
    assert result['state']['setup_path'] == 'DIVERGENCE_REVERSAL'
    assert result['config_fingerprint'] in buy['setup_id']


def test_shrinking_negative_histogram_without_price_stabilization_does_not_buy():
    prices = divergence_reversal_bars()[:-1]
    result = evaluate_structure_turning(prices)
    assert not any(m['marker_type'] == 'BUY_CONFIRM' for m in result['markers'])


def test_ordinary_recovery_without_new_price_low_is_not_divergence():
    prices = divergence_reversal_bars()
    prices[-1].update(open=60, low=59.9, close=60.5, high=61)
    result = evaluate_structure_turning(prices)
    assert not result['state']['factors']['divergence']['bottom_hist_divergence']


def test_v2_extended_future_prefix_preserves_events_and_closed_trades():
    prices = divergence_reversal_bars()
    extra = bars([62, 65, 70, 75, 78, 80, 82, 83, 84, 84.5, 84.7, 84.8,
                  84.9, 85, 85.1, 83, 80, 75])
    for index, row in enumerate(extra, len(prices)):
        row['trade_date'] = str(date(2026, 1, 1) + timedelta(days=index))
    prices += extra
    full = evaluate_structure_turning(prices)
    for length in range(45, len(prices)):
        cutoff = prices[length-1]['trade_date']
        partial = evaluate_structure_turning(prices[:length])
        assert partial['markers'] == [m for m in full['markers'] if m['trade_date'] <= cutoff]
        assert [t for t in partial['trades'] if t['status'] == 'CLOSED'] == [
            t for t in full['trades'] if t['exit_date'] and t['exit_date'] <= cutoff]


def top_divergence_bars():
    prices = divergence_reversal_bars()
    extra = bars([62, 65, 70, 75, 78, 80, 82, 83, 84, 84.5, 84.7, 84.8,
                  84.9, 85, 85.1, 83, 80, 75])
    for index, row in enumerate(extra, len(prices)):
        row['trade_date'] = str(date(2026, 1, 1) + timedelta(days=index))
    return prices + extra


def test_top_divergence_warns_without_selling_until_price_breakdown():
    prices = top_divergence_bars()
    result = evaluate_structure_turning(prices)
    warning = next(m for m in result['markers'] if m['marker_type'] == 'RISK_WARNING')
    assert warning['factors']['divergence']['top_hist_divergence']
    prefix = [row for row in prices if row['trade_date'] <= warning['trade_date']]
    partial = evaluate_structure_turning(prefix)
    assert partial['state']['status'] == 'HOLDING'
    assert not any(m['marker_type'] == 'EXIT' for m in partial['markers'])
    checks = warning['factors']['checks']
    assert any(c['dimension'] == 'warning' and c['passed'] for c in checks)
    assert not any(c['dimension'] == 'exit' and c['passed'] for c in checks)
    exit_marker = next(m for m in result['markers'] if m['marker_type'] == 'EXIT')
    assert exit_marker['trade_date'] > warning['trade_date']
    assert '顶背离预警后' in exit_marker['reason']
    assert exit_marker['factors']['top_warning_active']
    assert exit_marker['factors']['weak_days'] < 2
    assert exit_marker['price'] > exit_marker['stop_price']
    assert any(c['dimension'] == 'exit' and c['passed'] for c in exit_marker['factors']['checks'])


def test_disabled_top_divergence_does_not_emit_top_warning_or_early_exit():
    prices = top_divergence_bars()
    enabled = evaluate_structure_turning(prices)
    disabled = evaluate_structure_turning(prices, {'enable_divergence_exit': False})
    assert not any('顶背离' in m['reason'] for m in disabled['markers'])
    enabled_exit = next(m for m in enabled['markers'] if m['marker_type'] == 'EXIT')
    disabled_exit = next(m for m in disabled['markers'] if m['marker_type'] == 'EXIT')
    assert disabled_exit['trade_date'] > enabled_exit['trade_date']


def test_divergence_pending_requirements_match_evidence_and_are_json_safe():
    prices = divergence_reversal_bars()
    prices[-1].update(open=60.6)
    state = evaluate_structure_turning(prices[:-1])['state']
    assert state['setup_path'] == 'DIVERGENCE_REVERSAL'
    assert any('止跌' in requirement for requirement in state['waiting_for'])
    assert not any('MA5' in requirement or '下一根' in requirement for requirement in state['waiting_for'])
    full = evaluate_structure_turning(divergence_reversal_bars())
    buy = next(m for m in full['markers'] if m['marker_type'] == 'BUY_CONFIRM')
    assert all(check['passed'] for check in buy['factors']['checks'])
    assert all(isinstance(check['value'], (str, int, float)) for check in buy['factors']['checks'])
