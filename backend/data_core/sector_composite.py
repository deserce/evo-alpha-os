"""Deterministic equal-weight sector net-value and OHLC calculation.

This is the stable, local computation track for sector trend and RPS inputs.
The canonical close remains the versioned net value used by RPS.  A separate
table derives open/high/low from the same constituents' prices relative to
their previous close, so the UI can render meaningful candle geometry without
misrepresenting the net-value series as OHLC.  The implementation uses
``sqlite3`` directly so it can be used by the no-LLM Data Core runner and by
small isolated tests.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Iterable

from data_core.schema import table_columns, table_exists
from data_core.symbols import normalize_symbol
from data_core.sector_membership import (
    capture_membership_snapshots,
    ensure_membership_schema,
    load_snapshot_members,
)


METHOD_VERSION = "ew_qfq_v1"
OHLC_METHOD_VERSION = "ew_qfq_ohlc_v1"
DEFAULT_MIN_COVERAGE = 0.8
DEFAULT_MIN_BREADTH = 3
DEFAULT_MAX_ABS_RETURN = 0.35


def _iso_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        raise ValueError("date value is empty")
    return text[:10]


def _next_date(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def ensure_sector_composite_schema(conn: sqlite3.Connection) -> None:
    """Create membership and composite tables idempotently."""
    ensure_membership_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sector_composite_daily (
            sector_name TEXT NOT NULL,
            sector_code TEXT,
            trade_date TEXT NOT NULL,
            net_value REAL NOT NULL,
            pct_chg REAL NOT NULL,
            volume REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            eligible_count INTEGER NOT NULL DEFAULT 0,
            priced_count INTEGER NOT NULL DEFAULT 0,
            suspended_count INTEGER NOT NULL DEFAULT 0,
            unresolved_count INTEGER NOT NULL DEFAULT 0,
            coverage_ratio REAL NOT NULL DEFAULT 0,
            membership_snapshot_id TEXT,
            membership_status TEXT NOT NULL,
            method_version TEXT NOT NULL,
            quality_flag TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (sector_name, trade_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sector_composite_date "
        "ON sector_composite_daily (trade_date, quality_flag)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sector_composite_method_date_sector "
        "ON sector_composite_daily (method_version, trade_date, sector_name)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sector_composite_ohlc_daily (
            sector_name TEXT NOT NULL,
            sector_code TEXT,
            trade_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            pct_chg REAL NOT NULL DEFAULT 0,
            volume REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            eligible_count INTEGER NOT NULL DEFAULT 0,
            priced_count INTEGER NOT NULL DEFAULT 0,
            suspended_count INTEGER NOT NULL DEFAULT 0,
            unresolved_count INTEGER NOT NULL DEFAULT 0,
            coverage_ratio REAL NOT NULL DEFAULT 0,
            membership_snapshot_id TEXT,
            membership_status TEXT NOT NULL,
            method_version TEXT NOT NULL,
            quality_flag TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (sector_name, trade_date)
        )
        """
    )
    if "pct_chg" not in table_columns(conn, "sector_composite_ohlc_daily"):
        conn.execute(
            "ALTER TABLE sector_composite_ohlc_daily "
            "ADD COLUMN pct_chg REAL NOT NULL DEFAULT 0"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sector_composite_ohlc_date "
        "ON sector_composite_ohlc_daily (trade_date, quality_flag)"
    )


def _available_dates(conn: sqlite3.Connection) -> list[str]:
    if not table_exists(conn, "stock_daily_prices"):
        return []
    if "trade_date" not in table_columns(conn, "stock_daily_prices"):
        return []
    rows = conn.execute(
        "SELECT DISTINCT substr(trade_date, 1, 10) FROM stock_daily_prices "
        "WHERE trade_date IS NOT NULL ORDER BY 1"
    ).fetchall()
    return [str(row[0]) for row in rows if row[0]]


