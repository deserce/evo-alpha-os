"""Independent synthetic tests for chronology, observability and portfolio accounting."""
from __future__ import annotations

import copy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('mrgc_analysis', Path(__file__).with_name('analyze.py'))
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


def fixture(n=35, symbols=('600001',)):
    start = date(2026, 8, 3)
    days = []
    current = start
    while len(days) < n:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    prices = {
        symbol: {d: dict(open=10.0, high=10.2, low=9.8, close=10.0, volume=100.0)
                 for d in days}
        for symbol in symbols
    }
    return dict(days=days, day_index={d: i for i, d in enumerate(days)}, prices=prices,
                indices={d: dict(open=100.0, close=100.0) for d in days}, anomaly_keys=set())


def candidate(data, i=0, symbol='600001', rps=90):
    return dict(signal_date=data['days'][i], symbol=symbol, name=symbol,
                industry='测试', rps250=rps, price=10.0, atr=0.2, ma20=10.0,
                history_ready=True)


def set_bar(data, i, symbol='600001', **values):
    bar = data['prices'][symbol][data['days'][i]]
    bar.update(values)
    bar['high'] = max(bar['high'], bar['open'], bar['close'])
    bar['low'] = min(bar['low'], bar['open'], bar['close'])


class ExecutionTests(unittest.TestCase):
    def test_fixed_horizon_uses_next_open_and_full_holding_sessions(self):
        data = fixture()
        set_bar(data, 1, open=10.1)
        set_bar(data, 4, open=10.5)
        row = A.trade(candidate(data), 3, data)
        self.assertEqual(row['entry_date'], data['days'][1])
        self.assertEqual(row['exit_date'], data['days'][4])
        self.assertAlmostEqual(row['net_return'], (10.5 - 10.1*A.COST/2) / (10.1*(1+A.COST/2)) - 1)
        self.assertEqual(row['status'], 'closed')

    def test_one_day_horizon_obeys_t_plus_one(self):
        data = fixture()
        row = A.trade(candidate(data), 1, data)
        self.assertEqual(row['entry_date'], data['days'][1])
        self.assertEqual(row['exit_date'], data['days'][2])

    def test_buy_day_stop_uses_following_open_and_real_gap(self):
        data = fixture()
        set_bar(data, 1, close=9.6)
        set_bar(data, 2, open=9.5, close=9.6)
        row = A.trade(candidate(data), 5, data, managed=True)
        self.assertEqual(row['exit_date'], data['days'][2])
        self.assertEqual(row['exit_reason'], 'close_stop')
        self.assertEqual(row['exit_price'], 9.5)
        self.assertNotEqual(row['exit_price'], row['stop_price'])

    def test_c_exit_on_entry_day_is_next_day(self):
        data = fixture()
        exits = {(data['days'][1], '600001'): {'C_EXIT'}}
        row = A.trade(candidate(data), 5, data, exits, managed=True)
        self.assertEqual(row['exit_date'], data['days'][2])
        self.assertEqual(row['exit_reason'], 'c_exit_or_risk')

    def test_limit_up_next_open_skips_without_chasing(self):
        data = fixture()
        set_bar(data, 1, open=11.0, close=11.0)
        row = A.trade(candidate(data), 3, data)
        self.assertEqual(row['status'], 'limit_entry')
        self.assertNotIn('entry_date', row)

    def test_limit_down_exit_delays_to_tradable_open(self):
        data = fixture()
        set_bar(data, 3, open=9.0, close=9.0)
        set_bar(data, 4, open=9.1, close=9.1)
        row = A.trade(candidate(data), 2, data)
        self.assertEqual(row['exit_date'], data['days'][4])
        self.assertEqual(row['exit_delay'], 1)
        self.assertAlmostEqual(row['net_return'], (9.1 - 10*A.COST/2) / (10*(1+A.COST/2)) - 1)

    def test_missing_entry_is_not_replaced_with_later_session(self):
        data = fixture()
        del data['prices']['600001'][data['days'][1]]
        row = A.trade(candidate(data), 3, data)
        self.assertEqual(row['status'], 'missing_entry')
        self.assertNotIn('entry_date', row)

    def test_incomplete_horizon_is_not_zero_return_or_closed(self):
        data = fixture(n=4)
        row = A.trade(candidate(data), 5, data)
        self.assertEqual(row['status'], 'immature')
        self.assertEqual(row['entry_date'], data['days'][1])
        self.assertNotIn('net_return', row)

    def test_last_day_signal_has_no_synthetic_entry(self):
        data = fixture(n=4)
        row = A.trade(candidate(data, i=3), 1, data)
        self.assertEqual(row['status'], 'immature')
        self.assertNotIn('entry_date', row)

    def test_exit_session_intraday_range_not_part_of_exposure(self):
        data = fixture()
        set_bar(data, 3, high=100, low=.1)
        row = A.trade(candidate(data), 2, data)
        self.assertAlmostEqual(row['mfe'], .02)
        self.assertAlmostEqual(row['mae'], -.02)

    def test_future_after_exit_does_not_change_trade(self):
        data = fixture()
        before = A.trade(candidate(data), 2, data)
        set_bar(data, 4, open=40, close=40, high=100, low=.1)
        data['anomaly_keys'].add(('600001', data['days'][4]))
        after = A.trade(candidate(data), 2, data)
        self.assertEqual(before, after)

    def test_overlapping_stock_signals_do_not_create_positions(self):
        data = fixture()
        rows = A.simulate([candidate(data, i=i) for i in (0, 2, 4)], 3, data)
        self.assertEqual([r['status'] for r in rows], ['closed', 'overlap', 'closed'])

    def test_missing_path_is_explicitly_unusable(self):
        data = fixture()
        del data['prices']['600001'][data['days'][2]]
        row = A.trade(candidate(data), 3, data)
        self.assertEqual(row['status'], 'missing_path')
        self.assertNotIn('net_return', row)

    def test_exit_open_can_reenter_same_symbol_consistently(self):
        data = fixture(n=12)
        cands = [candidate(data, i=i) for i in (0, 3)]
        rows = A.simulate(cands, 3, data)
        self.assertEqual([r['status'] for r in rows], ['closed', 'closed'])
        self.assertEqual(rows[0]['exit_date'], rows[1]['entry_date'])
        result = A.portfolio([A.trade(c, 3, data) for c in cands], data,
                             data['days'][0], data['days'][-1])
        self.assertEqual(result['entries'], 2)
        self.assertTrue(all(r['positions'] <= 1 for r in result['curve']))

    def test_missing_path_still_reserves_entered_stock(self):
        data = fixture()
        del data['prices']['600001'][data['days'][2]]
        rows = A.simulate([candidate(data, i=i) for i in (0, 3)], 3, data)
        self.assertEqual([r['status'] for r in rows], ['missing_path', 'overlap'])

    def test_zero_volume_signal_has_no_entry_or_return(self):
        data = fixture()
        set_bar(data, 0, volume=0)
        row = A.trade(candidate(data), 3, data)
        self.assertEqual(row['status'], 'nontradable_signal')
        self.assertNotIn('entry_date', row)
        self.assertNotIn('net_return', row)

    def test_bad_input_bar_is_rejected(self):
        self.assertFalse(A.valid_bar(None))
        self.assertFalse(A.valid_bar(dict(open=10, high=9, low=8, close=10, volume=100)))
        self.assertFalse(A.valid_bar(dict(open=float('nan'), high=12, low=8, close=10, volume=100)))


