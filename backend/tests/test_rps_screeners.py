"""Verify the source formula's boundaries and per-stock date alignment."""

import numpy as np
import pandas as pd

from quant_engine.strategies.rps_screeners import STRATEGIES, evaluate


def stock(bars: int = 100) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
            "rps_10": 95.0,
            "rps_20": 95.0,
            "rps_50": 95.0,
            "rps_120": 95.0,
            "rps_250": 95.0,
        },
        index=range(bars),
    )


def test_acceleration_uses_20th_price_bar_and_does_not_fill_missing_rps():
    frame = stock()
    frame.loc[99, ["rps_50", "close"]] = [90, 101]
    frame.loc[94, "volume"] = 200
    frame.loc[79, "rps_50"] = 79
    other = stock().assign(symbol="000001")
    frame = frame.assign(symbol="600519")
    # Interleaved stocks, repeated indices, and a missing intervening RPS row.
    combined = pd.concat([frame, other]).sort_index(kind="stable")
    combined.loc[80, "rps_50"] = np.nan
    rule = "rps_acceleration_v1"
    assert evaluate(combined)[rule].loc[99].tolist() == [True, False]
    frame.loc[79, "rps_50"] = 80
    assert not evaluate(frame)[rule].iloc[-1]
    frame.loc[79, "rps_50"] = np.nan
    assert not evaluate(frame)[rule].iloc[-1]


def test_platform_breakout_requires_strict_body_and_break_of_reference_high():
    frame = stock()
    frame.loc[79, ["close", "high", "volume"]] = [103, 106, 150]
    frame.loc[99, ["close", "rps_20", "rps_50"]] = [107, 90, 85]
    rule = "rps_platform_breakout_v1"
    assert evaluate(frame)[rule].iloc[-1]
    frame.loc[99, "close"] = 106
    assert not evaluate(frame)[rule].iloc[-1]
    frame.loc[99, "close"] = 107
    frame.loc[79, "close"] = 102
    assert not evaluate(frame)[rule].iloc[-1]


def test_long_pullback_accepts_a_high_wick_even_with_a_bearish_reference_close():
    frame = stock()
    frame.loc[49, ["open", "high", "volume"]] = [101, 102, 150]
    frame.loc[99, ["close", "rps_50", "rps_120", "rps_250"]] = [104, 85, 90, 90]
    rule = "rps_long_pullback_v1"
    assert evaluate(frame)[rule].iloc[-1]
    frame.loc[99, "close"] = 105
    assert not evaluate(frame)[rule].iloc[-1]
    frame.loc[99, "close"] = 104
    frame.loc[49, "high"] = 101
    assert not evaluate(frame)[rule].iloc[-1]


def test_resonance_volume_mean_includes_the_current_bar():
    frame = stock()
    frame.loc[99, ["close", "volume", "rps_10", "rps_20", "rps_50"]] = [101, 127, 90, 90, 90]
    rule = "rps_resonance_v1"
    assert evaluate(frame)[rule].iloc[-1]
    frame.loc[99, "volume"] = 125
    assert not evaluate(frame)[rule].iloc[-1]
    frame.loc[99, "volume"] = 127
    frame.loc[89, "close"] = 101
    assert not evaluate(frame)[rule].iloc[-1]


def test_year_high_includes_today_requires_full_window_and_has_strict_threshold():
    frame = stock(252)
    frame["high"] = 100.0
    frame["close"] = 91.0
    frame.loc[250, "close"] = 90
    frame.loc[251, "high"] = 103
    rule = "near_year_high_v1"
    metrics = {}
    result = evaluate(frame, metrics=metrics)[rule]
    assert result.iloc[248:].tolist() == [False, True, False, False]
    assert metrics[rule].loc[251, "close_to_high_250"] == 91 / 103


def test_monthly_reversal_picks_first_marker_and_uses_no_future_bars():
    frame = stock(340)
    close = np.concatenate([np.full(170, 10.0), np.linspace(10, 9, 100),
                            np.linspace(9, 10.5, 70)])
    frame["close"] = close
    frame["open"] = close * 0.99
    frame["high"] = close * 1.01
    frame["low"] = close * 0.99
    rule = "monthly_reversal_v65"
    full = evaluate(frame)[rule]
    assert full[full].index.tolist() == [313]
    assert evaluate(frame.iloc[:314])[rule].iloc[-1]
    frame.loc[313, ["rps_50", "rps_120"]] = [87, 90]
    shifted = evaluate(frame)[rule]
    assert shifted[shifted].index.tolist() == [314]


def test_empty_input_produces_all_six_empty_masks():
    assert list(evaluate(pd.DataFrame())) == list(STRATEGIES)
