#!/usr/bin/env python3
"""Apply the Data Core v0.1 compatibility schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from data_core.schema import ensure_data_core_schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate Data Core schema")
    parser.add_argument("--db", default=settings.LOCAL_DB_PATH, help="SQLite database path")
    parser.add_argument(
        "--backfill-legacy-metadata",
        action="store_true",
        help="physically backfill market columns on legacy rows",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = ensure_data_core_schema(
        args.db,
        backfill_legacy_metadata=args.backfill_legacy_metadata,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

