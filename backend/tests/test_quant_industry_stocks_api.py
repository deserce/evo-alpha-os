import asyncio
import sqlite3

from app.api.v1 import quant as quant_api


def make_industry_stocks_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE stock_sector_map (
            sector_name TEXT,
            symbol TEXT,
            name TEXT,
            sector_type TEXT,
            PRIMARY KEY (sector_name, symbol)
        );

        CREATE TABLE stock_info (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT
        );

        CREATE TABLE stock_daily_prices (
            symbol TEXT,
            trade_date TEXT,
            close REAL,
            pct_chg REAL,
            amount REAL,
            volume REAL,
            PRIMARY KEY (symbol, trade_date)
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

        CREATE TABLE stock_lifecycle_state (
            trade_date TEXT,
            symbol TEXT,
            name TEXT,
            sector_name TEXT,
            lifecycle_state TEXT,
            state_rank INTEGER,
            market_state TEXT,
            industry_state TEXT,
            structure_score REAL,
            structure_state TEXT,
            signal_type TEXT,
            entry_reason TEXT,
            exit_reason TEXT,
            is_active INTEGER,
            PRIMARY KEY (trade_date, symbol)
        );
        """
    )
    conn.execute("INSERT INTO stock_sector_map VALUES ('半导体', '688766', '普冉股份', 'Industry')")
    conn.execute("INSERT INTO stock_info VALUES ('688766', '普冉股份', '半导体')")
    conn.execute(
        "INSERT INTO stock_daily_prices VALUES ('688766', '2026-05-29', 88.8, 3.2, 120000000, 900000)"
    )
    conn.execute(
        """
        INSERT INTO quant_feature_rps VALUES (
            '688766', '2026-05-29 00:00:00.000000', 91, 92, 93, 94, 95, 96
        )
        """
    )
    conn.execute(
        """
        INSERT INTO stock_lifecycle_state VALUES (
            '2026-05-29', '688766', '普冉股份', '半导体', 'S5', 5,
            'M4', 'I4', 75, 'healthy', 'BUY', 'entry', NULL, 1
        )
        """
    )
    conn.commit()
    conn.close()


def test_industry_stocks_uses_exact_date_joins(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_industry_stocks_db(db_path)
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
    quant_api._trade_date_cache.clear()

    data = asyncio.run(
        quant_api.get_industry_stocks(
            sector_name="半导体",
            trade_date="2026-05-29",
            rps_period=20,
            sort_by="rps_20",
            sort_dir="desc",
            limit=20,
        )
    ).data

    main_sql = next(sql for sql in statements if "FROM sector_symbols ss" in sql)
    assert "LEFT JOIN stock_daily_prices p ON p.trade_date = ?" in main_sql
    assert "LEFT JOIN quant_feature_rps r ON r.trade_date = ?" in main_sql
    assert "LEFT JOIN stock_lifecycle_state l ON l.trade_date = ?" in main_sql
    assert "latest_price" not in main_sql
    assert "MAX(DATE" not in main_sql

    row = data["rows"][0]
    assert data["total"] == 1
    assert row["symbol"] == "688766"
    assert row["rps_20"] == 93
    assert row["pct_chg"] == 3.2
    assert row["lifecycle_state"] == "S5"
