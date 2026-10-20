from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, event, text

from quant_engine.calculators import sector_composite_rps_calculator as calculator_module
from quant_engine.calculators.sector_composite_rps_calculator import (
    SectorCompositeRPSCalculator,
    compute_cross_sectional_rps,
)


def test_cross_sectional_rps_uses_net_value_and_average_ties() -> None:
    rows = []
    values = {
        "甲": [100.0, 101.0, 102.0, 103.0],
        "乙": [100.0, 101.0, 102.0, 103.0],
        "丙": [100.0, 99.0, 99.0, 99.0],
    }
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    for sector, series in values.items():
        for trade_date, net_value in zip(dates, series):
            rows.append(
                {
                    "sector_name": sector,
                    "trade_date": trade_date,
                    "net_value": net_value,
                }
            )

    result = compute_cross_sectional_rps(
        pd.DataFrame(rows), periods=(1,), value_column="net_value"
    )

    latest = result[result["trade_date"] == pd.Timestamp("2026-01-04")]
    ranks = dict(zip(latest["sector_name"], latest["rps_1"]))
    assert ranks["甲"] == 83.33
    assert ranks["乙"] == 83.33
    assert ranks["丙"] == 33.33


def test_cross_sectional_rps_does_not_forward_fill_missing_sector_values() -> None:
    frame = pd.DataFrame(
        [
            {"sector_name": "甲", "trade_date": "2026-01-01", "net_value": 100},
            {"sector_name": "甲", "trade_date": "2026-01-02", "net_value": 101},
            {"sector_name": "乙", "trade_date": "2026-01-01", "net_value": 100},
            {"sector_name": "乙", "trade_date": "2026-01-02", "net_value": None},
        ]
    )

    result = compute_cross_sectional_rps(frame, periods=(1,))
    missing = result[
        (result["sector_name"] == "乙")
        & (result["trade_date"] == pd.Timestamp("2026-01-02"))
    ].iloc[0]
    assert pd.isna(missing["chg_1"])
    assert pd.isna(missing["rps_1"])


def test_calculator_writes_only_the_canonical_v2_table(tmp_path, monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sector_composite_daily (
                    sector_name TEXT,
                    trade_date TEXT,
                    net_value REAL,
                    method_version TEXT,
                    quality_flag TEXT
                )
                """
            )
        )
        rows = [
            (sector, f"2026-01-{day:02d}", 100 + day * (index + 1), "ew_qfq_v1", "OK")
            for index, sector in enumerate(("甲", "乙", "丙"))
            for day in range(1, 8)
        ]
        conn.execute(
            text(
                "INSERT INTO sector_composite_daily "
                "(sector_name, trade_date, net_value, method_version, quality_flag) "
                "VALUES (:sector, :trade_date, :net_value, :method_version, :quality_flag)"
            ),
            [
                {
                    "sector": sector,
                    "trade_date": trade_date,
                    "net_value": net_value,
                    "method_version": method_version,
                    "quality_flag": quality_flag,
                }
                for sector, trade_date, net_value, method_version, quality_flag in rows
            ],
        )

    monkeypatch.setattr(calculator_module, "get_engine", lambda: engine)
    calculator = SectorCompositeRPSCalculator()
    result = calculator.run_daily(target_dates=["2026-01-07"], missing_only=False)

    assert result["success"] is True
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM quant_feature_sector_rps_v2")).scalar_one()
        assert count == 3
        assert conn.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE name='quant_feature_sector_rps'")
        ).scalar_one() == 0


def test_daily_queries_do_not_wrap_trade_date_and_remain_indexable(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sector_composite_daily (
                    sector_name TEXT,
                    trade_date TEXT,
                    net_value REAL,
                    method_version TEXT,
                    quality_flag TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX idx_sector_composite_method_date_sector "
                "ON sector_composite_daily (method_version, trade_date, sector_name)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sector_composite_daily VALUES "
                "('甲', '2026-01-01', 100, 'ew_qfq_v1', 'PASS'), "
                "('甲', '2026-01-02', 101, 'ew_qfq_v1', 'PASS')"
            )
        )

    monkeypatch.setattr(calculator_module, "get_engine", lambda: engine)
    calculator = SectorCompositeRPSCalculator()
    calculator._init_table()
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(str(statement))

    calculator.load_data(start_date="2026-01-01", end_date="2026-01-02")
    calculator._source_dates(limit=2)
    calculator._existing_dates([pd.Timestamp("2026-01-02").date()])
    calculator._window_start([pd.Timestamp("2026-01-02").date()])

    relevant = [
        statement
        for statement in statements
        if "trade_date" in statement and statement.lstrip().upper().startswith("SELECT")
    ]
    assert relevant
    assert all("DATE(" not in statement.upper() for statement in relevant)
