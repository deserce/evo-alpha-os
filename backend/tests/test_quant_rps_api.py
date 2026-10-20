import asyncio
import sqlite3

from app.api.v1 import quant as quant_api


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE stock_daily_prices (
            symbol TEXT,
            trade_date TEXT
        );
        CREATE TABLE industry_strength_daily (
            trade_date TEXT,
            sector_code TEXT,
            sector_name TEXT,
            sector_type TEXT,
            return_5d REAL,
            return_20d REAL,
            return_50d REAL,
            return_120d REAL,
            sector_rps_20 REAL,
            sector_rps_50 REAL,
            sector_rps_120 REAL,
            amount REAL,
            amount_ratio_20 REAL,
            rps90_stock_count INTEGER,
            rps90_stock_ratio REAL,
            new_high_stock_count INTEGER,
            new_high_stock_ratio REAL,
            leader_symbol TEXT,
            leader_strength_score REAL,
            industry_strength_score REAL,
            industry_state TEXT
        );
        CREATE TABLE quant_feature_sector_rps (
            sector_name TEXT,
            trade_date TEXT,
            chg_5 REAL,
            chg_10 REAL,
            chg_20 REAL,
            chg_50 REAL,
            chg_120 REAL,
            chg_250 REAL,
            rps_5 REAL,
            rps_10 REAL,
            rps_20 REAL,
            rps_50 REAL,
            rps_120 REAL,
            rps_250 REAL
        );
        CREATE TABLE stock_sector_map (
            symbol TEXT,
            sector_name TEXT,
            sector_type TEXT
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
            rps_20 REAL,
            rps_50 REAL,
            rps_120 REAL,
            rps_250 REAL,
            structure_score REAL,
            structure_state TEXT,
            signal_type TEXT,
            entry_reason TEXT,
            exit_reason TEXT,
            is_active INTEGER
        );
        CREATE TABLE quant_feature_rps (
            symbol TEXT,
            trade_date TEXT,
            chg_5 REAL,
            chg_10 REAL,
            chg_20 REAL,
            chg_50 REAL,
            chg_120 REAL,
            chg_250 REAL,
            rps_5 REAL,
            rps_10 REAL,
            rps_20 REAL,
            rps_50 REAL,
            rps_120 REAL,
            rps_250 REAL
        );
        CREATE TABLE timing_signals_daily (
            signal_id TEXT,
            symbol TEXT,
            trade_date TEXT,
            signal_type TEXT,
            signal_score REAL,
            entry_price REAL,
            stop_loss_price REAL,
            is_invalidated INTEGER
        );
        CREATE TABLE trade_plans (
            plan_id TEXT,
            signal_id TEXT,
            trade_date TEXT,
            symbol TEXT,
            plan_status TEXT,
            recommended_action TEXT,
            risk_status TEXT,
            current_stop_price REAL,
            latest_close REAL,
            r_multiple REAL,
            reduce_pct REAL
        );
        """
    )
    conn.executemany(
        "INSERT INTO stock_daily_prices VALUES (?, ?)",
        [("000001", "2026-05-29"), ("000002", "2026-05-29")],
    )
    conn.executemany(
        """
        INSERT INTO industry_strength_daily VALUES (
            ?, ?, ?, 'Industry', 0.01, 0.02, 0.03, 0.04, ?, ?, ?,
            1000000, 1.2, 3, 0.2, 1, 0.1, '000001', 80, ?, 'I4'
        )
        """,
        [
            ("2026-05-29", "A", "低短RPS", 20, 30, 40, 99),
            ("2026-05-29", "B", "高短RPS", 50, 60, 70, 80),
        ],
    )
    conn.executemany(
        """
        INSERT INTO quant_feature_sector_rps VALUES (
            ?, '2026-05-29 00:00:00.000000',
            0, 0, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            ("低短RPS", 10, 11, 20, 30, 40, 90),
            ("高短RPS", 99, 98, 50, 60, 70, 20),
        ],
    )
    conn.executemany(
        """
        INSERT INTO quant_feature_sector_rps VALUES (
            ?, '2026-05-28 00:00:00.000000',
            0, 0, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            ("低短RPS", 9, 10, 19, 29, 39, 89),
            ("高短RPS", 98, 97, 49, 59, 69, 19),
        ],
    )
    conn.executemany(
        "INSERT INTO stock_sector_map VALUES (?, ?, 'Industry')",
        [("000001", "高短RPS"), ("000002", "低短RPS")],
    )
    conn.executemany(
        """
        INSERT INTO stock_lifecycle_state VALUES (
            '2026-05-29', ?, ?, ?, ?, ?, 'M3', 'I4',
            ?, ?, ?, ?, ?, 'healthy', 'BUY', 'mrgc', NULL, 1
        )
        """,
        [
            ("000001", "短强", "高短RPS", "S4", 4, 60, 70, 80, 90, 75),
            ("000002", "长强", "低短RPS", "S5", 5, 90, 80, 70, 60, 70),
        ],
    )
    conn.executemany(
        """
        INSERT INTO quant_feature_rps VALUES (
            ?, '2026-05-29 00:00:00.000000',
            0, 0, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            ("000001", 95, 96, 60, 70, 80, 20),
            ("000002", 30, 31, 90, 80, 70, 99),
        ],
    )
    conn.execute(
        """
        INSERT INTO timing_signals_daily VALUES (
            'sig1', '000001', '2026-05-29', 'breakout', 88, 10.5, 9.8, 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO trade_plans VALUES (
            'plan1', 'sig1', '2026-05-29', '000001',
            'active', 'hold', 'normal', 9.8, 11.2, 1.4, 0
        )
        """
    )
    conn.commit()
    conn.close()


