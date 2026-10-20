import asyncio
import sqlite3

from app.api.v1 import quant as quant_api


def _row(trade_date: str, high: float, low: float, close: float) -> dict:
    return {
        "trade_date": trade_date,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
        "amount": 1000,
    }


def test_sector_chart_series_returns_aligned_kdj_values():
    rows = [
        _row("2026-05-25", 11, 9, 10),
        _row("2026-05-26", 12, 9.5, 11.5),
        _row("2026-05-27", 13, 10, 12.5),
    ]

    data = quant_api._sector_chart_series(rows)

    assert len(data["kdj"]["k"]) == len(rows)
    assert len(data["kdj"]["d"]) == len(rows)
    assert len(data["kdj"]["j"]) == len(rows)
    assert all(value is not None for value in data["kdj"]["j"])


def test_sector_chart_kdj_preserves_values_outside_zero_to_one_hundred():
    rows = [
        _row(f"2026-05-{day:02d}", 10, 0, 10)
        for day in range(1, 12)
    ]

    data = quant_api._sector_chart_series(rows)

    assert max(data["kdj"]["j"]) > 100


def test_sector_chart_series_returns_empty_kdj_for_empty_rows():
    data = quant_api._sector_chart_series([])

    assert data["kdj"] == {"k": [], "d": [], "j": []}


def test_sector_chart_uses_exact_requested_date_and_reports_source_quality(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE sector_vendor_daily (
            sector_name TEXT,
            trade_date TEXT,
            open REAL,
            close REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL,
            pct_chg REAL,
            quality_flag TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO sector_vendor_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("行业A", "2026-05-25", 10, 10.5, 11, 9.5, 100, 1000, 5, "ok"),
            ("行业A", "2026-05-26", 10.5, 10.2, 10.7, 10, 200, 2000, -3, "ok"),
            ("行业A", "2026-05-27", 10.2, 10.8, 11, 10.1, 300, 3000, 6, "ok"),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_sector_chart(
            sector_name="行业A",
            trade_date="2026-05-26",
            limit=30,
            mode="vendor",
        )
    ).data

    assert data["trade_date"] == "2026-05-26"
    assert data["dates"][-1] == "2026-05-26"
    assert data["mode"] == "vendor"
    assert data["price_kind"] == "ohlc"
    assert data["quality_summary"]["synthetic_rows"] == 0
    assert data["quality_summary"]["sources"] == {"eastmoney_bk": 2}


def test_sector_chart_defaults_to_local_ohlc_and_returns_real_candles(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE sector_composite_ohlc_daily (
            sector_name TEXT,
            trade_date TEXT,
            open REAL,
            close REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL,
            pct_chg REAL,
            coverage_ratio REAL,
            method_version TEXT,
            quality_flag TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO sector_composite_ohlc_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("行业A", "2026-05-25", 998, 1000, 1006, 994, 100, 1000, 0, 1, "ew_qfq_ohlc_v1", "PASS"),
            ("行业A", "2026-05-26", 1002, 1010, 1015, 999, 200, 2000, 1, 1, "ew_qfq_ohlc_v1", "PASS"),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_sector_chart(sector_name="行业A", trade_date="2026-05-26", limit=30)
    ).data

    assert data["mode"] == "local"
    assert data["price_kind"] == "ohlc"
    assert data["source"] == "local_equal_weight_ohlc"
    assert data["method_version"] == "ew_qfq_ohlc_v1"
    assert data["kline"] == [[998, 1000, 994, 1006], [1002, 1010, 999, 1015]]
    assert len(data["kdj"]["k"]) == 2


def test_sector_chart_local_ohlc_joins_only_canonical_sector_rps_v2(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "quant.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sector_composite_ohlc_daily (
            sector_name TEXT, trade_date TEXT, open REAL, close REAL,
            high REAL, low REAL, volume REAL, amount REAL, pct_chg REAL,
            coverage_ratio REAL, method_version TEXT, quality_flag TEXT
        );
        CREATE TABLE quant_feature_sector_rps_v2 (
            sector_name TEXT, trade_date TEXT,
            rps_5 REAL, rps_10 REAL, rps_20 REAL,
            rps_50 REAL, rps_120 REAL, rps_250 REAL
        );
        CREATE TABLE quant_feature_sector_rps (
            sector_name TEXT, trade_date TEXT, rps_20 REAL
        );
        INSERT INTO sector_composite_ohlc_daily VALUES
            ('行业A', '2026-05-26', 1002, 1010, 1015, 999,
             200, 2000, 1, 1, 'ew_qfq_ohlc_v1', 'PASS');
        INSERT INTO quant_feature_sector_rps_v2 VALUES
            ('行业A', '2026-05-26', 91, 92, 93, 94, 95, 96);
        INSERT INTO quant_feature_sector_rps VALUES
            ('行业A', '2026-05-26', 1);
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_sector_chart(
            sector_name="行业A",
            trade_date="2026-05-26",
            limit=30,
        )
    ).data

    assert data["rps_short"] == {"r5": [91], "r10": [92], "r20": [93]}
    assert data["rps_long"] == {"r50": [94], "r120": [95], "r250": [96]}


def test_sector_chart_local_mode_does_not_fall_back_to_legacy_or_net_value(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "quant.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE sector_daily_prices (sector_name TEXT, trade_date TEXT, close REAL)"
    )
    conn.execute("INSERT INTO sector_daily_prices VALUES ('行业A', '2026-05-26', 9999)")
    conn.execute(
        "CREATE TABLE sector_composite_daily (sector_name TEXT, trade_date TEXT, net_value REAL)"
    )
    conn.execute("INSERT INTO sector_composite_daily VALUES ('行业A', '2026-05-26', 1010)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_sector_chart(sector_name="行业A", trade_date="2026-05-26", limit=30)
    ).data

    assert data["mode"] == "local"
    assert data["ready"] is False
    assert data["price_kind"] == "ohlc"
    assert data["kline"] == []
    assert "等权合成K线" in data["message"]


def test_sector_chart_explicit_composite_mode_preserves_net_value_compatibility(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "quant.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE sector_composite_daily (
            sector_name TEXT,
            trade_date TEXT,
            net_value REAL,
            pct_chg REAL,
            volume REAL,
            amount REAL,
            coverage_ratio REAL,
            method_version TEXT,
            quality_flag TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO sector_composite_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("行业A", "2026-05-25", 1000, 0, 100, 1000, 1, "ew_qfq_v1", "PASS"),
            ("行业A", "2026-05-26", 1010, 1, 200, 2000, 1, "ew_qfq_v1", "PASS"),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    data = asyncio.run(
        quant_api.get_sector_chart(
            sector_name="行业A",
            trade_date="2026-05-26",
            limit=30,
            mode="composite",
        )
    ).data

    assert data["mode"] == "composite"
    assert data["price_kind"] == "net_value"
    assert data["net_value"] == [1000, 1010]
    assert data["kline"] == []
    assert data["kdj"] == {"k": [], "d": [], "j": []}
