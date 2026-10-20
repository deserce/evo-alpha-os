import sqlite3

import pytest

import data_core.sector_composite as sector_composite_module
from data_core.sector_composite import build_sector_composites, ensure_sector_composite_schema
from data_core.sector_membership import capture_membership_snapshots


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE stock_sector_map (
            symbol TEXT,
            sector_name TEXT,
            sector_code TEXT
        );
        CREATE TABLE sector_list (
            sector_name TEXT PRIMARY KEY,
            sector_code TEXT,
            sector_type TEXT,
            source TEXT
        );
        CREATE TABLE stock_daily_prices (
            symbol TEXT,
            trade_date TEXT,
            close REAL,
            pct_chg REAL,
            volume REAL,
            amount REAL
        );
        INSERT INTO sector_list VALUES
            ('运输设备', 'BK0001', 'industry', 'test');
        INSERT INTO stock_sector_map VALUES
            ('000001', '运输设备', 'BK0001'),
            ('000002', '运输设备', 'BK0001'),
            ('000003', '运输设备', 'BK0001');
        INSERT INTO stock_daily_prices VALUES
            ('000001', '2026-08-24', 10.0, 0.0, 100.0, 1000.0),
            ('000002', '2026-08-24', 20.0, 0.0, 200.0, 4000.0),
            ('000003', '2026-08-24', 30.0, 0.0, 300.0, 9000.0),
            ('000001', '2026-08-25', 11.0, 10.0, 110.0, 1100.0),
            ('000002', '2026-08-25', 19.0, -5.0, 210.0, 3990.0),
            ('000003', '2026-08-25', 30.0, 0.0, 0.0, 0.0),
            ('000001', '2026-08-26', 11.2, 1.8181818, 120.0, 1200.0),
            ('000002', '2026-08-26', 19.2, 1.0526316, 220.0, 4200.0);
        """
    )
    ensure_sector_composite_schema(conn)
    return conn


def test_schema_is_idempotent_and_composite_has_no_ohlc():
    conn = sqlite3.connect(":memory:")
    ensure_sector_composite_schema(conn)
    ensure_sector_composite_schema(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "sector_membership_version",
        "sector_membership_item",
        "sector_composite_daily",
        "sector_composite_ohlc_daily",
    } <= tables
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(sector_composite_daily)")
    }
    assert "net_value" in columns
    assert "coverage_ratio" in columns
    assert not {"open", "high", "low", "close"} & columns
    index_names = {
        row[1] for row in conn.execute("PRAGMA index_list(sector_composite_daily)")
    }
    assert "idx_sector_composite_method_date_sector" in index_names
    ohlc_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(sector_composite_ohlc_daily)")
    }
    assert "pct_chg" in ohlc_columns


def _ohlc_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE stock_sector_map (
            symbol TEXT,
            sector_name TEXT,
            sector_code TEXT
        );
        CREATE TABLE sector_list (
            sector_name TEXT PRIMARY KEY,
            sector_code TEXT,
            sector_type TEXT,
            source TEXT
        );
        CREATE TABLE stock_daily_prices (
            symbol TEXT,
            trade_date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pct_chg REAL,
            volume REAL,
            amount REAL
        );
        INSERT INTO sector_list VALUES ('运输设备', 'BK0001', 'industry', 'test');
        INSERT INTO stock_sector_map VALUES
            ('000001', '运输设备', 'BK0001'),
            ('000002', '运输设备', 'BK0001');
        INSERT INTO stock_daily_prices VALUES
            ('000001', '2026-08-24', 10.0, 10.0, 10.0, 10.0, 0.0, 100.0, 1000.0),
            ('000002', '2026-08-24', 20.0, 20.0, 20.0, 20.0, 0.0, 200.0, 4000.0),
            ('000001', '2026-08-25', 10.5, 11.0, 10.0, 11.0, 10.0, 110.0, 1100.0),
            ('000002', '2026-08-25', 20.0, 22.0, 19.0, 21.0, 5.0, 210.0, 4200.0);
        """
    )
    ensure_sector_composite_schema(conn)
    return conn