class FeatureTests(unittest.TestCase):
    def test_feature_prefix_unchanged_by_future_bar(self):
        data = fixture()
        prior, _ = A.features(data)
        set_bar(data, 30, close=15, open=15, high=16, low=14)
        after, _ = A.features(data)
        for day in data['days'][:30]:
            self.assertEqual(prior['600001'][day], after['600001'][day])

    def test_incomplete_prior_run_window_blocks_new_signal(self):
        data = fixture()
        days = data['days']
        data.update(mapping={'600001': '测试'}, results=[dict(trade_date=days[25], symbol='600001')],
                    runs=set(days), info={'600001': {'name': '测试股'}},
                    rps={'600001': {days[25]: {'rps_250': 99}}},
                    evidence={'signals': [], 'archive_signals': [], 'all_stock_signals': []})
        feats, _ = A.features(data)
        out, *_ = A.candidates(data, feats)
        self.assertEqual(len(out['first20']), 1)
        data['runs'].remove(days[10])
        out, *_ = A.candidates(data, feats)
        self.assertEqual(out['first20'], [])
        self.assertEqual(out['new5'], [])


    def test_raw_first_hit_and_count_survive_nontradable_filter(self):
        data = fixture(n=65)
        days = data['days']
        first_i = next(i for i, d in enumerate(days) if d >= A.START)
        later_i = first_i + 4
        set_bar(data, first_i, volume=0)
        data.update(mapping={'600001': '测试'},
                    results=[dict(trade_date=days[i], symbol='600001')
                             for i in (first_i, later_i)],
                    runs=set(days), info={'600001': {'name': '测试股'}},
                    rps={'600001': {days[i]: {'rps_250': 99} for i in (first_i, later_i)}},
                    evidence={'signals': [], 'archive_signals': [], 'all_stock_signals': []})
        feats, _ = A.features(data)
        out, hits, daily, _ = A.candidates(data, feats)
        self.assertEqual(len(daily), 2, 'Raw hit counts must retain the suspended first hit.')
        self.assertEqual(sum(len(v) for v in hits.values()), 2)
        self.assertEqual(daily[0]['signal_date'], days[first_i])
        self.assertFalse(daily[0]['signal_bar_tradable'])
        self.assertTrue(daily[1]['signal_bar_tradable'])
        self.assertEqual([r['signal_date'] for r in out['daily']], [days[later_i]])
        first_outcome = A.trade(daily[0], 3, data)
        later_outcome = A.trade(daily[1], 3, data)
        self.assertEqual(first_outcome['status'], 'nontradable_signal')
        self.assertNotIn('net_return', first_outcome)
        self.assertEqual(later_outcome['status'], 'closed')


