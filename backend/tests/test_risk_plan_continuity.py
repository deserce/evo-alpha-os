import sqlite3

from quant_engine.calculators.risk_plan_calculator import RiskPlanCalculator


def seed_database(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE timing_signals_daily (
                signal_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                signal_type TEXT,
                entry_price REAL,
                stop_loss_price REAL,
                stop_loss_pct REAL,
                risk_amount_pct REAL,
                suggested_position_pct REAL,
                is_invalidated INTEGER DEFAULT 0
            );
            CREATE TABLE stock_lifecycle_state (
                trade_date TEXT,
                symbol TEXT,
                market_state TEXT,
                industry_state TEXT,
                structure_score REAL
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                high REAL,
                low REAL,
                close REAL
            );
            INSERT INTO timing_signals_daily VALUES
                ('2026-01-02_600001_supply', '600001', '2026-01-02',
                 'supply_exhaustion', 10.0, 9.0, 0.10, 1.0, 10.0, 0);
            INSERT INTO stock_lifecycle_state VALUES
                ('2026-01-02', '600001', 'M3', 'I3', 80),
                ('2026-01-05', '600001', 'M3', 'I3', 82);
            INSERT INTO stock_daily_prices VALUES
                ('600001', '2026-01-02', 10.2, 9.8, 10.0),
                ('600001', '2026-01-05', 11.2, 10.8, 11.0);
            """
        )


def test_active_plan_is_revalued_on_latest_stock_date(tmp_path):
    db_path = tmp_path / "risk.db"
    seed_database(db_path)

    result = RiskPlanCalculator(str(db_path)).run_daily()

    assert result["success"]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        plan = conn.execute("SELECT * FROM trade_plans").fetchone()

    assert plan["trade_date"] == "2026-01-05"
    assert plan["signal_trade_date"] == "2026-01-02"
    assert plan["latest_close"] == 11.0
    assert plan["days_since_signal"] == 1
    assert plan["r_multiple"] == 1.0


def test_orphan_plans_are_removed_during_refresh(tmp_path):
    db_path = tmp_path / "risk.db"
    seed_database(db_path)
    calculator = RiskPlanCalculator(str(db_path))
    calculator._create_table(sqlite3.connect(db_path))

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trade_plans (
                plan_id, signal_id, trade_date, symbol, plan_status
            ) VALUES ('orphan', 'missing-signal', '2025-12-01', '000001', 'active')
            """
        )
        conn.commit()

    result = calculator.run_daily()

    assert result["success"]
    with sqlite3.connect(db_path) as conn:
        orphan_count = conn.execute(
            "SELECT COUNT(*) FROM trade_plans WHERE plan_id='orphan'"
        ).fetchone()[0]
    assert orphan_count == 0
