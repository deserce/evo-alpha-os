import sqlite3

from scripts import repair_stock_daily_quality as repair
from scripts import repair_stock_daily_gaps as gap_repair
from data_core.schema import ensure_data_core_schema


def test_repair_turnover_from_previous_valuation(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
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
                turnover_rate REAL,
                source TEXT,
                quality_flag TEXT,
                run_id TEXT,
                updated_at TEXT,
                adjustment_mode TEXT,
                market TEXT,
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE stock_valuation_daily (
                code TEXT,
                trade_date TEXT,
                price REAL,
                circ_mv REAL
            );
            INSERT INTO stock_daily_prices VALUES (
                '000001', '2026-05-29', 10, 11, 9, 10.5, 1000000, 10500000,
                NULL, 'tdx_mootdx_qfq', 'ok', 'run1', '2026-05-29 16:10:00',
                'qfq', 'SZ'
            );
            INSERT INTO stock_valuation_daily VALUES (
                '000001', '2026-05-28', 10.0, 1000000000.0
            );
            """
        )

    monkeypatch.setattr(repair, "DB_PATH", db_path)
    with sqlite3.connect(db_path) as conn:
        repaired = repair.repair_turnover_from_previous_valuation(conn, "2026-05-29")
        conn.commit()
        row = conn.execute(
            """
            SELECT turnover_rate, quality_flag
            FROM stock_daily_prices
            WHERE symbol = '000001'
            """
        ).fetchone()

    assert repaired == 1
    assert round(row[0], 4) == 1.0
    assert row[1] == "repaired_turnover_local"


def test_abnormal_symbols_detects_null_turnover(tmp_path, monkeypatch):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
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
                turnover_rate REAL,
                source TEXT,
                adjustment_mode TEXT,
                market TEXT
            );
            INSERT INTO stock_daily_prices VALUES (
                '600519', '2026-05-29', 1300, 1310, 1290, 1305, 100, 130500,
                NULL, 'tdx_mootdx_qfq', 'qfq', 'SH'
            );
            INSERT INTO stock_daily_prices VALUES (
                '920002', '2026-05-29', 20, 21, 19, 20.5, 100, 2050,
                NULL, 'tdx_standard_bj', 'none', 'BSE'
            );
            """
        )

    monkeypatch.setattr(repair, "DB_PATH", db_path)
    with sqlite3.connect(db_path) as conn:
        assert repair.abnormal_symbols(conn, "2026-05-29") == {"600519"}


def test_gap_scan_excludes_future_list_date_and_accepted_exception(tmp_path):
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
                ('000002', '未上市样本', '20260518'),
                ('000003', '无原始数据样本', '19910403');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-05-15', 10, 11, 9, 10.5, 100, 1000, 1.2);
            """
        )
    ensure_data_core_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO stock_daily_gap_exception (
                symbol, trade_date, market, reason, source, attempts, status
            ) VALUES ('000003', '2026-05-15', 'SZ', 'tdx_no_raw_data', 'tdx_mootdx_qfq', 1, 'accepted_no_raw_data')
            """
        )
        conn.commit()

    scan = gap_repair.scan_stock_daily_gaps(
        db_path,
        end_date="2026-05-15",
        window_trading_days=1,
        markets=("SZ",),
    )

    assert scan.raw_missing_count == 1
    assert scan.exception_count == 1
    assert scan.effective_missing_count == 0
    assert scan.candidate_symbols == ()


def test_gap_scan_marks_recoverable_pct_chg_null_as_abnormal(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_info (symbol TEXT PRIMARY KEY, name TEXT, list_date TEXT);
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL, turnover_rate REAL, pct_chg REAL
            );
            INSERT INTO stock_info VALUES ('000001', '平安银行', '19910403');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-07-10', 10, 10.5, 9.8, 10.2, 100, 1020, 1.2, 1.0),
                ('000001', '2026-07-13', 10.2, 10.8, 10.1, 10.6, 110, 1166, 1.3, NULL);
            """
        )
    ensure_data_core_schema(db_path)

    scan = gap_repair.scan_stock_daily_gaps(
        db_path,
        end_date="2026-07-13",
        window_trading_days=1,
        markets=("SZ",),
    )

    assert scan.abnormal_count == 1
    assert scan.candidate_symbols == ("000001",)


def test_gap_scan_day_lookup_uses_indexable_range_predicate(tmp_path):
    db_path = tmp_path / "local_quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily_prices (
                symbol TEXT,
                trade_date TEXT,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        conn.execute(
            "INSERT INTO stock_daily_prices VALUES ('000001', '2026-05-15 00:00:00')"
        )
        statements = []
        conn.set_trace_callback(statements.append)

        symbols = gap_repair.present_symbols(conn, "2026-05-15", "SZ")

    lookup_sql = next(
        statement
        for statement in statements
        if "SELECT DISTINCT symbol" in statement
        and "stock_daily_prices" in statement
    )
    assert symbols == {"000001"}
    assert "DATE(trade_date)" not in lookup_sql
    assert "trade_date >=" in lookup_sql
    assert "trade_date <" in lookup_sql


def test_gap_repair_collector_command_is_tdx_only_and_forces_window(tmp_path):
    args = gap_repair.build_parser().parse_args([
        "--end-date",
        "2026-05-15",
        "--workers",
        "2",
        "--pool-size",
        "2",
        "--submit-window",
        "16",
        "--max-bars",
        "120",
    ])
    command = gap_repair.build_collector_command(
        args,
        "gap_repair_test",
        tmp_path / "symbols.txt",
        tmp_path / "results.json",
    )

    assert "collect_stock_kline_mootdx_qfq.py" in command[1]
    assert "--force-window" in command
    assert "--max-fail-rate" in command
    assert command[command.index("--max-fail-rate") + 1] == "1.0"
    assert "akshare" not in " ".join(command).lower()
    assert "eastmoney" not in " ".join(command).lower()


def test_gap_exception_upsert_only_accepts_success_or_no_raw_data(tmp_path):
    db_path = tmp_path / "local_quant.db"
    ensure_data_core_schema(db_path)
    items = [
        gap_repair.GapItem("000002", "2026-05-15", "SZ", "missing_kline"),
        gap_repair.GapItem("000003", "2026-05-15", "SZ", "missing_kline"),
        gap_repair.GapItem("000004", "2026-05-15", "SZ", "missing_kline"),
    ]
    results = {
        "000002": {"symbol": "000002", "status": "success", "source": "tdx_mootdx_qfq", "reason": ""},
        "000003": {"symbol": "000003", "status": "fail", "source": "tdx_mootdx_qfq", "reason": "no raw data"},
        "000004": {"symbol": "000004", "status": "fail", "source": "tdx_mootdx_qfq", "reason": "adapter timeout"},
    }

    with sqlite3.connect(db_path) as conn:
        accepted = gap_repair.upsert_gap_exceptions(
            conn,
            items=items,
            collector_results=results,
            run_id="gap_repair_test",
            collector_returncode=1,
        )
        conn.commit()
        rows = conn.execute(
            """
            SELECT symbol, status
            FROM stock_daily_gap_exception
            ORDER BY symbol
            """
        ).fetchall()

    assert accepted == 2
    assert rows == [("000002", "accepted_no_raw_data"), ("000003", "accepted_no_raw_data")]
