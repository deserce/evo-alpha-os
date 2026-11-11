import asyncio
import sqlite3

import pytest
from fastapi import HTTPException

from app.api.v1 import quant as quant_api


def make_strategy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE stock_info (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT
        );

        CREATE TABLE quant_preselect_results (
            strategy_name TEXT,
            trade_date TEXT,
            symbol TEXT,
            signal_type TEXT,
            meta_info TEXT,
            strategy_display_name TEXT,
            result_type TEXT,
            created_at TEXT,
            PRIMARY KEY (strategy_name, trade_date, symbol)
        );

        CREATE TABLE stock_lifecycle_state (
            trade_date TEXT,
            symbol TEXT,
            market_state TEXT,
            industry_state TEXT,
            lifecycle_state TEXT,
            structure_score REAL,
            structure_state TEXT,
            rps_20 REAL,
            rps_50 REAL,
            rps_120 REAL,
            rps_250 REAL,
            PRIMARY KEY (trade_date, symbol)
        );

        CREATE TABLE quant_feature_rps (
            symbol TEXT,
            trade_date TEXT,
            rps_5 REAL,
            rps_10 REAL,
            rps_20 REAL,
            rps_50 REAL,
            rps_120 REAL,
            rps_250 REAL,
            PRIMARY KEY (symbol, trade_date)
        );

        CREATE TABLE stock_daily_prices (
            symbol TEXT,
            trade_date TEXT,
            close REAL,
            pct_chg REAL,
            amount REAL,
            PRIMARY KEY (symbol, trade_date)
        );

        CREATE TABLE strategy_run_log (
            run_id TEXT PRIMARY KEY,
            strategy_name TEXT,
            trade_date TEXT,
            status TEXT,
            signal_count INTEGER,
            error_message TEXT,
            started_at TEXT,
            finished_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO stock_info VALUES ('000001', '深科技', '消费电子')")
    conn.execute(
        """
        INSERT INTO quant_preselect_results VALUES (
            'mrgc_v1', '2026-05-29', '000001', 'BUY',
            '{"source":"test"}', '陶博士 MRGC', 'preselect', '2026-05-29 15:01:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO stock_lifecycle_state VALUES (
            '2026-05-29', '000001', 'M4', 'I4', 'S5', 75, 'healthy',
            80, 81, 82, 83
        )
        """
    )
    conn.execute(
        """
        INSERT INTO quant_feature_rps VALUES (
            '000001', '2026-05-29 00:00:00.000000', 95, 96, 97, 98, 99, 100
        )
        """
    )
    conn.execute(
        "INSERT INTO stock_daily_prices VALUES ('000001', '2026-05-29', 12.34, 3.21, 456000000)"
    )
    conn.execute(
        """
        INSERT INTO strategy_run_log VALUES (
            'run-1', 'mrgc_v1', '2026-05-29', 'success', 1, NULL,
            '2026-05-29 15:00:00', '2026-05-29 15:00:02'
        )
        """
    )
    conn.commit()
    conn.close()


def test_strategy_results_uses_date_indexed_joins(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_strategy_db(db_path)
    statements = []

    class TracingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            statements.append(sql)
            return super().execute(sql, parameters)

    def connect():
        conn = sqlite3.connect(db_path, factory=TracingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(quant_api, "DB_PATH", db_path)
    monkeypatch.setattr(quant_api, "_connect", connect)
    quant_api._strategy_page_cache.clear()
    quant_api._trade_date_cache.clear()

    data = asyncio.run(
        quant_api.get_strategy_results("2026-05-29", strategy_name="mrgc_v1", limit=10)
    ).data

    strategy_sql = next(sql for sql in statements if "FROM quant_preselect_results qpr" in sql)
    assert "sls.trade_date = ? AND sls.symbol = qpr.symbol" in strategy_sql
    assert "rps.symbol = qpr.symbol" in strategy_sql
    assert "rps.trade_date >= ?" in strategy_sql
    assert "rps.trade_date < DATE(?, '+1 day')" in strategy_sql
    assert "p.trade_date = ? AND p.symbol = qpr.symbol" in strategy_sql
    assert "LIKE qpr.trade_date" not in strategy_sql

    row = data["rows"][0]
    assert data["total"] == 1
    assert row["symbol"] == "000001"
    assert row["rps_5"] == 95
    assert row["rps_250"] == 100
    assert row["pct_chg"] == 3.21
    assert data["calculation"]["calculated"] is True
    assert data["calculation"]["status"] == "success"
    assert data["calculation"]["signal_count"] == 1


def test_formula_results_are_isolated_paginated_and_enriched(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_strategy_db(db_path)
    name = "near_year_high_v1"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE quant_formula_results AS SELECT * FROM quant_preselect_results WHERE 0")
        conn.executemany(
            "INSERT INTO quant_formula_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (name, "2026-05-29", symbol, "MATCH", '{"ratio":0.95}', "接近一年新高",
                 "formula", "2026-05-29 15:01:00")
                for symbol in ("000001", "000002")
            ],
        )
        conn.execute(
            "INSERT INTO strategy_run_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("formula", name, "2026-05-29", "success", 2, None, "15:00", "15:01"),
        )
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    page1 = asyncio.run(
        quant_api.get_strategy_results("2026-05-29", strategy_name=name, limit=1)
    ).data
    page2 = asyncio.run(
        quant_api.get_strategy_results("2026-05-29", strategy_name=name, limit=1, offset=1)
    ).data
    legacy = asyncio.run(quant_api.get_strategy_results("2026-05-29", limit=10)).data

    assert page1["total"] == page2["total"] == 2
    assert page1["rows"][0]["symbol"] == "000001"
    assert page1["rows"][0]["close"] == 12.34
    assert page1["rows"][0]["meta"] == {"ratio": 0.95}
    assert page2["rows"][0]["symbol"] == "000002"
    assert [row["strategy_name"] for row in legacy["rows"]] == ["mrgc_v1"]


def test_formula_runs_distinguish_missing_zero_and_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_strategy_db(db_path)
    success, failed, missing = quant_api.FORMULA_STRATEGY_NAMES[:3]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO strategy_run_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("zero", success, "2026-05-29", "success", 0, None, "15:00", "15:01"),
                ("failed", failed, "2026-05-29", "failed", 0, "输入缺失", "15:00", "15:01"),
                ("older", missing, "2026-05-28", "success", 3, None, "15:00", "15:01"),
            ],
        )
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    runs = asyncio.run(quant_api.get_formula_strategy_runs("2026-05-29")).data["runs"]
    assert runs[success] == {"status": "success", "signal_count": 0}
    assert runs[failed]["status"] == "failed"
    assert runs[missing] == {"status": "not_run", "signal_count": None}
    for name, status in [(success, "success"), (failed, "failed"), (missing, "not_run")]:
        result = asyncio.run(
            quant_api.get_strategy_results("2026-05-29", strategy_name=name, limit=10)
        ).data
        assert result["calculation"]["status"] == status
        assert result["rows"] == []


