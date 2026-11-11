from contextlib import closing
from datetime import date
import json
import sqlite3

import pytest

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


FROZEN_DATE = "2026-10-26"
SECOND_DATE = "2026-10-27"


def snapshot_tables(path, tables):
    with closing(sqlite3.connect(path)) as conn:
        return {
            table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in tables
        }


@pytest.fixture
def frozen_lifecycle_db(tmp_path):
    path = tmp_path / "frozen_lifecycle_fixture.db"
    make_lifecycle_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        for day in (FROZEN_DATE, SECOND_DATE):
            conn.execute("INSERT INTO stock_daily_prices SELECT symbol, ? "
                         "FROM stock_daily_prices WHERE trade_date=?", (day, TRADE_DATE))
            conn.execute("INSERT INTO quant_feature_rps SELECT symbol, ?, "
                         "rps_20,rps_50,rps_120,rps_250 FROM quant_feature_rps "
                         "WHERE trade_date=?", (day, TRADE_DATE))
            conn.execute("INSERT INTO structure_health_daily SELECT symbol, ?, "
                         "structure_score,structure_state FROM structure_health_daily "
                         "WHERE trade_date=?", (day, TRADE_DATE))
            conn.execute("INSERT INTO industry_strength_daily SELECT ?,sector_name,industry_state "
                         "FROM industry_strength_daily WHERE trade_date=?", (day, TRADE_DATE))
            conn.execute("INSERT INTO market_strength_daily VALUES (?, 'M3', 80)", (day,))
            conn.execute("INSERT INTO stock_daily_prices VALUES ('601995', ?)", (day,))
            conn.execute("INSERT INTO quant_feature_rps VALUES ('601995', ?, 90,88,80,70)",
                         (day,))
            conn.execute("INSERT INTO structure_health_daily VALUES ('601995', ?,80,'HEALTHY')",
                         (day,))
        conn.execute("INSERT INTO stock_info VALUES ('601995','Frozen member')")
        conn.execute("INSERT INTO stock_sector_map VALUES ('601995','行业A')")
        conn.executescript("""
            CREATE TABLE stock_daily_prices_raw AS SELECT * FROM stock_daily_prices;
            CREATE TABLE data_repair_state (
                trade_date TEXT, dataset TEXT, input_hash TEXT, members_json TEXT,
                PRIMARY KEY (trade_date, dataset)
            );
            CREATE TABLE portfolio_accounts (account_id TEXT, name TEXT);
            CREATE TABLE portfolio_positions (account_id TEXT, symbol TEXT, quantity INTEGER);
            CREATE TABLE portfolio_daily_snapshots (trade_date TEXT, equity REAL);
            INSERT INTO portfolio_accounts VALUES ('fixture', 'Untouched');
            INSERT INTO portfolio_positions VALUES ('fixture','601995',100);
            INSERT INTO portfolio_daily_snapshots VALUES ('2026-10-26',1000);
        """)
    # Seed existing rows before freezing; every calculator in this file uses a tmp_path DB.
    result = LifecycleStateCalculator(str(path)).run_daily(
        target_dates=[FROZEN_DATE, SECOND_DATE]
    )
    assert result["success"] is True
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executemany("INSERT INTO data_repair_state VALUES (?, 'lifecycle', ?, ?)", [
            (FROZEN_DATE, "unchanged-26", json.dumps(["000001", "000002", "601995"])),
            (SECOND_DATE, "unchanged-27", json.dumps(["000002"])),
        ])
    return path


PROTECTED_TABLES = (
    "quant_stock_pool", "stock_pools", "stock_pool_members", "data_repair_state",
    "portfolio_accounts", "portfolio_positions", "portfolio_daily_snapshots",
    "stock_daily_prices", "stock_daily_prices_raw",
)


