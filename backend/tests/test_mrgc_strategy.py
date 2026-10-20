import pandas as pd

from quant_engine.runner.strategy_runner import StrategyRunner
from quant_engine.strategies.mrgc_strategy import MrgcStrategy


def test_run_does_not_hard_filter_pool_by_lifecycle(monkeypatch):
    strategy = MrgcStrategy()
    pool = pd.DataFrame([{"symbol": "000001", "name": "Test Stock"}])
    features = pd.DataFrame(
        [{"symbol": "000001", "rps_50": 95, "rps_120": 96, "rps_250": 97}]
    )
    kline = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": "2026-07-14",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000,
                "turnover_rate": 1.0,
            }
        ]
    )
    saved = []
    queued = []
    from data_job import auto_f10

    monkeypatch.setattr(
        auto_f10, "enqueue_symbols", lambda symbols, **kwargs: queued.append(symbols)
    )

    monkeypatch.setattr(strategy, "get_stock_pool", lambda pool_name: pool)
    monkeypatch.setattr(strategy, "get_daily_features", lambda trade_date, symbols: features)
    monkeypatch.setattr(
        strategy,
        "_apply_market_industry_filter",
        lambda trade_date, pool_df: (_ for _ in ()).throw(
            AssertionError("MRGC must not hard-filter candidates by lifecycle state")
        ),
        raising=False,
    )
    monkeypatch.setattr(strategy, "_check_signal", lambda frame, rps: (True, "MRGC trigger"))
    monkeypatch.setattr(
        strategy, "save_results", lambda frame: saved.append(frame.copy()) or len(frame)
    )
    monkeypatch.setattr(pd, "read_sql", lambda sql, engine: kline)

    count = strategy.run("2026-07-14")

    assert count == 1
    assert len(saved) == 1
    assert saved[0].iloc[0]["symbol"] == "000001"
    assert queued == [["000001"]]


def test_run_many_skips_completed_dates_and_repairs_missing_dates(monkeypatch):
    runner = StrategyRunner()
    calls = []
    monkeypatch.setattr(
        runner,
        "has_successful_run",
        lambda strategy_name, trade_date: trade_date == "2026-07-15",
    )
    monkeypatch.setattr(
        runner,
        "run",
        lambda strategy_name, trade_date=None: calls.append(trade_date) or {"success": True},
    )

    result = runner.run_many(
        "mrgc",
        ["2026-07-15", "2026-07-16", "2026-07-16"],
        missing_only=True,
    )

    assert result["success"] is True
    assert calls == ["2026-07-16"]
    assert result["dates"]["2026-07-15"]["skipped"] is True