def _fetch_day(conn: sqlite3.Connection, day: str) -> dict[str, dict[str, float | None]]:
    """Fetch one normalized trading day using an index-friendly half-open range."""
    if not table_exists(conn, "stock_daily_prices"):
        return {}
    columns = table_columns(conn, "stock_daily_prices")
    required = {"symbol", "trade_date", "close"}
    if not required <= columns:
        return {}
    selected = ["symbol", "trade_date"]
    for column in ("open", "high", "low", "close", "pct_chg", "volume", "amount"):
        if column in columns:
            selected.append(column)
        else:
            selected.append(f"NULL AS {column}")
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM stock_daily_prices "
        "WHERE trade_date >= ? AND trade_date < ? ORDER BY trade_date",
        (day, _next_date(day)),
    ).fetchall()
    result: dict[str, dict[str, float | None]] = {}
    for symbol, raw_date, open_price, high_price, low_price, close, pct_chg, volume, amount in rows:
        if symbol is None:
            continue
        try:
            symbol_text = normalize_symbol(symbol)
        except (TypeError, ValueError):
            continue
        # A legacy table can contain a timestamp and a plain date for the same
        # symbol.  The latest textual timestamp is the deterministic winner.
        key = symbol_text
        candidate = {
            "open": _as_float(open_price),
            "high": _as_float(high_price),
            "low": _as_float(low_price),
            "close": _as_float(close),
            "pct_chg": _as_float(pct_chg),
            "volume": _as_float(volume),
            "amount": _as_float(amount),
            "trade_date": str(raw_date or ""),
        }
        previous = result.get(key)
        if previous is None or candidate["trade_date"] >= str(previous["trade_date"]):
            result[key] = candidate
    return result


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _pct_return(
    row: dict[str, float | None],
    previous: dict[str, float | None] | None,
    *,
    pct_chg_is_percent: bool,
) -> float | None:
    close = row.get("close")
    previous_close = previous.get("close") if previous else None
    if close is not None and close > 0 and previous_close is not None and previous_close > 0:
        return close / previous_close - 1.0
    raw = row.get("pct_chg")
    if raw is None or not math.isfinite(raw):
        return None
    return raw / 100.0 if pct_chg_is_percent else raw


def _date_selection(
    available: list[str],
    *,
    dates: Iterable[str | date] | None,
    start_date: str | date | None,
    end_date: str | date | None,
) -> list[str]:
    if dates is not None:
        available_set = set(available)
        selected = sorted({_iso_date(value) for value in dates} & available_set)
    else:
        selected = available[:]
    if start_date is not None:
        selected = [day for day in selected if day >= _iso_date(start_date)]
    if end_date is not None:
        selected = [day for day in selected if day <= _iso_date(end_date)]
    return selected


def _latest_net_value(
    conn: sqlite3.Connection,
    sector_name: str,
    first_date: str,
    method_version: str,
) -> float:
    row = conn.execute(
        "SELECT net_value FROM sector_composite_daily "
        "WHERE sector_name=? AND trade_date<? AND method_version=? "
        "ORDER BY trade_date DESC LIMIT 1",
        (sector_name, first_date, method_version),
    ).fetchone()
    value = _as_float(row[0]) if row else None
    return value if value is not None and value > 0 else 1000.0


def _existing_net_values_for_day(
    conn: sqlite3.Connection,
    day: str,
    method_version: str,
) -> dict[str, float]:
    rows = conn.execute(
        "SELECT sector_name, net_value FROM sector_composite_daily "
        "WHERE trade_date=? AND method_version=?",
        (day, method_version),
    ).fetchall()
    values: dict[str, float] = {}
    for sector_name, raw_value in rows:
        value = _as_float(raw_value)
        if sector_name is not None and value is not None and value > 0:
            values[str(sector_name)] = value
    return values


def _quality_flag(
    *,
    eligible_count: int,
    priced_count: int,
    coverage_ratio: float,
    min_coverage: float,
    min_breadth: int,
) -> str:
    if priced_count == 0:
        return "NO_DATA"
    if eligible_count < min_breadth:
        return "LOW_BREADTH"
    if coverage_ratio < min_coverage:
        return "LOW_COVERAGE"
    return "OK"


