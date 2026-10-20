import sqlite3

import pytest

from data_core.sector_composite import build_sector_composites
from scripts import rebuild_sector_composite


def _prepared_db(tmp_path):
    db_path = tmp_path / "quant.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_sector_map (
                symbol TEXT, sector_name TEXT, sector_code TEXT
            );
            CREATE TABLE sector_list (
                sector_name TEXT PRIMARY KEY, sector_code TEXT,
                sector_type TEXT, source TEXT
            );
            CREATE TABLE stock_daily_prices (
                symbol TEXT, trade_date TEXT, open REAL, high REAL,
                low REAL, close REAL, pct_chg REAL, volume REAL, amount REAL
            );
            INSERT INTO sector_list VALUES ('运输设备', 'BK0001', 'industry', 'test');
            INSERT INTO stock_sector_map VALUES ('000001', '运输设备', 'BK0001');
            INSERT INTO stock_daily_prices VALUES
                ('000001', '2026-08-24', 10, 10, 10, 10, 0, 100, 1000),
                ('000001', '2026-08-25', 10, 11, 9, 10.5, 5, 120, 1260);
            """
        )
        build_sector_composites(
            conn,
            start_date="2026-08-24",
            end_date="2026-08-25",
            membership_status="daily_snapshot",
            min_breadth=1,
        )
    return db_path


def test_missing_only_rebuilds_when_net_value_exists_but_ohlc_is_missing(
    tmp_path,
    monkeypatch,
):
    db_path = _prepared_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM sector_composite_ohlc_daily WHERE trade_date='2026-08-25'"
        )
        conn.execute(
            "UPDATE sector_composite_daily SET net_value=1077.0 "
            "WHERE trade_date='2026-08-25'"
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM sector_composite_daily WHERE trade_date='2026-08-25'"
        ).fetchone()[0] == 1

    monkeypatch.setattr(
        "sys.argv",
        [
            "rebuild_sector_composite.py",
            "--db",
            str(db_path),
            "--target-date",
            "2026-08-25",
            "--missing-only",
            "--membership-status",
            "daily_snapshot",
            "--min-breadth",
            "1",
        ],
    )

    assert rebuild_sector_composite.main() == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT net_value FROM sector_composite_daily "
            "WHERE trade_date='2026-08-25'"
        ).fetchone()[0] == pytest.approx(1077.0)
        assert conn.execute(
            "SELECT close FROM sector_composite_ohlc_daily "
            "WHERE trade_date='2026-08-25'"
        ).fetchone()[0] == pytest.approx(1077.0)


def test_missing_only_skips_only_when_net_value_and_ohlc_are_both_complete(
    tmp_path,
    monkeypatch,
    capsys,
):
    db_path = _prepared_db(tmp_path)
    monkeypatch.setattr(
        rebuild_sector_composite,
        "build_sector_composites",
        lambda *args, **kwargs: pytest.fail("complete target must be a no-op"),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "rebuild_sector_composite.py",
            "--db",
            str(db_path),
            "--target-date",
            "2026-08-25",
            "--missing-only",
            "--membership-status",
            "daily_snapshot",
        ],
    )

    assert rebuild_sector_composite.main() == 0
    assert '"reason": "target_date_complete"' in capsys.readouterr().out


def test_missing_only_rebuilds_wrong_ohlc_method_version(tmp_path, monkeypatch):
    db_path = _prepared_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE sector_composite_ohlc_daily SET method_version='stale_v0' "
            "WHERE trade_date='2026-08-25'"
        )

    monkeypatch.setattr(
        "sys.argv",
        [
            "rebuild_sector_composite.py",
            "--db",
            str(db_path),
            "--target-date",
            "2026-08-25",
            "--missing-only",
            "--membership-status",
            "daily_snapshot",
            "--min-breadth",
            "1",
        ],
    )

    assert rebuild_sector_composite.main() == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT DISTINCT method_version FROM sector_composite_ohlc_daily "
            "WHERE trade_date='2026-08-25'"
        ).fetchall() == [("ew_qfq_ohlc_v1",)]
