"""Shared market-strength row selection helpers."""

from __future__ import annotations

from typing import Any


MARKET_INDEX_PRIORITY = ("399006", "000300", "000852", "000001")


def market_priority(index_code: Any) -> int:
    """Return the canonical display priority for market_strength_daily rows."""
    code = str(index_code or "")
    try:
        return MARKET_INDEX_PRIORITY.index(code)
    except ValueError:
        return len(MARKET_INDEX_PRIORITY) + 1


def market_order_case(column: str = "index_code") -> str:
    """Build a SQLite CASE expression matching the canonical market row order."""
    clauses = [
        f"WHEN '{code}' THEN {index}"
        for index, code in enumerate(MARKET_INDEX_PRIORITY)
    ]
    return f"CASE {column} {' '.join(clauses)} ELSE {len(MARKET_INDEX_PRIORITY) + 1} END"


def pick_market_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick one canonical market row from same-date market_strength_daily rows."""
    if not rows:
        return None
    return sorted(rows, key=lambda row: market_priority(row.get("index_code")))[0]