def _upsert_row(conn: sqlite3.Connection, row: tuple[object, ...]) -> None:
    conn.execute(
        """
        INSERT INTO sector_composite_daily (
            sector_name, sector_code, trade_date, net_value, pct_chg, volume, amount,
            eligible_count, priced_count, suspended_count, unresolved_count,
            coverage_ratio, membership_snapshot_id, membership_status,
            method_version, quality_flag, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(sector_name, trade_date) DO UPDATE SET
            sector_code=excluded.sector_code,
            net_value=excluded.net_value,
            pct_chg=excluded.pct_chg,
            volume=excluded.volume,
            amount=excluded.amount,
            eligible_count=excluded.eligible_count,
            priced_count=excluded.priced_count,
            suspended_count=excluded.suspended_count,
            unresolved_count=excluded.unresolved_count,
            coverage_ratio=excluded.coverage_ratio,
            membership_snapshot_id=excluded.membership_snapshot_id,
            membership_status=excluded.membership_status,
            method_version=excluded.method_version,
            quality_flag=excluded.quality_flag,
            updated_at=CURRENT_TIMESTAMP
        """,
        row,
    )


def _upsert_ohlc_row(conn: sqlite3.Connection, row: tuple[object, ...]) -> None:
    conn.execute(
        """
        INSERT INTO sector_composite_ohlc_daily (
            sector_name, sector_code, trade_date, open, high, low, close, pct_chg,
            volume, amount, eligible_count, priced_count, suspended_count,
            unresolved_count, coverage_ratio, membership_snapshot_id,
            membership_status, method_version, quality_flag, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(sector_name, trade_date) DO UPDATE SET
            sector_code=excluded.sector_code,
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            pct_chg=excluded.pct_chg,
            volume=excluded.volume,
            amount=excluded.amount,
            eligible_count=excluded.eligible_count,
            priced_count=excluded.priced_count,
            suspended_count=excluded.suspended_count,
            unresolved_count=excluded.unresolved_count,
            coverage_ratio=excluded.coverage_ratio,
            membership_snapshot_id=excluded.membership_snapshot_id,
            membership_status=excluded.membership_status,
            method_version=excluded.method_version,
            quality_flag=excluded.quality_flag,
            updated_at=CURRENT_TIMESTAMP
        """,
        row,
    )


