import asyncio
import sqlite3

import pytest
from fastapi import HTTPException

from app.api.v1 import stock as stock_api


def _fetch_all(db_path):
    def inner(query, params=None):
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params or {}).fetchall()
            return [dict(row) for row in rows]

    return inner


def _fetch_one(db_path):
    def inner(query, params=None):
        rows = _fetch_all(db_path)(query, params)
        return rows[0] if rows else None

    return inner


def _create_base_db(db_path, include_optional=True):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stock_daily_prices (
            symbol TEXT,
            trade_date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            pct_chg REAL,
            turnover_rate REAL
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO stock_daily_prices VALUES (
            '000001', ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            ("2026-05-25", 10, 11, 9.5, 10.5, 100, 1000, 5.0, 1.0),
            ("2026-05-26", 10.5, 12, 10, 11.5, 120, 1400, 9.52, 1.2),
            ("2026-05-27", 11.5, 12.5, 11, 12.0, 130, 1600, 4.35, 1.3),
            ("2026-05-28", 12.0, 13, 11.8, 12.8, 150, 1900, 6.67, 1.5),
            ("2026-05-29", 12.8, 13.5, 12.4, 13.2, 160, 2100, 3.13, 1.6),
        ],
    )

    if include_optional:
        conn.executescript(
            """
            CREATE TABLE quant_feature_rps (
                symbol TEXT,
                trade_date TEXT,
                rps_5 REAL,
                rps_10 REAL,
                rps_20 REAL,
                rps_50 REAL,
                rps_120 REAL,
                rps_250 REAL
            );
            CREATE TABLE finance_fund_holdings (
                symbol TEXT,
                report_date TEXT,
                fund_count INTEGER,
                hold_count REAL,
                hold_value REAL
            );
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                total_mv REAL
            );
            CREATE TABLE stock_northbound_holdings (
                symbol TEXT,
                hold_date TEXT,
                hold_ratio REAL,
                hold_value REAL
            );
            """
        )
        conn.executemany(
            "INSERT INTO quant_feature_rps VALUES ('000001', ?, ?, ?, ?, ?, ?, ?)",
            [
                ("2026-05-25 00:00:00.000000", 80, 81, 82, 83, 84, 85),
                ("2026-05-26 00:00:00.000000", 81, 82, 83, 84, 85, 86),
                ("2026-05-27 00:00:00.000000", 82, 83, 84, 85, 86, 87),
                ("2026-05-28 00:00:00.000000", 83, 84, 85, 86, 87, 88),
                ("2026-05-29 00:00:00.000000", 84, 85, 86, 87, 88, 89),
            ],
        )
        conn.execute(
            "INSERT INTO finance_fund_holdings VALUES ('000001', '2026-03-31', 12, 1000000, 5000000)"
        )
        conn.execute(
            "INSERT INTO stock_valuation_daily VALUES ('000001', '2026-03-31', 100000000)"
        )
        conn.execute(
            "INSERT INTO stock_northbound_holdings VALUES ('000001', '2026-05-29', 2.6, 3000000)"
        )

    conn.commit()
    conn.close()


def _patch_db(monkeypatch, db_path):
    monkeypatch.setattr(stock_api, "fetch_all", _fetch_all(db_path))
    monkeypatch.setattr(stock_api, "fetch_one", _fetch_one(db_path))


def test_stock_chart_returns_real_indicator_series(tmp_path, monkeypatch):
    db_path = tmp_path / "stock.db"
    _create_base_db(db_path)
    _patch_db(monkeypatch, db_path)

    data = asyncio.run(
        stock_api.get_stock_chart("000001", period="daily", end_date="2026-05-29", limit=260)
    ).data

    assert data["dates"][-1] == "2026-05-29"
    assert data["kline"][-1] == [12.8, 13.2, 12.4, 13.5]
    assert data["volume"][-1] == 160
    assert data["amount"][-1] == 2100
    assert data["rps_short"]["r20"][-1] == 86
    assert data["rps_long"]["r250"][-1] == 89
    assert data["funds"]["fund"][-1] == 5
    assert data["funds"]["north"][-1] == 2.6
    assert len(data["macd"]["hist"]) == data["count"]
    assert len(data["kdj"]["k"]) == data["count"]


def test_stock_chart_weekly_aggregates_price_and_amount(tmp_path, monkeypatch):
    db_path = tmp_path / "stock.db"
    _create_base_db(db_path)
    _patch_db(monkeypatch, db_path)

    data = asyncio.run(
        stock_api.get_stock_chart("000001", period="weekly", end_date="2026-05-29", limit=30)
    ).data

    assert data["count"] == 1
    assert data["kline"][0] == [10.0, 13.2, 9.5, 13.5]
    assert data["volume"][0] == 660
    assert data["amount"][0] == 8000
    assert data["rps_short"]["r5"][0] == 84


def test_stock_chart_handles_missing_optional_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "stock.db"
    _create_base_db(db_path, include_optional=False)
    _patch_db(monkeypatch, db_path)

    data = asyncio.run(
        stock_api.get_stock_chart("000001", period="daily", end_date="2026-05-29", limit=260)
    ).data

    assert data["count"] == 5
    assert data["rps_short"]["r5"] == [None, None, None, None, None]
    assert data["funds"]["fund"] == [None, None, None, None, None]


def test_stock_chart_rejects_invalid_period(tmp_path, monkeypatch):
    db_path = tmp_path / "stock.db"
    _create_base_db(db_path)
    _patch_db(monkeypatch, db_path)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(stock_api.get_stock_chart("000001", period="hourly", limit=260))

    assert exc.value.status_code == 400