def test_strategy_results_distinguishes_successful_zero_result(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_strategy_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM quant_preselect_results")
        conn.execute("UPDATE strategy_run_log SET signal_count = 0")
        conn.commit()

    monkeypatch.setattr(quant_api, "DB_PATH", db_path)
    quant_api._strategy_page_cache.clear()
    quant_api._trade_date_cache.clear()

    data = asyncio.run(
        quant_api.get_strategy_results("2026-05-29", strategy_name="mrgc_v1", limit=10)
    ).data

    assert data["rows"] == []
    assert data["total"] == 0
    assert data["calculation"]["calculated"] is True
    assert data["calculation"]["signal_count"] == 0


def test_strategy_results_keeps_explicit_missing_date_instead_of_falling_back(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "quant.db"
    make_strategy_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('000001', '2026-06-01', 12.80, 1.20, 500000000)"
        )
        conn.commit()

    monkeypatch.setattr(quant_api, "DB_PATH", db_path)
    quant_api._strategy_page_cache.clear()
    quant_api._trade_date_cache.clear()

    data = asyncio.run(
        quant_api.get_strategy_results("2026-06-01", strategy_name="mrgc_v1", limit=10)
    ).data

    assert data["date"] == "2026-06-01"
    assert data["rows"] == []
    assert data["total"] == 0
    assert data["calculation"]["calculated"] is False
    assert data["calculation"]["status"] == "not_run"
    assert data["calculation"]["needs_recalculation"] is True


def test_strategy_results_rejects_invalid_explicit_date(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_strategy_db(db_path)

    monkeypatch.setattr(quant_api, "DB_PATH", db_path)
    quant_api._strategy_page_cache.clear()
    quant_api._trade_date_cache.clear()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            quant_api.get_strategy_results(
                "not-a-date",
                strategy_name="mrgc_v1",
                limit=10,
            )
        )

    assert exc_info.value.status_code == 400
