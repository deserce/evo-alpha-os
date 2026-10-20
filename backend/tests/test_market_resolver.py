import asyncio
import sqlite3

from app.api.v1 import market as market_api
from app.api.v1 import quant as quant_api
from app.api.v1.market_resolver import market_order_case, market_priority, pick_market_row


def test_market_priority_prefers_quant_display_index():
    rows = [
        {"index_code": "000001", "market_state": "M2"},
        {"index_code": "399006", "market_state": "M3"},
        {"index_code": "000300", "market_state": "M3"},
    ]

    selected = pick_market_row(rows)

    assert selected is not None
    assert selected["index_code"] == "399006"
    assert market_priority("399006") < market_priority("000300")


def test_market_order_case_matches_sqlite_ordering():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE market_strength_daily (
            trade_date TEXT,
            index_code TEXT,
            market_strength_score REAL,
            market_state TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO market_strength_daily VALUES (?, ?, ?, ?)",
        [
            ("2026-05-29", "000001", 50.48, "M2"),
            ("2026-05-29", "000300", 63.48, "M3"),
            ("2026-05-29", "399006", 63.48, "M3"),
            ("2026-05-29", "000852", 55.48, "M2"),
        ],
    )
    row = conn.execute(
        f"""
        SELECT index_code, market_state, market_strength_score
        FROM market_strength_daily
        ORDER BY trade_date DESC, {market_order_case("index_code")}
        LIMIT 1
        """
    ).fetchone()

    assert dict(row) == {
        "index_code": "399006",
        "market_state": "M3",
        "market_strength_score": 63.48,
    }


def test_quant_dashboard_and_brief_expose_same_market_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "quant.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE market_strength_daily (
                trade_date TEXT,
                index_code TEXT,
                index_name TEXT,
                close REAL,
                above_ma20 INTEGER,
                above_ma50 INTEGER,
                up_count INTEGER,
                down_count INTEGER,
                new_high_20_count INTEGER,
                new_high_60_count INTEGER,
                limit_up_count INTEGER,
                limit_down_count INTEGER,
                total_amount REAL,
                amount_ratio_20 REAL,
                rps90_count INTEGER,
                market_strength_score REAL,
                market_state TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO market_strength_daily VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                ("2026-05-29", "000001", "上证指数", 4068.57, 0, 1, 1543, 3882, 28, 21, 10, 0, 1.0, 1.0, 870, 50.48, "M2"),
                ("2026-05-29", "399006", "创业板指", 4037.95, 1, 1, 1543, 3882, 28, 21, 10, 0, 1.0, 1.0, 870, 63.48, "M3"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(quant_api, "DB_PATH", db_path)

    dashboard = asyncio.run(quant_api.get_quant_dashboard("2026-05-29", 20)).data
    brief = asyncio.run(quant_api.get_ai_brief("2026-05-29")).data

    assert dashboard["market_index_code"] == "399006"
    assert dashboard["market_state"] == "M3"
    assert dashboard["market_strength_score"] == 63.48
    assert brief["market_index_code"] == dashboard["market_index_code"]
    assert brief["market_state"] == dashboard["market_state"]
    assert brief["market_strength_score"] == dashboard["market_strength_score"]


def test_market_dashboard_exposes_resolved_market_fields(monkeypatch):
    row = {
        "trade_date": "2026-05-29",
        "index_code": "399006",
        "index_name": "创业板指",
        "up_count": 1543,
        "down_count": 3882,
        "new_high_20_count": 28,
        "new_high_60_count": 21,
        "limit_up_count": 10,
        "limit_down_count": 0,
        "total_amount": 3326212262671.25,
        "amount_ratio_20": 1.06,
        "rps90_count": 870,
        "market_strength_score": 63.48,
        "market_state": "M3",
        "created_at": "2026-05-31 15:46:39",
    }
    monkeypatch.setattr(market_api, "_resolve_market_date", lambda date: "2026-05-29")
    monkeypatch.setattr(market_api, "_latest_market_row", lambda trade_date: row)
    monkeypatch.setattr(market_api, "_previous_market_row", lambda trade_date: {"market_state": "M3"})
    monkeypatch.setattr(market_api, "_market_history", lambda trade_date, days: [])
    monkeypatch.setattr(
        market_api,
        "_build_histories",
        lambda rows: {
            "breadthHistory": [],
            "limitStatsHistory": [],
            "strengthDiffusionHistory": [],
            "amountHistory": [],
        },
    )
    monkeypatch.setattr(market_api, "_market_indices", lambda trade_date: [{"indexCode": "000001"}])

    data = asyncio.run(market_api.get_market_dashboard("2026-05-29")).data

    assert data["trade_date"] == "2026-05-29"
    assert data["market_index_code"] == "399006"
    assert data["market_state"] == "M3"
    assert data["market_strength_score"] == 63.48
    assert data["defaultIndexCode"] == "399006"