class PortfolioTests(unittest.TestCase):
    def test_immature_position_stays_in_nav_and_occupies_slot(self):
        data = fixture(n=4)
        set_bar(data, 3, close=10.5)
        row = A.trade(candidate(data), 5, data)
        result = A.portfolio([row], data, data['days'][0], data['days'][-1])
        self.assertEqual(result['entries'], 1)
        self.assertEqual(result['open_positions'], 1)
        self.assertAlmostEqual(result['return_nav'], .2*.05 - .2*A.COST/2)
        self.assertTrue(all(r['cash'] >= -1e-12 for r in result['curve']))

    def test_six_simultaneous_candidates_take_only_five_by_known_rps(self):
        symbols = tuple(f'60000{i}' for i in range(1, 7))
        data = fixture(n=8, symbols=symbols)
        rows = [A.trade(candidate(data, symbol=s, rps=i), 3, data)
                for i, s in enumerate(symbols)]
        result = A.portfolio(rows, data, data['days'][0], data['days'][-1])
        self.assertEqual(result['entries'], 5)
        self.assertEqual({r['symbol'] for r in result['trades']}, set(symbols[1:]))
        self.assertTrue(all(r['positions'] <= 5 and r['cash'] >= -1e-12 for r in result['curve']))

    def test_immature_abnormal_mark_is_not_trusted(self):
        data = fixture(n=4)
        data['anomaly_keys'].add(('600001', data['days'][2]))
        row = A.trade(candidate(data), 5, data)
        self.assertEqual(row['status'], 'immature')
        result = A.portfolio([row], data, data['days'][0], data['days'][-1])
        self.assertIsNone(result['return_nav'])

    def test_selected_future_missing_path_cannot_disappear_from_portfolio(self):
        data = fixture(n=8)
        del data['prices']['600001'][data['days'][2]]
        row = A.trade(candidate(data), 3, data)
        self.assertEqual(row['status'], 'missing_path')
        result = A.portfolio([row], data, data['days'][0], data['days'][-1])
        self.assertIsNone(result['return_nav'], 'Future data failure must not become a skipped entry and flat NAV.')


if __name__ == '__main__':
    unittest.main(verbosity=2)
