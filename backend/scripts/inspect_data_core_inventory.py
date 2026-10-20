#!/usr/bin/env python3
"""Generate a read-only Data Core source and database inventory report."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from data_core.quality import latest_quality_reports, latest_source_failures
from data_core.source_registry import all_sources, table_definition_map, table_definitions
from data_core.symbols import P0_MARKETS, market_case_sql


DEFAULT_OUTPUT = BACKEND_DIR / "docs" / "DATA_CORE_SOURCE_AND_DB_MAP.md"


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def table_row_count(conn: sqlite3.Connection, table_name: str) -> int | None:
    if not table_exists(conn, table_name):
        return None
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def table_date_range(conn: sqlite3.Connection, table_name: str, date_column: str | None) -> tuple[Any, Any]:
    if not date_column or not table_exists(conn, table_name):
        return None, None
    if date_column not in table_columns(conn, table_name):
        return None, None
    row = conn.execute(
        f"SELECT MIN({date_column}), MAX({date_column}) FROM {table_name}"
    ).fetchone()
    return row[0], row[1]


def stock_market_counts(conn: sqlite3.Connection, table_name: str, date_filter: str | None = None) -> dict[str, int]:
    if not table_exists(conn, table_name):
        return {}
    where = ""
    params: tuple[Any, ...] = ()
    if date_filter:
        where = "WHERE DATE(trade_date)=DATE(?)"
        params = (date_filter,)
    rows = conn.execute(
        f"""
        SELECT {market_case_sql('symbol')} AS market, COUNT(DISTINCT symbol) AS count
        FROM {table_name}
        {where}
        GROUP BY market
        """,
        params,
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def stock_info_market_counts(conn: sqlite3.Connection) -> dict[str, int]:
    if not table_exists(conn, "stock_info"):
        return {}
    rows = conn.execute(
        f"""
        SELECT {market_case_sql('symbol')} AS market, COUNT(DISTINCT symbol) AS count
        FROM stock_info
        GROUP BY market
        """
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def latest_stock_date(conn: sqlite3.Connection) -> str | None:
    if not table_exists(conn, "stock_daily_prices"):
        return None
    row = conn.execute("SELECT MAX(trade_date) FROM stock_daily_prices").fetchone()
    return str(row[0]) if row and row[0] else None


def table_inventory(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    registry = table_definition_map()
    actual_tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    }
    names = list(dict.fromkeys([definition.name for definition in table_definitions()] + sorted(actual_tables)))
    result: list[dict[str, Any]] = []
    for table_name in names:
        definition = registry.get(table_name)
        date_column = definition.date_column if definition else None
        min_date, max_date = table_date_range(conn, table_name, date_column)
        result.append(
            {
                "name": table_name,
                "display_name": definition.display_name if definition else "-",
                "role": definition.role if definition else "unclassified",
                "module": definition.module if definition else "-",
                "rows": table_row_count(conn, table_name),
                "date_column": date_column,
                "min_date": min_date,
                "max_date": max_date,
                "p0_quality_gate": bool(definition and definition.p0_quality_gate),
                "description": definition.description if definition else "",
            }
        )
    return result


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    return ", ".join(f"{market}:{counts.get(market, 0)}" for market in P0_MARKETS)


def build_report(db_path: str | Path) -> str:
    db = Path(db_path)
    lines: list[str] = [
        "# Data Core Source And DB Map",
        "",
        f"Generated at: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Database: `{db}`",
        "",
    ]

    if not db.exists():
        lines.extend(["## Database", "", "Database file does not exist.", ""])
        return "\n".join(lines)

    with sqlite3.connect(db) as conn:
        stock_latest = latest_stock_date(conn)
        stock_info_counts = stock_info_market_counts(conn)
        latest_daily_counts = stock_market_counts(conn, "stock_daily_prices", stock_latest) if stock_latest else {}
        latest_reports = latest_quality_reports(conn)
        failures = latest_source_failures(conn, limit=8)
        inventory = table_inventory(conn)

    lines.extend(
        [
            "## Summary",
            "",
            f"- Latest stock daily date: `{stock_latest or '-'}`",
            f"- Stock universe by market: {format_counts(stock_info_counts)}",
            f"- Latest daily rows by market: {format_counts(latest_daily_counts)}",
            "- Formal stock daily writes are TDX-only. HTTP sources are diagnostic or legacy only.",
            "",
            "## Source Registry",
            "",
            "| Source | Module | Role | Official Write | Blocks Downstream | Applies To |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for source in all_sources():
        lines.append(
            f"| `{source.id}` | {source.module} | {source.role} | "
            f"{'yes' if source.allowed_official_write else 'no'} | "
            f"{'yes' if source.blocks_downstream else 'no'} | {source.applies_to} |"
        )

    lines.extend(
        [
            "",
            "## Table Inventory",
            "",
            "| Table | Role | Module | Rows | Date Range | P0 Gate |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for item in inventory:
        rows = "-" if item["rows"] is None else str(item["rows"])
        date_range = "-"
        if item["date_column"]:
            date_range = f"{item['min_date'] or '-'}..{item['max_date'] or '-'}"
        lines.append(
            f"| `{item['name']}` | {item['role']} | {item['module']} | {rows} | "
            f"{date_range} | {'yes' if item['p0_quality_gate'] else 'no'} |"
        )

    lines.extend(["", "## Latest Quality Reports", ""])
    if not latest_reports:
        lines.append("No data_quality_report rows found.")
    else:
        lines.extend(
            [
                "| Date | Module | Market | Status | Expected | Actual | Missing | Score |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for report in latest_reports[:12]:
            score = report.get("quality_score")
            score_label = "-" if score is None else f"{float(score):.2f}"
            lines.append(
                f"| {report.get('trade_date')} | {report.get('module')} | {report.get('market')} | "
                f"{report.get('status')} | {report.get('expected_count')} | "
                f"{report.get('actual_count')} | {report.get('missing_count')} | {score_label} |"
            )

    lines.extend(["", "## Latest Source Failures", ""])
    if not failures:
        lines.append("No recent source failures found.")
    else:
        lines.extend(
            [
                "| Source | API | Market | Status | Fail Count | Error | Created At |",
                "| --- | --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for failure in failures:
            error = str(failure.get("error_message") or "-").replace("|", "/")
            lines.append(
                f"| `{failure.get('source')}` | {failure.get('api_name')} | "
                f"{failure.get('market')} | {failure.get('status')} | "
                f"{failure.get('fail_count')} | {error} | {failure.get('created_at')} |"
            )

    blockers = [
        report for report in latest_reports if str(report.get("status")) == "FAIL"
    ]
    lines.extend(["", "## Current Blockers", ""])
    if not blockers:
        lines.append("- No FAIL quality reports in the latest Data Core snapshot.")
    else:
        for report in blockers:
            lines.append(
                f"- `{report.get('module')}[{report.get('market')}]` is FAIL on "
                f"`{report.get('trade_date')}`: actual={report.get('actual_count')}, "
                f"expected={report.get('expected_count')}, missing={report.get('missing_count')}."
            )

    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect Data Core source and DB inventory")
    parser.add_argument("--db", default=settings.LOCAL_DB_PATH, help="SQLite DB path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Markdown report path")
    parser.add_argument("--stdout", action="store_true", help="print report instead of writing it")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(args.db)
    if args.stdout:
        print(report)
        return 0

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