def test_equal_weight_ohlc_uses_component_returns_and_matches_net_value():
    conn = _ohlc_db()
    result = build_sector_composites(
        conn,
        start_date="2026-08-24",
        end_date="2026-08-25",
        membership_status="current_snapshot_backfill",
        min_breadth=1,
    )
    assert result["rows_written"] == 2
    net = conn.execute(
        "SELECT net_value FROM sector_composite_daily "
        "WHERE sector_name='运输设备' AND trade_date='2026-08-25'"
    ).fetchone()[0]
    row = conn.execute(
        "SELECT open, high, low, close, pct_chg, priced_count, coverage_ratio, "
        "method_version, quality_flag "
        "FROM sector_composite_ohlc_daily "
        "WHERE sector_name='运输设备' AND trade_date='2026-08-25'"
    ).fetchone()
    # Previous composite close is 1000. Component return averages are:
    # open=(5%+0%)/2, high=(10%+10%)/2, low=(0%-5%)/2,
    # close=(10%+5%)/2.
    assert row[0] == pytest.approx(1025.0)
    assert row[1] == pytest.approx(1100.0)
    assert row[2] == pytest.approx(975.0)
    assert row[3] == pytest.approx(net)
    assert row[3] == pytest.approx(1075.0)
    assert row[4] == pytest.approx(7.5)
    assert row[5:] == (2, pytest.approx(1.0), "ew_qfq_ohlc_v1", "OK")
    assert result["rows_written"] == 2

    # Upserting the same date range must not create duplicate OHLC rows.
    build_sector_composites(
        conn,
        start_date="2026-08-24",
        end_date="2026-08-25",
        membership_status="current_snapshot_backfill",
        min_breadth=1,
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM sector_composite_ohlc_daily"
    ).fetchone()[0] == 2


def test_ohlc_only_backfill_preserves_existing_canonical_net_values():
    conn = _ohlc_db()
    build_sector_composites(
        conn,
        start_date="2026-08-24",
        end_date="2026-08-25",
        membership_status="current_snapshot_backfill",
        min_breadth=1,
    )
    conn.execute(
        "UPDATE sector_composite_daily SET net_value=1077.0 "
        "WHERE sector_name='运输设备' AND trade_date='2026-08-25'"
    )
    conn.execute(
        "DELETE FROM sector_composite_ohlc_daily WHERE trade_date='2026-08-25'"
    )

    result = build_sector_composites(
        conn,
        dates=["2026-08-25"],
        membership_status="current_snapshot_backfill",
        min_breadth=1,
        preserve_existing_net_values=True,
    )

    assert result["rows_written"] == 0
    assert result["ohlc_rows_written"] == 1
    assert conn.execute(
        "SELECT net_value FROM sector_composite_daily "
        "WHERE sector_name='运输设备' AND trade_date='2026-08-25'"
    ).fetchone()[0] == pytest.approx(1077.0)
    ohlc = conn.execute(
        "SELECT close, pct_chg FROM sector_composite_ohlc_daily "
        "WHERE sector_name='运输设备' AND trade_date='2026-08-25'"
    ).fetchone()
    assert ohlc[0] == pytest.approx(1077.0)
    assert ohlc[1] == pytest.approx(7.7)


def test_ohlc_only_backfill_fails_closed_when_canonical_close_is_missing():
    conn = _ohlc_db()
    build_sector_composites(
        conn,
        start_date="2026-08-24",
        end_date="2026-08-25",
        membership_status="current_snapshot_backfill",
        min_breadth=1,
    )
    conn.execute("DELETE FROM sector_composite_daily WHERE trade_date='2026-08-25'")
    conn.execute("DELETE FROM sector_composite_ohlc_daily WHERE trade_date='2026-08-25'")

    with pytest.raises(ValueError, match="canonical net values missing"):
        build_sector_composites(
            conn,
            dates=["2026-08-25"],
            membership_status="current_snapshot_backfill",
            min_breadth=1,
            preserve_existing_net_values=True,
        )

    assert conn.execute(
        "SELECT COUNT(*) FROM sector_composite_ohlc_daily "
        "WHERE trade_date='2026-08-25'"
    ).fetchone()[0] == 0


