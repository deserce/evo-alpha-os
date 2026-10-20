"""Versioned sector membership snapshots for the local sector composite index.

The existing ``stock_sector_map`` table is a current-state table.  This module
turns a read of that table into a deterministic, content-addressed snapshot so
that a composite series can always explain which constituents it used.  It is
deliberately SQLite-only and has no dependency on a remote data source.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

from data_core.schema import table_columns, table_exists
from data_core.symbols import normalize_symbol


@dataclass(frozen=True)
class MembershipSnapshot:
    """One immutable sector membership version."""

    snapshot_id: str
    sector_name: str
    sector_code: str
    effective_from: str
    membership_hash: str
    member_count: int
    membership_status: str
    source: str


def _iso_date(value: object) -> str:
    """Normalize a date-like value to ``YYYY-MM-DD``."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        raise ValueError("date value is empty")
    return text[:10]


def _now_text(now: object | None = None) -> str:
    if now is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=" ")
    if isinstance(now, datetime):
        return now.replace(microsecond=0).isoformat(sep=" ")
    return str(now)


def _membership_hash(symbols: Iterable[str]) -> str:
    payload = "\n".join(sorted(set(symbols))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _snapshot_id(sector_name: str, membership_hash: str, status: str) -> str:
    payload = f"{sector_name}\0{membership_hash}\0{status}".encode("utf-8")
    return f"smv_{hashlib.sha256(payload).hexdigest()[:24]}"


def ensure_membership_schema(conn: sqlite3.Connection) -> None:
    """Create membership tables and indexes idempotently."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sector_membership_version (
            snapshot_id TEXT PRIMARY KEY,
            sector_name TEXT NOT NULL,
            sector_code TEXT,
            effective_from TEXT NOT NULL,
            membership_hash TEXT NOT NULL,
            member_count INTEGER NOT NULL DEFAULT 0,
            membership_status TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'stock_sector_map',
            captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (sector_name, membership_hash, membership_status)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sector_membership_item (
            snapshot_id TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, symbol)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sector_membership_version_lookup "
        "ON sector_membership_version (sector_name, effective_from, membership_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sector_membership_item_sector "
        "ON sector_membership_item (sector_name, symbol)"
    )


def _sector_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Read the current map without assuming optional columns exist."""
    if not table_exists(conn, "stock_sector_map"):
        return []
    columns = table_columns(conn, "stock_sector_map")
    if "symbol" not in columns or "sector_name" not in columns:
        return []

    sector_meta: dict[str, str] = {}
    if table_exists(conn, "sector_list"):
        sector_columns = table_columns(conn, "sector_list")
        if "sector_name" in sector_columns and "sector_code" in sector_columns:
            for name, code in conn.execute(
                "SELECT sector_name, sector_code FROM sector_list"
            ).fetchall():
                if name is not None and code is not None and str(code).strip():
                    sector_meta[str(name).strip()] = str(code).strip()

    code_expr = "sector_code" if "sector_code" in columns else "NULL"
    type_expr = "sector_type" if "sector_type" in columns else "'Industry'"
    rows = conn.execute(
        f"SELECT symbol, sector_name, {code_expr}, {type_expr} FROM stock_sector_map "
        "WHERE symbol IS NOT NULL AND sector_name IS NOT NULL"
    ).fetchall()
    normalised: list[tuple[str, str, str]] = []
    for symbol, sector_name, sector_code, sector_type in rows:
        if str(sector_type or "Industry").strip().lower() not in {"industry", "行业"}:
            continue
        name = str(sector_name).strip()
        if not name:
            continue
        try:
            symbol_text = normalize_symbol(symbol)
        except (TypeError, ValueError):
            continue
        code = str(sector_code).strip() if sector_code is not None else ""
        if not code:
            code = sector_meta.get(name, name)
        normalised.append((symbol_text, name, code))
    return normalised


def capture_membership_snapshots(
    conn: sqlite3.Connection,
    *,
    effective_date: str | date,
    membership_status: str = "daily_snapshot",
    source: str = "stock_sector_map",
    now: object | None = None,
) -> dict[str, MembershipSnapshot]:
    """Capture all current map memberships and return snapshots by sector.

    Snapshots are content-addressed.  Re-reading the same map is idempotent,
    while a changed member set receives a new snapshot ID.  ``current_snapshot
    ``current_snapshot_backfill`` is intentionally a first-class status so historical backfills
    cannot be mistaken for point-in-time membership history.
    """
    ensure_membership_schema(conn)
    effective = _iso_date(effective_date)
    captured_at = _now_text(now)
    grouped: dict[str, tuple[str, list[str]]] = {}
    for symbol, sector_name, sector_code in _sector_rows(conn):
        if sector_name not in grouped:
            grouped[sector_name] = (sector_code, [])
        code, members = grouped[sector_name]
        if not code and sector_code:
            code = sector_code
        if symbol not in members:
            members.append(symbol)
        grouped[sector_name] = (code, members)

    result: dict[str, MembershipSnapshot] = {}
    for sector_name in sorted(grouped):
        sector_code, members = grouped[sector_name]
        members = sorted(set(members))
        membership_hash = _membership_hash(members)
        snapshot_id = _snapshot_id(sector_name, membership_hash, membership_status)
        existing = conn.execute(
            "SELECT effective_from, sector_code, source, captured_at "
            "FROM sector_membership_version WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO sector_membership_version (
                    snapshot_id, sector_name, sector_code, effective_from,
                    membership_hash, member_count, membership_status, source,
                    captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    sector_name,
                    sector_code,
                    effective,
                    membership_hash,
                    len(members),
                    membership_status,
                    source,
                    captured_at,
                ),
            )
            conn.executemany(
                "INSERT INTO sector_membership_item (snapshot_id, sector_name, symbol) "
                "VALUES (?, ?, ?)",
                [(snapshot_id, sector_name, symbol) for symbol in members],
            )
        elif effective < str(existing[0])[:10]:
            conn.execute(
                "UPDATE sector_membership_version SET effective_from=? WHERE snapshot_id=?",
                (effective, snapshot_id),
            )

        result[sector_name] = MembershipSnapshot(
            snapshot_id=snapshot_id,
            sector_name=sector_name,
            sector_code=sector_code or str(existing[1] if existing else sector_name),
            effective_from=str(
                effective if existing is None else min(effective, str(existing[0])[:10])
            ),
            membership_hash=membership_hash,
            member_count=len(members),
            membership_status=membership_status,
            source=source if existing is None else str(existing[2]),
        )
    return result


def load_snapshot_members(conn: sqlite3.Connection, snapshot_id: str) -> list[str]:
    """Return normalized members for a known snapshot."""
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT symbol FROM sector_membership_item WHERE snapshot_id=? ORDER BY symbol",
            (snapshot_id,),
        ).fetchall()
    ]


__all__ = [
    "MembershipSnapshot",
    "capture_membership_snapshots",
    "ensure_membership_schema",
    "load_snapshot_members",
]
