#!/usr/bin/env python3
"""Backfill stock_info.list_date from exchange-specific AkShare stock lists."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings


SOURCE_NAME = "akshare_exchange_stock_info"
MARKET_FETCHERS = {
    "SH": (
        ("stock_info_sh_name_code", {"symbol": "主板A股"}),
        ("stock_info_sh_name_code", {"symbol": "科创板"}),
        ("stock_info_sh_delist", {"symbol": "全部"}),
    ),
    "SZ": (
        ("stock_info_sz_name_code", {"symbol": "A股列表"}),
        ("stock_info_sz_delist", {"symbol": "终止上市公司"}),
    ),
    "BSE": (("stock_info_bj_name_code", {}),),
}


@dataclass(frozen=True)
class ListingRecord:
    symbol: str
    name: str | None
    list_date: str | None
    market: str
    source: str


def normalize_symbol(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


def normalize_date(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "-"}:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _first_existing_column(df: pd.DataFrame, columns: Iterable[str]) -> str | None:
    for column in columns:
        if column in df.columns:
            return column
    return None


def listing_records_from_frame(
    df: pd.DataFrame,
    *,
    market: str,
    source: str,
) -> list[ListingRecord]:
    symbol_column = _first_existing_column(
        df,
        ("证券代码", "A股代码", "公司代码", "股票代码", "代码", "code", "symbol"),
    )
    name_column = _first_existing_column(
        df,
        ("证券简称", "A股简称", "公司简称", "股票简称", "名称", "name"),
    )
    date_column = _first_existing_column(df, ("上市日期", "A股上市日期", "list_date"))
    if not symbol_column or not date_column:
        return []

    records: list[ListingRecord] = []
    for row in df.to_dict("records"):
        symbol = normalize_symbol(row.get(symbol_column))
        list_date = normalize_date(row.get(date_column))
        if not symbol or not list_date:
            continue
        name = row.get(name_column) if name_column else None
        records.append(
            ListingRecord(
                symbol=symbol,
                name=str(name).strip() if name is not None and str(name).strip() else None,
                list_date=list_date,
                market=market,
                source=source,
            )
        )
    return records


def fetch_listing_records(markets: Iterable[str]) -> tuple[list[ListingRecord], list[dict[str, str]]]:
    import akshare as ak

    records: list[ListingRecord] = []
    errors: list[dict[str, str]] = []
    for market in markets:
        normalized = str(market).upper()
        for function_name, kwargs in MARKET_FETCHERS[normalized]:
            source_variant = ",".join(f"{key}={value}" for key, value in kwargs.items())
            source = f"{SOURCE_NAME}.{function_name}"
            if source_variant:
                source = f"{source}[{source_variant}]"
            try:
                df = getattr(ak, function_name)(**kwargs)
                records.extend(
                    listing_records_from_frame(
                        df,
                        market=normalized,
                        source=source,
                    )
                )
            except Exception as exc:
                errors.append(
                    {
                        "market": normalized,
                        "source": source,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    return records, errors


def ensure_stock_info_columns(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_info (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            list_date TEXT,
            market TEXT
        )
        """
    )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(stock_info)").fetchall()}
    for column_name, column_type in (
        ("name", "TEXT"),
        ("list_date", "TEXT"),
        ("market", "TEXT"),
    ):
        if column_name not in columns:
            conn.execute(f"ALTER TABLE stock_info ADD COLUMN {column_name} {column_type}")


def backup_database(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"local_quant_before_stock_info_list_date_{timestamp}.db"
    temp_path = backup_path.with_suffix(".tmp")
    shutil.copy2(db_path, temp_path)
    temp_path.replace(backup_path)
    return backup_path


def apply_listing_records(
    db_path: str | Path,
    records: Iterable[ListingRecord],
    *,
    dry_run: bool = False,
    insert_missing: bool = False,
) -> dict[str, object]:
    rows = list(dict((record.symbol, record) for record in records).values())
    summary: dict[str, object] = {
        "records": len(rows),
        "inserted": 0,
        "updated_list_date": 0,
        "updated_market": 0,
        "unchanged": 0,
        "ignored_missing": 0,
        "dry_run": dry_run,
        "insert_missing": insert_missing,
    }
    if not rows:
        return summary

    with sqlite3.connect(db_path) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stock_info'"
        ).fetchone()
        existing: dict[str, dict[str, object]] = {}
        if table_exists:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(stock_info)").fetchall()}
            if "symbol" in columns:
                list_date_expr = "list_date" if "list_date" in columns else "NULL"
                market_expr = "market" if "market" in columns else "NULL"
                existing = {
                    str(row[0]).zfill(6): {"list_date": row[1], "market": row[2]}
                    for row in conn.execute(
                        f"""
                        SELECT symbol, {list_date_expr}, {market_expr}
                        FROM stock_info
                        WHERE symbol IN ({",".join("?" for _ in rows)})
                        """,
                        tuple(record.symbol for record in rows),
                    ).fetchall()
                }

        for record in rows:
            current = existing.get(record.symbol)
            if current is None:
                if insert_missing:
                    summary["inserted"] = int(summary["inserted"]) + 1
                else:
                    summary["ignored_missing"] = int(summary["ignored_missing"]) + 1
                continue
            changed = False
            if not current.get("list_date") and record.list_date:
                summary["updated_list_date"] = int(summary["updated_list_date"]) + 1
                changed = True
            if current.get("market") != record.market:
                summary["updated_market"] = int(summary["updated_market"]) + 1
                changed = True
            if not changed:
                summary["unchanged"] = int(summary["unchanged"]) + 1

        if dry_run:
            return summary

        rows_to_apply = [
            record for record in rows if insert_missing or record.symbol in existing
        ]
        if not rows_to_apply:
            return summary

        ensure_stock_info_columns(conn)
        conn.executemany(
            """
            INSERT INTO stock_info (symbol, name, list_date, market)
            VALUES (:symbol, :name, :list_date, :market)
            ON CONFLICT(symbol) DO UPDATE SET
                name = CASE
                    WHEN stock_info.name IS NULL OR stock_info.name = ''
                    THEN excluded.name
                    ELSE stock_info.name
                END,
                list_date = CASE
                    WHEN stock_info.list_date IS NULL OR stock_info.list_date = ''
                    THEN excluded.list_date
                    ELSE stock_info.list_date
                END,
                market = excluded.market
            """,
            [asdict(record) for record in rows_to_apply],
        )
        conn.commit()
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill stock_info.list_date")
    parser.add_argument("--db", default=settings.LOCAL_DB_PATH, help="SQLite database path")
    parser.add_argument(
        "--markets",
        default="SH,SZ,BSE",
        help="comma-separated markets: SH,SZ,BSE",
    )
    parser.add_argument("--dry-run", action="store_true", help="fetch and summarize without writing")
    parser.add_argument("--no-backup", action="store_true", help="skip pre-write SQLite backup")
    return parser


def parse_markets(value: str) -> list[str]:
    markets = [item.strip().upper() for item in value.split(",") if item.strip()]
    invalid = [market for market in markets if market not in MARKET_FETCHERS]
    if invalid:
        raise ValueError(f"invalid markets: {invalid}")
    return markets


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    markets = parse_markets(args.markets)
    records, errors = fetch_listing_records(markets)

    backup_path = None
    if not args.dry_run and not args.no_backup:
        backup_path = backup_database(db_path)

    summary = apply_listing_records(db_path, records, dry_run=args.dry_run)
    output = {
        "source": SOURCE_NAME,
        "markets": markets,
        "backup_path": str(backup_path) if backup_path else None,
        "summary": summary,
        "errors": errors,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
