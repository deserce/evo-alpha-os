import sqlite3

from scripts import optimize_local_storage as storage


def _make_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                close REAL,
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE quant_feature_rps (
                symbol TEXT,
                trade_date TEXT,
                rps_20 REAL,
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE INDEX idx_rps_date_symbol
                ON quant_feature_rps (trade_date, symbol);
            CREATE TABLE stock_lifecycle_state (
                symbol TEXT,
                trade_date TEXT,
                lifecycle_state TEXT,
                PRIMARY KEY (trade_date, symbol)
            );
            CREATE INDEX idx_lifecycle_date_symbol
                ON stock_lifecycle_state (trade_date, symbol);
            """
        )
        dates = [
            "2026-06-01",
            "2026-06-02",
            "2026-06-03",
            "2026-06-04",
            "2026-06-05",
        ]
        conn.executemany(
            "INSERT INTO stock_daily_prices VALUES (?, ?, ?)",
            [("000001", date, 10.0) for date in dates],
        )
        conn.executemany(
            "INSERT INTO quant_feature_rps VALUES (?, ?, ?)",
            [("000001", date, 80.0) for date in dates],
        )
        conn.executemany(
            "INSERT INTO stock_lifecycle_state VALUES (?, ?, ?)",
            [("000001", date, "S3") for date in dates],
        )


def test_compute_cutoff_trade_date_uses_completed_market_dates(tmp_path):
    db_path = tmp_path / "local_quant.db"
    _make_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert storage.compute_cutoff_trade_date(conn, 3) == "2026-06-03"


def test_compute_cutoff_trade_date_prefers_open_trading_calendar(tmp_path):
    db_path = tmp_path / "local_quant.db"
    _make_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE trading_calendar (
                trade_date TEXT NOT NULL,
                market TEXT NOT NULL,
                is_open INTEGER NOT NULL,
                PRIMARY KEY (trade_date, market)
            )
            """
        )
        conn.executemany(
            "INSERT INTO trading_calendar VALUES (?, 'CN', ?)",
            [
                ("2026-06-01", 1),
                ("2026-06-02", 1),
                ("2026-06-03", 0),
                ("2026-06-04", 1),
                ("2026-06-05", 1),
            ],
        )
        assert storage.compute_cutoff_trade_date(conn, 3) == "2026-06-02"


def test_inspect_storage_reports_rebuildable_rows_and_duplicate_indexes(tmp_path):
    db_path = tmp_path / "local_quant.db"
    _make_db(db_path)

    with sqlite3.connect(db_path) as conn:
        report = storage.inspect_storage(conn, 3)

    assert report["cutoff_trade_date"] == "2026-06-03"
    assert report["tables"]["quant_feature_rps"]["rows_before_cutoff"] == 2
    assert "idx_lifecycle_date_symbol" in report["duplicate_indexes"]


def test_inspect_storage_keeps_descending_index_with_different_query_shape(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE index_daily (
                index_code TEXT,
                trade_date TEXT,
                PRIMARY KEY (index_code, trade_date)
            );
            CREATE INDEX idx_index_code_date
                ON index_daily (index_code, trade_date DESC);
            INSERT INTO stock_daily_prices VALUES ('000001', '2026-06-05');
            """
        )
        report = storage.inspect_storage(conn, 1)

    assert "idx_index_code_date" not in report["duplicate_indexes"]


def test_apply_storage_cleanup_keeps_raw_prices_and_removes_old_features(tmp_path):
    db_path = tmp_path / "local_quant.db"
    _make_db(db_path)

    with sqlite3.connect(db_path) as conn:
        result = storage.apply_storage_cleanup(conn, 3, batch_size=2)
        raw_count = conn.execute("SELECT COUNT(*) FROM stock_daily_prices").fetchone()[0]
        rps_dates = conn.execute(
            "SELECT trade_date FROM quant_feature_rps ORDER BY trade_date"
        ).fetchall()
        lifecycle_dates = conn.execute(
            "SELECT trade_date FROM stock_lifecycle_state ORDER BY trade_date"
        ).fetchall()
        duplicate_index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_lifecycle_date_symbol'"
        ).fetchone()

    assert result["deleted_rows"]["quant_feature_rps"] == 2
    assert result["deleted_rows"]["stock_lifecycle_state"] == 2
    assert raw_count == 5
    assert [row[0] for row in rps_dates] == ["2026-06-03", "2026-06-04", "2026-06-05"]
    assert [row[0] for row in lifecycle_dates] == [
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
    ]
    assert duplicate_index is None
