#!/usr/bin/env python3
"""Apply the idempotent portfolio accounts and transaction ledger schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from portfolio.schema import (
    DEFAULT_BUSY_TIMEOUT_MS,
    MAX_BUSY_TIMEOUT_MS,
    ensure_portfolio_schema,
)


def _bounded_timeout(value: str) -> int:
    try:
        timeout = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 0 <= timeout <= MAX_BUSY_TIMEOUT_MS:
        raise argparse.ArgumentTypeError(
            f"must be between 0 and {MAX_BUSY_TIMEOUT_MS}"
        )
    return timeout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate portfolio accounts and transaction ledger schema"
    )
    parser.add_argument(
        "--db",
        default=settings.LOCAL_DB_PATH,
        help="target SQLite database path",
    )
    parser.add_argument(
        "--busy-timeout-ms",
        type=_bounded_timeout,
        default=DEFAULT_BUSY_TIMEOUT_MS,
        help=f"SQLite lock wait in milliseconds (0-{MAX_BUSY_TIMEOUT_MS})",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = ensure_portfolio_schema(
        args.db,
        busy_timeout_ms=args.busy_timeout_ms,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
