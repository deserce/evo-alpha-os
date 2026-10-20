import sqlite3

from data_core.bse_codes import to_bse_tdx_symbol
from data_core.quality import (
    actual_stock_count,
    generate_stock_daily_quality_report,
    report_allows_rps,
)
from data_core.schema import (
    create_core_tables,
    ensure_data_core_schema,
    refresh_trading_calendar_from_history,
    table_columns,
)
from data_core.source_registry import (
    MODULE_STOCK_DAILY,
    official_source_ids,
    is_official_write_source,
)
from data_core.symbols import infer_market, normalize_symbol
from scripts.inspect_data_core_inventory import build_report
from scripts.run_daily_data_workflow import build_parser, make_tasks


def test_symbol_normalization_and_market_mapping():
    assert normalize_symbol("000001.SZ") == "000001"
    assert normalize_symbol("sh600519") == "600519"
    assert infer_market("000001") == "SZ"
    assert infer_market("600519") == "SH"
    assert infer_market("920002") == "BSE"
    assert infer_market("830799") == "BSE"
    assert infer_market("abc") == "UNKNOWN"
    assert to_bse_tdx_symbol("920002") == "920002"
    assert to_bse_tdx_symbol("430047") == "920047"
    assert to_bse_tdx_symbol("830799") == "920799"
    assert to_bse_tdx_symbol("871396") == "920396"


def test_official_stock_daily_sources_are_tdx_only():
    assert official_source_ids(MODULE_STOCK_DAILY) == ("tdx_mootdx_qfq", "tdx_standard_bj")
    assert is_official_write_source("tdx_mootdx_qfq", MODULE_STOCK_DAILY)
    assert is_official_write_source("tdx_standard_bj", MODULE_STOCK_DAILY)
    assert not is_official_write_source("tdx_exhq", MODULE_STOCK_DAILY)
    assert not is_official_write_source("akshare_legacy", MODULE_STOCK_DAILY)
    assert not is_official_write_source("multi_source_auto", MODULE_STOCK_DAILY)


def test_data_core_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                close REAL
            );
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                price REAL
            );
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15 15:00:00', 10.0);
            """
        )

    first = ensure_data_core_schema(db_path)
    second = ensure_data_core_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        assert "data_quality_report" in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "stock_daily_gap_exception" in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        daily_columns = table_columns(conn, "stock_daily_prices")
        assert {"source", "quality_flag", "run_id", "updated_at", "adjustment_mode", "market"} <= daily_columns
        assert first["calendar_rows_added"] == 1
        assert second["calendar_rows_added"] == 0


def test_stock_daily_date_filter_uses_indexed_half_open_range(tmp_path):
    db_path = tmp_path / "local_quant.db"
    traced: list[str] = []
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT NOT NULL
            );
            CREATE INDEX idx_stock_daily_prices_trade_date
            ON stock_daily_prices (trade_date);
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15'),
                ('000002', '2026-05-15 15:00:00'),
                ('000003', '2026-05-16');
            """
        )
        conn.set_trace_callback(traced.append)

        assert actual_stock_count(conn, "2026-05-15", "SZ") == 2

        query = next(
            statement
            for statement in reversed(traced)
            if "FROM stock_daily_prices" in statement
        )
        plan = conn.execute(f"EXPLAIN QUERY PLAN {query}").fetchall()

    plan_text = " ".join(str(row[3]) for row in plan)
    assert (
        "SEARCH stock_daily_prices USING INDEX idx_stock_daily_prices_trade_date"
        in plan_text
    )
    assert "trade_date>? AND trade_date<?" in plan_text