def build_sector_composites(
    conn: sqlite3.Connection,
    *,
    dates: Iterable[str | date] | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    membership_status: str = "current_snapshot_backfill",
    method_version: str = METHOD_VERSION,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_breadth: int = DEFAULT_MIN_BREADTH,
    max_abs_return: float = DEFAULT_MAX_ABS_RETURN,
    pct_chg_is_percent: bool = True,
    preserve_existing_net_values: bool = False,
) -> dict[str, object]:
    """Build or rebuild composite rows for a bounded range, idempotently.

    ``membership_status='current_snapshot_backfill'`` is the safe explicit
    label for historical reconstruction from today's map.  Daily callers can
    use ``daily_snapshot`` once their date-effective map is available.

    ``preserve_existing_net_values`` is reserved for a bounded display-OHLC
    backfill.  It requires a complete canonical net-value row for every
    selected sector/date and writes only ``sector_composite_ohlc_daily``.
    """
    ensure_sector_composite_schema(conn)
    available = _available_dates(conn)
    selected_dates = _date_selection(
        available,
        dates=dates,
        start_date=start_date,
        end_date=end_date,
    )
    if not selected_dates:
        return {
            "rows_written": 0,
            "dates": [],
            "sectors": 0,
            "quality_counts": {},
            "method_version": method_version,
        }

    snapshots = capture_membership_snapshots(
        conn,
        effective_date=selected_dates[0],
        membership_status=membership_status,
    )
    if not snapshots:
        return {
            "rows_written": 0,
            "dates": selected_dates,
            "sectors": 0,
            "quality_counts": {},
            "method_version": method_version,
        }

    available_indices = {day: index for index, day in enumerate(available)}
    previous_date_by_date: dict[str, str | None] = {}
    for day in selected_dates:
        index = available_indices.get(day, 0)
        previous_date_by_date[day] = available[index - 1] if index > 0 else None

    quality_counts: Counter[str] = Counter()
    rows_written = 0
    ohlc_rows_written = 0
    members_by_sector: dict[str, list[str]] = {}
    net_values: dict[str, float] = {}
    for sector_name, snapshot in sorted(snapshots.items()):
        members = load_snapshot_members(conn, snapshot.snapshot_id)
        members_by_sector[sector_name] = members
        net_values[sector_name] = _latest_net_value(
            conn, sector_name, selected_dates[0], method_version
        )

    # Process one trading date at a time.  A three-year rebuild therefore
    # keeps only two daily stock-row dictionaries in memory instead of all
    # millions of rows in the requested range.
    previous_cache_day: str | None = None
    previous_cache_rows: dict[str, dict[str, float | None]] = {}
    for day in selected_dates:
        canonical_net_values = (
            _existing_net_values_for_day(conn, day, method_version)
            if preserve_existing_net_values
            else {}
        )
        if preserve_existing_net_values:
            missing_net_values = sorted(set(snapshots).difference(canonical_net_values))
            if missing_net_values:
                raise ValueError(
                    "canonical net values missing for OHLC-only backfill: "
                    f"date={day}, count={len(missing_net_values)}"
                )
        previous_day = previous_date_by_date[day]
        if previous_day and previous_day == previous_cache_day:
            previous_day_rows = previous_cache_rows
        elif previous_day:
            previous_day_rows = _fetch_day(conn, previous_day)
        else:
            previous_day_rows = {}
        current_rows = _fetch_day(conn, day)
        for sector_name, snapshot in sorted(snapshots.items()):
            members = members_by_sector[sector_name]
            net_value = net_values[sector_name]
            previous_net_value = net_value
            eligible_count = len(members)
            priced_count = 0
            suspended_count = 0
            unresolved_count = 0
            returns: list[float] = []
            ohlc_returns: list[tuple[float, float, float, float]] = []
            volume = 0.0
            amount = 0.0
            ohlc_volume = 0.0
            ohlc_amount = 0.0
            ohlc_incomplete = False

            for symbol in members:
                current = current_rows.get(symbol)
                if current is None:
                    unresolved_count += 1
                    continue
                current_close = current.get("close")
                if current_close is not None and current_close <= 0:
                    unresolved_count += 1
                    continue
                stock_return = _pct_return(
                    current,
                    previous_day_rows.get(symbol),
                    pct_chg_is_percent=pct_chg_is_percent,
                )
                if stock_return is None or not math.isfinite(stock_return):
                    unresolved_count += 1
                    continue
                if abs(stock_return) > max_abs_return:
                    unresolved_count += 1
                    continue

                priced_count += 1
                returns.append(stock_return)
                volume += current.get("volume") or 0.0
                amount += current.get("amount") or 0.0
                previous = previous_day_rows.get(symbol)
                previous_close = previous.get("close") if previous else None

                field_returns: list[float] = []
                for field in ("open", "high", "low", "close"):
                    current_value = current.get(field)
                    previous_value = previous.get("close") if previous else None
                    if (
                        current_value is None
                        or current_value <= 0
                        or previous_value is None
                        or previous_value <= 0
                    ):
                        field_returns = []
                        break
                    field_return = current_value / previous_value - 1.0
                    if not math.isfinite(field_return) or abs(field_return) > max_abs_return:
                        field_returns = []
                        break
                    field_returns.append(field_return)
                if len(field_returns) == 4:
                    ohlc_returns.append(tuple(field_returns))
                    ohlc_volume += current.get("volume") or 0.0
                    ohlc_amount += current.get("amount") or 0.0
                else:
                    ohlc_incomplete = True

                if abs(stock_return) < 1e-12 and (
                    (current.get("volume") is not None and current.get("volume") <= 0)
                    or (
                        current_close is not None
                        and previous_close is not None
                        and abs(current_close - previous_close) < 1e-12
                    )
                ):
                    suspended_count += 1

            sector_return = sum(returns) / len(returns) if returns else 0.0
            if preserve_existing_net_values:
                net_value = canonical_net_values[sector_name]
                net_values[sector_name] = net_value
            elif returns:
                net_value *= 1.0 + sector_return
                net_values[sector_name] = net_value
            coverage_ratio = priced_count / eligible_count if eligible_count else 0.0
            quality_flag = _quality_flag(
                eligible_count=eligible_count,
                priced_count=priced_count,
                coverage_ratio=coverage_ratio,
                min_coverage=min_coverage,
                min_breadth=min_breadth,
            )
            quality_counts[quality_flag] += 1
            if not preserve_existing_net_values:
                _upsert_row(
                    conn,
                    (
                        sector_name,
                        snapshot.sector_code,
                        day,
                        net_value,
                        sector_return * 100.0,
                        volume,
                        amount,
                        eligible_count,
                        priced_count,
                        suspended_count,
                        unresolved_count,
                        coverage_ratio,
                        snapshot.snapshot_id,
                        snapshot.membership_status,
                        method_version,
                        quality_flag,
                    ),
                )
                rows_written += 1
            ohlc_priced_count = len(ohlc_returns)
            ohlc_coverage_ratio = (
                ohlc_priced_count / eligible_count if eligible_count else 0.0
            )
            if ohlc_priced_count == 0:
                ohlc_quality_flag = "NO_DATA"
                ohlc_open = ohlc_high = ohlc_low = net_value
            else:
                ohlc_open = previous_net_value * (
                    1.0 + sum(item[0] for item in ohlc_returns) / ohlc_priced_count
                )
                ohlc_high = previous_net_value * (
                    1.0 + sum(item[1] for item in ohlc_returns) / ohlc_priced_count
                )
                ohlc_low = previous_net_value * (
                    1.0 + sum(item[2] for item in ohlc_returns) / ohlc_priced_count
                )
                # close is deliberately the canonical composite net value;
                # this keeps RPS and estimated OHLC on exactly one close.
                ohlc_close = net_value
                ohlc_high = max(ohlc_high, ohlc_open, ohlc_close)
                ohlc_low = min(ohlc_low, ohlc_open, ohlc_close)
                if eligible_count < min_breadth:
                    ohlc_quality_flag = "LOW_BREADTH"
                elif ohlc_coverage_ratio < min_coverage:
                    ohlc_quality_flag = "LOW_COVERAGE"
                elif ohlc_incomplete or ohlc_priced_count != priced_count:
                    ohlc_quality_flag = "OHLC_INCOMPLETE"
                else:
                    ohlc_quality_flag = quality_flag
            if ohlc_priced_count == 0:
                ohlc_close = net_value
            ohlc_pct_chg = sector_return * 100.0
            if preserve_existing_net_values and previous_net_value > 0:
                ohlc_pct_chg = (ohlc_close / previous_net_value - 1.0) * 100.0
            _upsert_ohlc_row(
                conn,
                (
                    sector_name,
                    snapshot.sector_code,
                    day,
                    ohlc_open,
                    ohlc_high,
                    ohlc_low,
                    ohlc_close,
                    ohlc_pct_chg,
                    ohlc_volume,
                    ohlc_amount,
                    eligible_count,
                    ohlc_priced_count,
                    suspended_count,
                    eligible_count - ohlc_priced_count,
                    ohlc_coverage_ratio,
                    snapshot.snapshot_id,
                    snapshot.membership_status,
                    OHLC_METHOD_VERSION,
                    ohlc_quality_flag,
                ),
            )
            ohlc_rows_written += 1

        # Commit per date so a long historical rebuild has bounded WAL growth
        # and can be safely resumed by rerunning the same range.
        conn.commit()
        previous_cache_day = day
        previous_cache_rows = current_rows

    return {
        "rows_written": rows_written,
        "ohlc_rows_written": ohlc_rows_written,
        "dates": selected_dates,
        "sectors": len(snapshots),
        "quality_counts": dict(quality_counts),
        "method_version": method_version,
    }


__all__ = [
    "DEFAULT_MAX_ABS_RETURN",
    "DEFAULT_MIN_BREADTH",
    "DEFAULT_MIN_COVERAGE",
    "METHOD_VERSION",
    "OHLC_METHOD_VERSION",
    "build_sector_composites",
    "ensure_sector_composite_schema",
]
