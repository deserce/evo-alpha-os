import sqlite3

import pandas as pd

from quant_engine.calculators.sector_rps_calculator import SectorRPSCalculator
from scripts.calculate_strength_features import calculate_industry_strength


def test_sector_rps_keeps_real_industry_when_sector_list_is_used(monkeypatch):
    calculator = SectorRPSCalculator.__new__(SectorRPSCalculator)
    monkeypatch.setattr(calculator, "_has_sector_list", lambda: True)

    source = pd.DataFrame(
        [
            {
                "sector_name": "医药流通",
                "trade_date": "2026-01-05",
                "close": 1000.0,
            }
        ]
    )

    result = calculator.should_filter(source)

    assert result["sector_name"].tolist() == ["医药流通"]


def test_industry_strength_uses_sector_list_universe():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            """
            CREATE TABLE stock_sector_map (
                symbol TEXT,
                sector_name TEXT,
                sector_type TEXT
            );
            CREATE TABLE sector_list (
                sector_name TEXT PRIMARY KEY,
                sector_type TEXT,
                sector_code TEXT,
                source TEXT,
                updated_at TEXT
            );
            CREATE TABLE sector_daily_prices (
                sector_name TEXT,
                trade_date TEXT,
                close REAL,
                amount REAL
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                close REAL,
                high REAL
            );
            """
        )
        sectors = ["医药流通", "半导体", "元件", "食品饮料", "银行"]
        for index, sector_name in enumerate(sectors, start=1):
            symbol = f"60000{index}"
            conn.execute(
                "INSERT INTO stock_sector_map VALUES (?, ?, 'Industry')",
                (symbol, sector_name),
            )
            conn.execute(
                "INSERT INTO sector_list VALUES (?, 'Industry', ?, 'test', '2026-01-05')",
                (sector_name, sector_name),
            )
            conn.execute(
                "INSERT INTO sector_daily_prices VALUES (?, '2026-01-05', ?, ?)",
                (sector_name, 1000.0 + index, 1000000.0 + index),
            )
            conn.execute(
                "INSERT INTO stock_daily_prices VALUES (?, '2026-01-05', ?, ?)",
                (symbol, 10.0 + index, 10.5 + index),
            )
        conn.execute(
            "INSERT INTO sector_daily_prices VALUES ('TDX行业35', '2026-01-05', 888.0, 999.0)"
        )
        conn.commit()

        result = calculate_industry_strength(conn, ["2026-01-05"], "2026-01-01")

        assert set(result["sector_name"]) == set(sectors)
        assert "TDX行业35" not in set(result["sector_name"])
    finally:
        conn.close()
