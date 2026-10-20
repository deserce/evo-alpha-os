import sqlite3
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from scripts.calculate_strength_features import calculate_limit_stats, calculate_rps90_counts


def test_calculate_rps90_counts_includes_rps20():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE quant_feature_rps (
            symbol TEXT,
            trade_date TEXT,
            rps_20 REAL,
            rps_50 REAL,
            rps_120 REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO quant_feature_rps VALUES (?, ?, ?, ?, ?)",
        [
            ("000001", "2026-05-07", 91, 10, 10),
            ("000002", "2026-05-07", 10, 92, 10),
            ("000003", "2026-05-07", 10, 10, 93),
            ("000004", "2026-05-07", 89, 89, 89),
        ],
    )

    df = calculate_rps90_counts(conn)

    assert df.to_dict("records") == [{"trade_date": "2026-05-07", "rps90_count": 3}]


def test_calculate_limit_stats_includes_down_table():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE limit_board_trading (
            trade_date TEXT,
            symbol TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE limit_down_trading (
            trade_date TEXT,
            symbol TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO limit_board_trading VALUES (?, ?)",
        [
            ("2026-05-08", "000001"),
            ("2026-05-08", "000002"),
            ("2026-05-08", "000002"),
        ],
    )
    conn.executemany(
        "INSERT INTO limit_down_trading VALUES (?, ?)",
        [
            ("2026-05-08", "000003"),
            ("2026-05-09", "000004"),
        ],
    )

    df = calculate_limit_stats(conn).sort_values("trade_date").reset_index(drop=True)

    assert df.to_dict("records") == [
        {"trade_date": "2026-05-08", "limit_up_count": 2, "limit_down_count": 1},
        {"trade_date": "2026-05-09", "limit_up_count": 0, "limit_down_count": 1},
    ]