def test_industry_strength_can_sort_by_short_rps(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_db(db_path)
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_industry_strength(
            "2026-05-29",
            sort_by="sector_rps_5",
            sort_dir="desc",
            limit=10,
        )
    ).data

    assert data["rows"][0]["sector_name"] == "高短RPS"
    assert data["rows"][0]["sector_rps_5"] == 99


def test_lifecycle_can_sort_by_rps_10(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_db(db_path)
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_lifecycle(
            "2026-05-29",
            sort_by="rps_10",
            sort_dir="desc",
            limit=10,
        )
    ).data

    assert data["rows"][0]["symbol"] == "000001"
    assert data["rows"][0]["rps_10"] == 96


def test_alpha_opportunities_sort_by_selected_rps_period(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_db(db_path)
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_alpha_opportunities("2026-05-29", rps_period=250, limit=10)
    ).data

    assert data["rps_period"] == "rps_250"
    assert data["rows"][0]["symbol"] == "000002"
    assert data["rows"][0]["rps_value"] == 99


def test_sector_rps_heatmap_includes_requested_timestamped_date(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_db(db_path)
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_sector_rps_heatmap(
            trade_date="2026-05-29",
            rps_period=20,
            days=20,
            sectors=100,
            universe="ranked",
        )
    ).data

    assert data["trade_date"] == "2026-05-29"
    assert data["dates"][0] == "2026-05-29"
    assert data["sectors"][0]["values"]["2026-05-29"] is not None


def test_data_management_lists_all_sqlite_tables_lightweight(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    make_db(db_path)
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)
    monkeypatch.setattr(quant_api, "_read_data_core_quality", lambda conn: {"reports": [], "source_failures": [], "status_counts": {}})
    monkeypatch.setattr(quant_api, "_workflow_plan", lambda: [])
    monkeypatch.setattr(quant_api, "_read_progress_files", lambda: [])
    monkeypatch.setattr(quant_api, "_recent_log_files", lambda: [])

    data = asyncio.run(quant_api.get_data_management(False, False)).data
    with sqlite3.connect(db_path) as conn:
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]

    assert len(data["tables"]) == table_count
    assert data["data_core_quality"]["reports"] == []
    assert all(row["lag_days"] is None or row["lag_days"] >= 0 for row in data["tables"])