@pytest.mark.parametrize("missing_only", [False, True])
def test_frozen_only_member_backfills_one_row_and_is_idempotent(
    frozen_lifecycle_db, missing_only
):
    path = frozen_lifecycle_db
    protected = snapshot_tables(path, PROTECTED_TABLES)
    before = snapshot_tables(path, ["stock_lifecycle_state"])
    calculator = LifecycleStateCalculator(str(path))

    result = calculator.run_daily(
        target_dates=[FROZEN_DATE], symbols=["601995"], missing_only=missing_only
    )

    assert result["success"] is True
    assert result["records"] == 1
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT name,data_status FROM stock_lifecycle_state "
                           "WHERE trade_date=? AND symbol='601995'", (FROZEN_DATE,)).fetchone()
    assert row == ("Frozen member", "OK")
    after = snapshot_tables(path, ["stock_lifecycle_state"])["stock_lifecycle_state"]
    assert [row for row in after if row[1] != "601995"] == before["stock_lifecycle_state"]
    assert snapshot_tables(path, PROTECTED_TABLES) == protected

    repeated = calculator.run_daily(
        target_dates=[FROZEN_DATE], symbols=["601995"], missing_only=missing_only
    )
    assert repeated["success"] is True
    assert repeated["records"] == 1
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM stock_lifecycle_state "
                            "WHERE trade_date=? AND symbol='601995'",
                            (FROZEN_DATE,)).fetchone()[0] == 1
    repeated_rows = snapshot_tables(path, ["stock_lifecycle_state"])["stock_lifecycle_state"]
    assert [row[:-2] for row in repeated_rows] == [row[:-2] for row in after]
    assert snapshot_tables(path, PROTECTED_TABLES) == protected


@pytest.mark.parametrize("symbols", [None, ["000001", "601995"]])
def test_each_output_date_uses_its_own_frozen_universe(frozen_lifecycle_db, symbols):
    path = frozen_lifecycle_db
    result = LifecycleStateCalculator(str(path)).run_daily(
        target_dates=[FROZEN_DATE, SECOND_DATE], symbols=symbols
    )
    assert result["success"] is True
    assert result["records"] == (4 if symbols is None else 2)
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("SELECT trade_date,symbol FROM stock_lifecycle_state "
                            "ORDER BY trade_date,symbol").fetchall()
    if symbols is None:
        assert rows == [(FROZEN_DATE, "000001"), (FROZEN_DATE, "000002"),
                        (FROZEN_DATE, "601995"), (SECOND_DATE, "000002")]
    else:
        # Targeted saves do not delete existing rows outside the date's intersection.
        assert (SECOND_DATE, "601995") not in rows
        assert (FROZEN_DATE, "601995") in rows


def test_current_pool_changes_do_not_change_frozen_history(frozen_lifecycle_db):
    path = frozen_lifecycle_db
    calculator = LifecycleStateCalculator(str(path))
    assert calculator.run_daily(target_dates=[FROZEN_DATE])["records"] == 3
    before = snapshot_tables(path, ["stock_lifecycle_state"])["stock_lifecycle_state"]
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("DELETE FROM quant_stock_pool")
        conn.execute("DELETE FROM stock_pool_members")
        conn.execute("INSERT INTO quant_stock_pool VALUES ('000003','New member','core_pool',1)")
    assert calculator.run_daily(target_dates=[FROZEN_DATE])["records"] == 3
    after = snapshot_tables(path, ["stock_lifecycle_state"])["stock_lifecycle_state"]
    assert [row[:-2] for row in after] == [row[:-2] for row in before]


@pytest.mark.parametrize("payload", [
    "broken-json", "{}", '["601995",null]', '["１２３４５６"]', '["123"]',
    "[]", "x" * 100001, json.dumps(["601995"] * 10001),
], ids=["json", "object", "null-member", "unicode", "short", "empty", "size", "count"])
def test_invalid_frozen_members_fail_without_any_writes(frozen_lifecycle_db, payload):
    path = frozen_lifecycle_db
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_repair_state SET members_json=? WHERE trade_date=?",
                     (payload, FROZEN_DATE))
    tables = (*PROTECTED_TABLES, "stock_lifecycle_state")
    before = snapshot_tables(path, tables)
    result = LifecycleStateCalculator(str(path)).run_daily(
        target_dates=[FROZEN_DATE], symbols=["601995"]
    )
    assert result["success"] is False
    assert result["records"] == 0
    assert snapshot_tables(path, tables) == before


@pytest.mark.parametrize("target_dates,symbols", [
    ([FROZEN_DATE], []), ([FROZEN_DATE], [" "]),
    (["not-a-date"], ["601995"]), (["2026-02-30"], None),
    ([FROZEN_DATE, "invalid"], None), ([FROZEN_DATE + "garbage"], None),
    ([], None), ([FROZEN_DATE], ["bad-symbol"]),
])
def test_invalid_target_input_fails_before_schema_creation(tmp_path, target_dates, symbols):
    path = tmp_path / "invalid_input_fixture.db"
    make_lifecycle_db(path)
    with closing(sqlite3.connect(path)) as conn:
        before = list(conn.iterdump())
    result = LifecycleStateCalculator(str(path)).run_daily(
        target_dates=target_dates, symbols=symbols
    )
    assert result["success"] is False
    assert result["records"] == 0
    with closing(sqlite3.connect(path)) as conn:
        assert list(conn.iterdump()) == before