def test_trading_calendar_refresh_uses_indexed_history_range(tmp_path):
    db_path = tmp_path / "local_quant.db"
    traced: list[str] = []
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT NOT NULL
            );
            CREATE INDEX idx_stock_daily_prices_trade_date
            ON stock_daily_prices (trade_date);
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15 15:00:00'),
                ('600519', '2026-05-16 15:00:00');
            """
        )
        create_core_tables(conn)
        conn.execute(
            """
            INSERT INTO trading_calendar (trade_date, market, is_open, source)
            VALUES ('2026-05-15', 'SZ', 1, 'test')
            """
        )
        conn.set_trace_callback(traced.append)

        assert refresh_trading_calendar_from_history(conn) == 1

        query = next(
            statement
            for statement in reversed(traced)
            if "INSERT OR IGNORE INTO trading_calendar" in statement
        )
        plan = conn.execute(f"EXPLAIN QUERY PLAN {query}").fetchall()
        dates = conn.execute(
            "SELECT trade_date, market FROM trading_calendar ORDER BY trade_date, market"
        ).fetchall()

    plan_text = " ".join(str(row[3]) for row in plan)
    assert (
        "SEARCH stock_daily_prices USING INDEX idx_stock_daily_prices_trade_date"
        in plan_text
    )
    assert "trade_date>? AND trade_date<?" in plan_text
    assert dates == [("2026-05-15", "SZ"), ("2026-05-16", "SH")]


def test_schema_backfills_market_metadata(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                close REAL
            );
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                price REAL
            );
            INSERT INTO stock_info VALUES
                ('000001', '平安银行'),
                ('600519', '贵州茅台'),
                ('920002', '万达轴承');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15', 10.0),
                ('600519', '2026-05-15', 1300.0),
                ('920002', '2026-05-15', 20.0);
            INSERT INTO stock_valuation_daily VALUES
                ('000001', '2026-05-15', 10.0),
                ('600519', '2026-05-15', 1300.0),
                ('920002', '2026-05-15', 20.0);
            """
        )

    ensure_data_core_schema(db_path, backfill_legacy_metadata=True)
    ensure_data_core_schema(db_path, backfill_legacy_metadata=True)

    with sqlite3.connect(db_path) as conn:
        assert "market" in table_columns(conn, "stock_info")
        assert conn.execute("SELECT market FROM stock_info WHERE symbol='000001'").fetchone()[0] == "SZ"
        assert conn.execute("SELECT market FROM stock_info WHERE symbol='600519'").fetchone()[0] == "SH"
        assert conn.execute("SELECT market FROM stock_info WHERE symbol='920002'").fetchone()[0] == "BSE"
        assert conn.execute("SELECT market FROM stock_daily_prices WHERE symbol='920002'").fetchone()[0] == "BSE"
        assert conn.execute("SELECT market FROM stock_valuation_daily WHERE code='600519'").fetchone()[0] == "SH"


def test_stock_daily_quality_reports_market_failures(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                turnover_rate REAL
            );
            INSERT INTO stock_info VALUES
                ('000001', '平安银行'),
                ('600519', '贵州茅台'),
                ('920002', '万达轴承');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15', 10, 11, 9, 10.5, 100, 1000, 1.2),
                ('600519', '2026-05-15', 1300, 1310, 1290, 1305, 100, 130500, 0.5);
            """
        )

    reports = generate_stock_daily_quality_report(
        db_path,
        trade_date="2026-05-15",
        run_id="test_run",
    )
    by_market = {report["market"]: report for report in reports}

    assert by_market["SZ"]["status"] == "PASS"
    assert by_market["SH"]["status"] == "PASS"
    assert by_market["BSE"]["status"] == "FAIL"
    assert by_market["BSE"]["missing_count"] == 1
    assert not report_allows_rps(reports)


def test_stock_daily_quality_rejects_cross_symbol_cloned_ohlcv(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                turnover_rate REAL
            );
            INSERT INTO stock_info VALUES
                ('601696', '中银证券'),
                ('603359', '东珠生态');
            INSERT INTO stock_daily_prices VALUES
                ('601696', '2026-08-07', 1308.66, 1315.28, 1301, 1309.22, 2497581, 3266919424, 1.0),
                ('603359', '2026-08-07', 1308.66, 1315.28, 1301, 1309.22, 2497581, 3266919424, 1.0);
            """
        )

    reports = generate_stock_daily_quality_report(
        db_path,
        trade_date="2026-08-07",
        run_id="clone_guard",
        markets=("SH",),
    )

    assert reports[0]["status"] == "FAIL"
    assert reports[0]["abnormal_count"] == 2
    assert '"cross_symbol_clone_count": 2' in reports[0]["detail_json"]
    assert not report_allows_rps(reports)


