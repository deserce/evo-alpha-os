#!/usr/bin/env python3
"""Create the versioned membership and local sector composite tables."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from data_core.sector_composite import ensure_sector_composite_schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create EvoAlpha sector membership/composite tables"
    )
    parser.add_argument(
        "--db",
        default=settings.LOCAL_DB_PATH,
        help="SQLite database path (default: configured local database)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        ensure_sector_composite_schema(conn)
        conn.commit()
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN (?, ?, ?, ?) ORDER BY name",
                (
                    "sector_membership_version",
                    "sector_membership_item",
                    "sector_composite_daily",
                    "sector_composite_ohlc_daily",
                ),
            ).fetchall()
        ]
    print(json.dumps({"db_path": str(db_path), "tables": tables}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
