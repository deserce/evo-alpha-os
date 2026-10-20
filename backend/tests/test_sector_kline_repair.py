import sqlite3
from dataclasses import dataclass

from scripts.repair_sector_kline_source_mismatch import (
    FetchResult,
    Sector,
    fetch_sector,
    normalize_legacy_volume_units,
    quality_report,
)


def test_normalize_legacy_volume_units_only_rescales_confirmed_lots():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE stock_sector_map (symbol TEXT, sector_name TEXT);
        CREATE TABLE stock_daily_prices (symbol TEXT, trade_date TEXT, volume REAL);
        INSERT INTO stock_sector_map VALUES ('000001', '行业A');
        INSERT INTO stock_sector_map VALUES ('000002', '行业A');
        INSERT INTO stock_daily_prices VALUES ('000001', '2026-08-25', 60000);
        INSERT INTO stock_daily_prices VALUES ('000002', '2026-08-25', 40000);
        """
    )
    rows = [
        {
            "sector_name": "行业A",
            "trade_date": "2026-08-25",
            "volume": 1000,
            "quality_flag": "fallback_no_anchor",
        },
        {
            "sector_name": "行业A",
            "trade_date": "2026-08-24",
            "volume": 100000,
            "quality_flag": "synthetic",
        },
    ]

    changed = normalize_legacy_volume_units(conn, rows)

    assert changed == 1
    assert rows[0]["volume"] == 100000
    assert rows[0]["quality_flag"].endswith("|volume_shares")
    assert rows[1]["volume"] == 100000


@dataclass
class EmptyGate:
    failures: int = 0

    def get(self, *args, **kwargs):
        return {"rc": 0, "data": {"klines": []}}

    def record_failure(self):
        self.failures += 1


def test_fetch_sector_empty_response_is_failed_and_never_fallback():
    gate = EmptyGate()

    result = fetch_sector(
        Sector("行业A", "BK0001", "eastmoney_direct"),
        "20260825",
        "20260825",
        retries=4,
        timeout=3,
        request_delay=1.5,
        gate=gate,
    )

    assert result.ok is False
    assert result.rows == []
    assert "empty kline" in result.error
    assert gate.failures == 1


def test_quality_report_marks_non_vendor_rows_as_hard_source_contamination():
    result = FetchResult(
        "行业A",
        "BK0001",
        True,
        [
            {
                "sector_name": "行业A",
                "sector_code": "BK0001",
                "trade_date": "2026-08-25",
                "open": 100.0,
                "close": 101.0,
                "high": 102.0,
                "low": 99.0,
                "volume": 100.0,
                "amount": 1000.0,
                "pct_chg": 1.0,
                "source": "synthetic_stock_map",
                "quality_flag": "synthetic",
            }
        ],
    )

    report = quality_report([result])

    assert report["non_vendor_rows"] == 1
    assert report["synthetic_source_rows"] == 1