def test_stock_daily_quality_rejects_cross_day_symbol_mismatch(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (symbol TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL,
                close REAL, volume REAL, amount REAL, turnover_rate REAL,
                pct_chg REAL, PRIMARY KEY (symbol, trade_date)
            );
            INSERT INTO stock_info VALUES ('688022', '瀚川智能');
            INSERT INTO stock_daily_prices VALUES
                ('688022', '2026-09-21', 8.74, 8.99, 8.74, 8.99, 2226113, 19714620, 1.0, 2.63),
                ('688022', '2026-09-22', 114.68, 125.85, 114.61, 123.60, 13012229, 1591593216, 5.55, 10.06);
            """
        )

    reports = generate_stock_daily_quality_report(
        db_path, trade_date="2026-09-22", run_id="cross_day_guard", markets=("SH",)
    )

    assert reports[0]["status"] == "FAIL"
    assert reports[0]["abnormal_count"] == 1
    assert '"cross_day_pct_mismatch_count": 1' in reports[0]["detail_json"]


def test_stock_daily_quality_uses_effective_missing_after_gap_exception(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                turnover_rate REAL
            );
            INSERT INTO stock_info VALUES
                ('000001', '平安银行'),
                ('000002', '万科A');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15 15:00:00', 10, 11, 9, 10.5, 100, 1000, 1.2);
            """
        )

    ensure_data_core_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO stock_daily_gap_exception (
                symbol, trade_date, market, reason, source, attempts, status
            ) VALUES (
                '000002', '2026-05-15 15:00:00', 'SZ', 'tdx_no_raw_data',
                'tdx_mootdx_qfq', 1, 'accepted_no_raw_data'
            )
            """
        )
        conn.commit()

    reports = generate_stock_daily_quality_report(
        db_path,
        trade_date="2026-05-15",
        run_id="test_run",
        markets=("SZ",),
    )

    detail = reports[0]["detail_json"]
    assert reports[0]["missing_count"] == 1
    assert reports[0]["status"] == "PASS"
    assert '"raw_missing_count": 1' in detail
    assert '"exception_count": 1' in detail
    assert '"effective_missing_count": 0' in detail
    assert report_allows_rps(reports)


def test_stock_daily_quality_excludes_not_yet_listed_symbols(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                list_date TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                turnover_rate REAL
            );
            INSERT INTO stock_info VALUES
                ('000001', '平安银行', '19910403'),
                ('000002', '万科A', '20260518');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15', 10, 11, 9, 10.5, 100, 1000, 1.2);
            """
        )

    reports = generate_stock_daily_quality_report(
        db_path,
        trade_date="2026-05-15",
        run_id="test_run",
        markets=("SZ",),
    )

    assert reports[0]["expected_count"] == 1
    assert reports[0]["missing_count"] == 0
    assert reports[0]["status"] == "PASS"


def test_bse_standard_unadjusted_rows_do_not_fail_on_missing_turnover(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                turnover_rate REAL,
                source TEXT,
                adjustment_mode TEXT
            );
            INSERT INTO stock_info VALUES ('920002', '万达轴承');
            INSERT INTO stock_daily_prices VALUES
                ('920002', '2026-05-15', 20, 21, 19, 20.5, 100, 2050, NULL, 'tdx_standard_bj', 'none');
            """
        )

    reports = generate_stock_daily_quality_report(
        db_path,
        trade_date="2026-05-15",
        run_id="test_run",
        markets=("BSE",),
    )

    assert reports[0]["status"] == "PASS"
    assert reports[0]["abnormal_count"] == 0
    assert '"tdx_standard_bj": 1' in reports[0]["detail_json"]
    assert '"none": 1' in reports[0]["detail_json"]


def test_inventory_report_is_read_only_and_lists_sources(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                market TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                close REAL,
                market TEXT
            );
            INSERT INTO stock_info VALUES
                ('000001', '平安银行', 'SZ'),
                ('600519', '贵州茅台', 'SH'),
                ('920002', '万达轴承', 'BSE');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15', 10.0, 'SZ'),
                ('600519', '2026-05-15', 1300.0, 'SH');
            """
        )
        before = conn.execute("SELECT COUNT(*) FROM stock_daily_prices").fetchone()[0]

    report = build_report(db_path)

    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM stock_daily_prices").fetchone()[0]

    assert before == after
    assert "tdx_mootdx_qfq" in report
    assert "akshare_legacy" in report
    assert "stock_daily_prices" in report
    assert "Formal stock daily writes are TDX-only" in report


def test_workflow_plan_contains_data_core_gates(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                turnover_rate REAL
            );
            INSERT INTO stock_daily_prices VALUES ('000001', '2026-05-15', 1.3);
            """
        )
    import scripts.run_daily_data_workflow as workflow

    monkeypatch.setattr(workflow, "DB_PATH", db_path)
    args = build_parser().parse_args(["--kline-end", "2026-05-15"])
    names = [task.name for task in make_tasks(args)]

    assert names[0] == "mootdx_qfq_stock_kline"
    assert "tdx_source_capability" not in names
    assert "data_core_stock_daily_quality" in names
    assert "stock_daily_gap_repair" in names
    assert names.index("stock_daily_quality_repair") < names.index("stock_daily_gap_repair")
    assert names.index("stock_daily_gap_repair") < names.index("data_core_stock_daily_quality")
    assert names.index("data_core_stock_daily_quality") < names.index("stock_valuation")
