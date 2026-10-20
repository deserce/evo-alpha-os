"""Symbol and market normalization helpers for A-share data."""

from __future__ import annotations

import re


MARKET_SH = "SH"
MARKET_SZ = "SZ"
MARKET_BSE = "BSE"
MARKET_UNKNOWN = "UNKNOWN"
P0_MARKETS = (MARKET_SH, MARKET_SZ, MARKET_BSE)

_SYMBOL_PATTERN = re.compile(r"(\d{6})")


def normalize_symbol(value: object) -> str:
    """Return the internal 6-digit symbol, or raise ValueError."""
    if value is None:
        raise ValueError("symbol is required")

    text = str(value).strip().upper()
    match = _SYMBOL_PATTERN.search(text)
    if not match:
        raise ValueError(f"invalid symbol: {value!r}")
    return match.group(1)


def infer_market(value: object) -> str:
    """Infer SH/SZ/BSE from an A-share symbol prefix."""
    try:
        symbol = normalize_symbol(value)
    except ValueError:
        return MARKET_UNKNOWN

    if symbol.startswith(("60", "68")):
        return MARKET_SH
    if symbol.startswith(("00", "30")):
        return MARKET_SZ
    if symbol.startswith(("92", "83", "87", "43")):
        return MARKET_BSE
    return MARKET_UNKNOWN


def is_p0_symbol(value: object, include_bse: bool = True) -> bool:
    """Return whether the symbol belongs to the P0 A-share universe."""
    market = infer_market(value)
    if include_bse:
        return market in P0_MARKETS
    return market in (MARKET_SH, MARKET_SZ)


def market_case_sql(column_name: str = "symbol") -> str:
    """SQLite CASE expression for market inference."""
    return (
        "CASE "
        f"WHEN {column_name} LIKE '60%' OR {column_name} LIKE '68%' THEN 'SH' "
        f"WHEN {column_name} LIKE '00%' OR {column_name} LIKE '30%' THEN 'SZ' "
        f"WHEN {column_name} LIKE '92%' OR {column_name} LIKE '83%' "
        f"OR {column_name} LIKE '87%' OR {column_name} LIKE '43%' THEN 'BSE' "
        "ELSE 'UNKNOWN' END"
    )


def market_where_sql(market: str, column_name: str = "symbol") -> str:
    """SQLite predicate matching one normalized market."""
    normalized = str(market).upper()
    if normalized == MARKET_SH:
        return f"({column_name} LIKE '60%' OR {column_name} LIKE '68%')"
    if normalized == MARKET_SZ:
        return f"({column_name} LIKE '00%' OR {column_name} LIKE '30%')"
    if normalized == MARKET_BSE:
        return (
            f"({column_name} LIKE '92%' OR {column_name} LIKE '83%' "
            f"OR {column_name} LIKE '87%' OR {column_name} LIKE '43%')"
        )
    return "1 = 0"

