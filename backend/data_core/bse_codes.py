"""BSE code helpers for standard TDX market requests."""

from __future__ import annotations

from data_core.symbols import normalize_symbol


TDX_STANDARD_BJ_SOURCE_NAME = "tdx_standard_bj"
TDX_BSE_MARKET = 2
BSE_OLD_PREFIXES = ("43", "83", "87")


def to_bse_tdx_symbol(value: object) -> str:
    """Return the BSE code understood by standard TDX market=2."""
    symbol = normalize_symbol(value)
    if symbol.startswith("92"):
        return symbol
    if symbol.startswith(BSE_OLD_PREFIXES):
        return f"920{symbol[-3:]}"
    return symbol
