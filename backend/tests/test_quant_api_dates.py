import sqlite3

import pytest
from fastapi import HTTPException

from app.api.v1.quant import _resolve_trade_date


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE stock_lifecycle_state (trade_date TEXT)")
    conn.executemany(
        "INSERT INTO stock_lifecycle_state VALUES (?)",
        [("2026-04-24",), ("2026-04-27",)],
    )
    conn.commit()
    return conn


def test_resolve_trade_date_uses_requested_date_when_available():
    conn = make_conn()
    try:
        assert _resolve_trade_date(conn, "2026-04-24") == "2026-04-24"
    finally:
        conn.close()


def test_resolve_trade_date_rolls_back_to_latest_available_date():
    conn = make_conn()
    try:
        assert _resolve_trade_date(conn, "2026-04-29") == "2026-04-27"
    finally:
        conn.close()


def test_resolve_trade_date_rejects_invalid_date():
    conn = make_conn()
    try:
        with pytest.raises(HTTPException):
            _resolve_trade_date(conn, "not-a-date")
    finally:
        conn.close()