def test_corrupt_second_date_does_not_partially_save_first(frozen_lifecycle_db):
    path = frozen_lifecycle_db
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE data_repair_state SET members_json='broken' WHERE trade_date=?",
                     (SECOND_DATE,))
    before = snapshot_tables(path, ["stock_lifecycle_state"])
    result = LifecycleStateCalculator(str(path)).run_daily(
        target_dates=[FROZEN_DATE, SECOND_DATE]
    )
    assert result["success"] is False
    assert snapshot_tables(path, ["stock_lifecycle_state"]) == before


def test_targeted_save_failure_rolls_back_deleted_and_inserted_rows(frozen_lifecycle_db):
    path = frozen_lifecycle_db
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TRIGGER fixture_reject_insert BEFORE INSERT ON stock_lifecycle_state
            WHEN NEW.symbol='601995' BEGIN SELECT RAISE(ABORT, 'fixture_insert_failed'); END;
        """)
    before = snapshot_tables(path, ["stock_lifecycle_state"])
    result = LifecycleStateCalculator(str(path)).run_daily(
        target_dates=[FROZEN_DATE], symbols=["000002", "601995"]
    )
    assert result["success"] is False
    assert "fixture_insert_failed" in result["error"]
    assert snapshot_tables(path, ["stock_lifecycle_state"]) == before


def test_current_day_default_universe_remains_live(tmp_path):
    path = tmp_path / "current_day_fixture.db"
    make_lifecycle_db(path)
    today = date.today().isoformat()
    with closing(sqlite3.connect(path)) as conn, conn:
        for table in ("stock_daily_prices", "quant_feature_rps", "structure_health_daily",
                      "industry_strength_daily", "market_strength_daily"):
            conn.execute(f"UPDATE {table} SET trade_date=?", (today,))
        conn.execute("CREATE TABLE data_repair_state "
                     "(trade_date TEXT,dataset TEXT,members_json TEXT)")
        conn.execute("INSERT INTO data_repair_state VALUES (?,'lifecycle','[\"601995\"]')",
                     (today,))
    result = LifecycleStateCalculator(str(path)).run_daily()
    assert result["success"] is True
    assert result["records"] == 2
    assert {row["symbol"] for row in read_states(path)} == {"000001", "000002"}


def test_targeted_save_preserves_symbols_excluded_by_other_dates_freeze(frozen_lifecycle_db):
    path = frozen_lifecycle_db
    with closing(sqlite3.connect(path)) as conn:
        before = conn.execute("SELECT * FROM stock_lifecycle_state "
                              "WHERE trade_date=? AND symbol='000001'",
                              (SECOND_DATE,)).fetchone()
    result = LifecycleStateCalculator(str(path)).run_daily(
        target_dates=[FROZEN_DATE, SECOND_DATE], symbols=["000001", "000002"]
    )
    assert result["success"] is True
    assert result["records"] == 3
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT * FROM stock_lifecycle_state "
                            "WHERE trade_date=? AND symbol='000001'",
                            (SECOND_DATE,)).fetchone() == before


def test_frozen_reader_and_resolver_work_on_readonly_fixture(frozen_lifecycle_db):
    from data_core.repair_plan import _date_members, read_frozen_members

    path = frozen_lifecycle_db
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        frozen = read_frozen_members(conn, FROZEN_DATE, SECOND_DATE)
        assert _date_members(conn, FROZEN_DATE, "lifecycle", {"000003"}, frozen,
                             audited=False) == {"000001", "000002", "601995"}
        assert _date_members(conn, SECOND_DATE, "lifecycle", {"000003"}, frozen,
                             audited=False) == {"000002"}
        assert conn.total_changes == 0


def test_corrupt_freeze_does_not_create_target_table(tmp_path):
    path = tmp_path / "corrupt_before_schema_fixture.db"
    make_lifecycle_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE data_repair_state "
                     "(trade_date TEXT,dataset TEXT,members_json TEXT)")
        conn.execute("INSERT INTO data_repair_state VALUES (?,'lifecycle','broken')",
                     (TRADE_DATE,))
        before = list(conn.iterdump())
    result = LifecycleStateCalculator(str(path)).run_daily(target_dates=[TRADE_DATE])
    assert result["success"] is False
    with closing(sqlite3.connect(path)) as conn:
        assert list(conn.iterdump()) == before
