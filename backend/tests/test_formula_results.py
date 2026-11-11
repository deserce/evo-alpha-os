"""Database contract for the six independent formula results."""

import json
from datetime import date, timedelta

from sqlalchemy import create_engine

from quant_engine.runner.formula_results import STRATEGIES, run_formula_screens
from quant_engine.runner.strategy_runner import ensure_strategy_run_log_table


def test_exact_dates_rerun_zero_and_audit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    ensure_strategy_run_log_table(engine)
    start = date(2024, 1, 1)
    days = [(start + timedelta(days=index)).isoformat() for index in range(251)]
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL,
                close REAL, volume REAL, market TEXT, PRIMARY KEY(symbol,trade_date)
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE quant_feature_rps (
                symbol TEXT,trade_date TEXT,rps_10 REAL,rps_20 REAL,rps_50 REAL,
                rps_120 REAL,rps_250 REAL,PRIMARY KEY(symbol,trade_date)
            )
        """)
        conn.exec_driver_sql("INSERT INTO stock_daily_prices VALUES (?,?,?,?,?,?,?,?)", [
            ("600000", day, 95, 100, 90, 95, 1, "SH") for day in days
        ])
        conn.exec_driver_sql("INSERT INTO quant_feature_rps VALUES (?,?,?,?,?,?,?)", [
            ("600000", day + " 00:00:00", 10, 10, 10, 10, 10) for day in days
        ])
    previous, target, absent = days[-2], days[-1], "2025-01-01"
    name = "near_year_high_v1"
    result = run_formula_screens(engine, [previous, target, absent])
    assert not result["success"]
    assert result["dates"][previous]["counts"][name] == 1
    assert result["dates"][target]["counts"][name] == 1
    with engine.begin() as conn:
        details = conn.exec_driver_sql("""
            SELECT meta_info FROM quant_formula_results
            WHERE trade_date=? AND strategy_name=?
        """, (target, name)).scalar_one()
        assert json.loads(details)["close_to_high_250"] == 0.95
        assert conn.exec_driver_sql("""
            SELECT count(*) FROM strategy_run_log WHERE trade_date=? AND status='failed'
        """, (absent,)).scalar_one() == len(STRATEGIES)
        conn.exec_driver_sql("UPDATE stock_daily_prices SET close=90 WHERE trade_date=?", (target,))
    assert run_formula_screens(engine, [target], missing_only=True)["dates"] == {}
    assert run_formula_screens(engine, [target])["dates"][target]["counts"][name] == 0
    with engine.connect() as conn:
        assert conn.exec_driver_sql("""
            SELECT trade_date FROM quant_formula_results WHERE strategy_name=?
        """, (name,)).scalar_one() == previous
    engine.dispose()
