import pandas as pd

from quant_engine.calculators.timing_signal_calculator import TimingSignalCalculator


def make_candidate(trade_date: str) -> dict:
    return {
        "trade_date": trade_date,
        "symbol": "600001",
        "name": "测试股份",
        "sector_name": "测试行业",
        "market_state": "M3",
        "industry_state": "I3",
        "lifecycle_state": "S4",
        "open": 9.5,
        "high": 10.2,
        "low": 9.4,
        "close": 10.0,
        "volume": 200.0,
        "max_down_volume_10": 100.0,
        "close_position": 0.8,
        "structure_score": 80.0,
        "ma_10": 9.8,
        "ma_20": 9.9,
        "ma_50": 9.7,
        "volume_ma20": 150.0,
        "high_10_prev": 11.0,
        "high_20_prev": 11.0,
        "prev_high": 10.4,
        "low_20": 9.5,
        "high_60": 10.2,
        "atr_20": 0.1,
        "volume_down_3": False,
        "gap_recent": False,
        "gap_not_filled": False,
        "net_profit_up": 0.0,
        "revenue_up": 0.0,
        "rps_20": 92.0,
        "rps_50": 90.0,
        "rps_120": 88.0,
        "rps_250": 85.0,
    }


def test_cooldown_uses_existing_history_across_daily_windows():
    calculator = TimingSignalCalculator()
    source = pd.DataFrame(
        [
            make_candidate("2026-01-08"),
            make_candidate("2026-01-14"),
        ]
    )
    history = pd.DataFrame(
        [
            {
                "symbol": "600001",
                "signal_type": "pocket_pivot",
                "trade_date": "2026-01-02",
            }
        ]
    )
    trade_calendar = [
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
        "2026-01-12",
        "2026-01-13",
        "2026-01-14",
    ]

    result = calculator._compute_signals(source, history, trade_calendar)

    assert result["trade_date"].tolist() == ["2026-01-14"]
    assert result["signal_type"].tolist() == ["pocket_pivot"]
