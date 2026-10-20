import sqlite3

from quant_engine.calculators.lifecycle_state_calculator import LifecycleStateCalculator


TRADE_DATE = "2026-07-09"


def make_lifecycle_db(path, *, include_manual_industry=True, old_target=False):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT);
        CREATE TABLE quant_stock_pool (
            symbol TEXT, name TEXT, pool_name TEXT, is_active INTEGER
        );
        CREATE TABLE stock_info (symbol TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE stock_pools (
            pool_id TEXT PRIMARY KEY,
            monitor_enabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE stock_pool_members (pool_id TEXT, symbol TEXT);
        CREATE TABLE quant_feature_rps (
            symbol TEXT, trade_date TEXT, rps_20 REAL, rps_50 REAL,
            rps_120 REAL, rps_250 REAL
        );
        CREATE TABLE structure_health_daily (
            symbol TEXT, trade_date TEXT, structure_score REAL, structure_state TEXT
        );
        CREATE TABLE stock_sector_map (symbol TEXT, sector_name TEXT);
        CREATE TABLE industry_strength_daily (
            trade_date TEXT, sector_name TEXT, industry_state TEXT
        );
        CREATE TABLE market_strength_daily (
            trade_date TEXT, market_state TEXT, market_strength_score REAL
        );

        INSERT INTO stock_daily_prices VALUES
            ('000001', '2026-07-09'), ('000002', '2026-07-09');
        INSERT INTO quant_stock_pool VALUES
            ('000001', '核心股', 'core_pool', 1);
        INSERT INTO stock_info VALUES
            ('000001', '核心股'), ('000002', '手工股');
        INSERT INTO stock_pools VALUES ('watchlist', 1);
        INSERT INTO stock_pool_members VALUES
            ('watchlist', '000001'), ('watchlist', '000002');
        INSERT INTO quant_feature_rps VALUES
            ('000001', '2026-07-09', 90, 88, 80, 70),
            ('000002', '2026-07-09', 91, 89, 81, 71);
        INSERT INTO structure_health_daily VALUES
            ('000001', '2026-07-09', 80, 'HEALTHY'),
            ('000002', '2026-07-09', 82, 'HEALTHY');
        INSERT INTO stock_sector_map VALUES
            ('000001', '行业A'), ('000002', '行业B');
        INSERT INTO industry_strength_daily VALUES
            ('2026-07-09', '行业A', 'I3');
        INSERT INTO market_strength_daily VALUES
            ('2026-07-09', 'M3', 80);
        """
    )
    if include_manual_industry:
        conn.execute(
            "INSERT INTO industry_strength_daily VALUES (?, ?, ?)",
            (TRADE_DATE, "行业B", "I3"),
        )
    if old_target:
        conn.executescript(
            """
            CREATE TABLE stock_lifecycle_state (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                sector_name TEXT,
                lifecycle_state TEXT NOT NULL,
                state_rank INTEGER NOT NULL,
                market_state TEXT,
                industry_state TEXT,
                rps_20 REAL,
                rps_50 REAL,
                rps_120 REAL,
                rps_250 REAL,
                structure_score REAL,
                structure_state TEXT,
                signal_type TEXT,
                entry_reason TEXT,
                exit_reason TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (trade_date, symbol)
            );
            """
        )
    conn.commit()
    conn.close()


def read_states(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT symbol, lifecycle_state, universe_source, data_status, signal_type
        FROM stock_lifecycle_state
        ORDER BY symbol
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def test_manual_monitored_stock_joins_core_universe_and_is_deduplicated(tmp_path):
    db_path = tmp_path / "lifecycle.db"
    make_lifecycle_db(db_path)

    result = LifecycleStateCalculator(str(db_path)).run_daily(days=1)
    rows = read_states(db_path)

    assert result["success"] is True
    assert result["records"] == 2
    assert rows[0]["universe_source"] == "both"
    assert rows[1]["symbol"] == "000002"
    assert rows[1]["universe_source"] == "manual"
    assert rows[1]["data_status"] == "OK"


def test_unmonitored_custom_group_stock_is_included_for_opportunity_scans(tmp_path):
    db_path = tmp_path / "all_manual_groups.db"
    make_lifecycle_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO stock_info VALUES ('000003', '分组股')")
        conn.execute("INSERT INTO stock_pools VALUES ('custom', 0)")
        conn.execute("INSERT INTO stock_pool_members VALUES ('custom', '000003')")
        conn.execute("INSERT INTO stock_daily_prices VALUES ('000003', ?)", (TRADE_DATE,))
        conn.execute(
            "INSERT INTO quant_feature_rps VALUES ('000003', ?, 92, 90, 82, 72)",
            (TRADE_DATE,),
        )
        conn.execute(
            "INSERT INTO structure_health_daily VALUES ('000003', ?, 83, 'HEALTHY')",
            (TRADE_DATE,),
        )
        conn.execute("INSERT INTO stock_sector_map VALUES ('000003', '行业C')")
        conn.execute(
            "INSERT INTO industry_strength_daily VALUES (?, '行业C', 'I3')",
            (TRADE_DATE,),
        )

    result = LifecycleStateCalculator(str(db_path)).run_daily(days=1)
    rows = {row["symbol"]: row for row in read_states(db_path)}

    assert result["success"] is True
    assert result["records"] == 3
    assert rows["000003"]["universe_source"] == "manual"
    assert rows["000003"]["data_status"] == "OK"


def test_missing_industry_marks_unknown_and_suppresses_signal(tmp_path):
    db_path = tmp_path / "missing_industry.db"
    make_lifecycle_db(db_path, include_manual_industry=False)

    result = LifecycleStateCalculator(str(db_path)).run_daily(days=1)
    rows = {row["symbol"]: row for row in read_states(db_path)}

    assert result["success"] is True
    assert rows["000001"]["lifecycle_state"] != "UNKNOWN"
    assert rows["000002"]["lifecycle_state"] == "UNKNOWN"
    assert "MISSING_INDUSTRY" in rows["000002"]["data_status"]
    assert rows["000002"]["signal_type"] is None


def test_existing_lifecycle_table_gets_new_columns(tmp_path):
    db_path = tmp_path / "old_lifecycle.db"
    make_lifecycle_db(db_path, old_target=True)

    result = LifecycleStateCalculator(str(db_path)).run_daily(days=1)

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_lifecycle_state)")}
    conn.close()
    assert result["success"] is True
    assert {"universe_source", "data_status"}.issubset(columns)


def test_targeted_member_refresh_preserves_other_symbols_on_same_date(tmp_path):
    db_path = tmp_path / "targeted_lifecycle.db"
    make_lifecycle_db(db_path)
    calculator = LifecycleStateCalculator(str(db_path))

    assert calculator.run_daily(days=1)["success"] is True
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE structure_health_daily SET structure_score=61 "
            "WHERE symbol='000002' AND trade_date=?",
            (TRADE_DATE,),
        )

    result = calculator.run_daily(
        target_dates=[TRADE_DATE],
        symbols=["000002"],
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, structure_score FROM stock_lifecycle_state "
            "WHERE trade_date=? ORDER BY symbol",
            (TRADE_DATE,),
        ).fetchall()
    assert result["success"] is True
    assert result["records"] == 1
    assert rows == [("000001", 80.0), ("000002", 61.0)]


def test_targeted_member_refresh_rejects_empty_symbol_list(tmp_path):
    db_path = tmp_path / "targeted_empty.db"
    make_lifecycle_db(db_path)

    result = LifecycleStateCalculator(str(db_path)).run_daily(
        target_dates=[TRADE_DATE],
        symbols=[],
    )

    assert result["success"] is False
    assert "symbols" in result["error"]


def test_symbol_backfill_submits_full_160_day_window_in_one_calculation(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "targeted_window.db"
    make_lifecycle_db(db_path)
    calculator = LifecycleStateCalculator(str(db_path))
    calls = []

    def fake_run_daily(**kwargs):
        calls.append(kwargs)
        return {"success": True, "records": len(kwargs["target_dates"]), "elapsed": 0.1}

    monkeypatch.setattr(calculator, "run_daily", fake_run_daily)
    trade_dates = [f"2026-01-{day:02d}" for day in range(1, 11)]

    result = calculator.backfill_symbols(["000002"], trade_dates)

    assert result["success"] is True
    assert len(calls) == 1
    assert calls[0]["symbols"] == ["000002"]
    assert calls[0]["target_dates"] == trade_dates