def test_contiguous_build_reuses_previous_day_rows(monkeypatch):
    conn = _db()
    calls: list[str] = []
    original = sector_composite_module._fetch_day

    def tracked_fetch_day(connection, day):
        calls.append(day)
        return original(connection, day)

    monkeypatch.setattr(sector_composite_module, "_fetch_day", tracked_fetch_day)
    build_sector_composites(
        conn,
        start_date="2026-08-24",
        end_date="2026-08-26",
        membership_status="current_snapshot_backfill",
    )
    assert calls == ["2026-08-24", "2026-08-25", "2026-08-26"]


def test_membership_snapshot_is_versioned_and_deterministic():
    conn = _db()
    first = capture_membership_snapshots(
        conn,
        effective_date="2026-08-24",
        membership_status="current_snapshot_backfill",
    )
    second = capture_membership_snapshots(
        conn,
        effective_date="2026-08-24",
        membership_status="current_snapshot_backfill",
    )
    assert first["运输设备"].snapshot_id == second["运输设备"].snapshot_id
    row = conn.execute(
        "SELECT membership_status, member_count, membership_hash "
        "FROM sector_membership_version WHERE sector_name='运输设备'"
    ).fetchone()
    assert row[0] == "current_snapshot_backfill"
    assert row[1] == 3
    assert row[2]
    assert conn.execute(
        "SELECT COUNT(*) FROM sector_membership_item WHERE snapshot_id=?",
        (row and first["运输设备"].snapshot_id,),
    ).fetchone()[0] == 3

    conn.execute(
        "INSERT INTO stock_sector_map VALUES ('000004', '运输设备', 'BK0001')"
    )
    changed = capture_membership_snapshots(
        conn,
        effective_date="2026-08-25",
        membership_status="daily_snapshot",
    )
    assert changed["运输设备"].snapshot_id != first["运输设备"].snapshot_id
    assert changed["运输设备"].member_count == 4


def test_equal_weight_net_value_uses_close_returns_and_is_idempotent():
    conn = _db()
    result = build_sector_composites(
        conn,
        start_date="2026-08-24",
        end_date="2026-08-26",
        membership_status="current_snapshot_backfill",
    )
    assert result["rows_written"] == 3
    rows = conn.execute(
        "SELECT trade_date, net_value, pct_chg, priced_count, suspended_count, "
        "unresolved_count, coverage_ratio, quality_flag, membership_status "
        "FROM sector_composite_daily ORDER BY trade_date"
    ).fetchall()
    assert rows[0][0] == "2026-08-24"
    assert rows[0][1] == pytest.approx(1000.0)
    # 2026-08-25: (10% - 5% + 0%) / 3 = 1.6667%.
    assert rows[1][2] == pytest.approx(1.6666667, rel=1e-5)
    assert rows[1][1] == pytest.approx(1016.6666667, rel=1e-5)
    assert rows[1][3:7] == (3, 1, 0, pytest.approx(1.0))
    assert rows[1][7] == "OK"
    assert rows[1][8] == "current_snapshot_backfill"

    again = build_sector_composites(
        conn,
        start_date="2026-08-24",
        end_date="2026-08-26",
        membership_status="current_snapshot_backfill",
    )
    assert again["rows_written"] == 3
    assert conn.execute("SELECT COUNT(*) FROM sector_composite_daily").fetchone()[0] == 3


def test_missing_and_abnormal_returns_do_not_pollute_net_value():
    conn = _db()
    conn.execute(
        "INSERT INTO stock_daily_prices VALUES (?, ?, ?, ?, ?, ?)",
        ("000001", "2026-08-27", 20.0, 100.0, 100.0, 2000.0),
    )
    conn.execute(
        "INSERT INTO stock_daily_prices VALUES (?, ?, ?, ?, ?, ?)",
        ("000002", "2026-08-27", None, None, None, None),
    )
    result = build_sector_composites(
        conn,
        start_date="2026-08-27",
        end_date="2026-08-27",
        membership_status="current_snapshot_backfill",
    )
    row = conn.execute(
        "SELECT net_value, pct_chg, priced_count, unresolved_count, quality_flag "
        "FROM sector_composite_daily WHERE trade_date='2026-08-27'"
    ).fetchone()
    assert result["rows_written"] == 1
    assert row[0] == pytest.approx(1000.0)
    assert row[1] == pytest.approx(0.0)
    assert row[2] == 0
    assert row[3] == 3
    assert row[4] == "NO_DATA"
