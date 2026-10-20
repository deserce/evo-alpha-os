#!/usr/bin/env python3
"""Repair recent stock_daily_prices gaps with the official TDX-only collector."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from data_core.bse_codes import TDX_STANDARD_BJ_SOURCE_NAME
from data_core.quality import ACCEPTED_GAP_STATUS, BSE_UNADJUSTED_SOURCES, GAP_EXCEPTION_TABLE
from data_core.schema import ensure_data_core_schema, log_data_source_run, table_columns, table_exists
from data_core.symbols import MARKET_BSE, P0_MARKETS, infer_market, market_where_sql


SOURCE_NAME = "tdx_stock_daily_gap_repair"
DEFAULT_WINDOW_TRADING_DAYS = 45
DEFAULT_MAX_BARS = 120
DEFAULT_WORKERS = 2
DEFAULT_POOL_SIZE = 2
DEFAULT_SUBMIT_WINDOW = 16


@dataclass(frozen=True)
class GapItem:
    symbol: str
    trade_date: str
    market: str
    reason: str


@dataclass(frozen=True)
class GapScan:
    end_date: str
    trading_dates: tuple[str, ...]
    raw_missing_count: int
    exception_count: int
    effective_missing_count: int
    abnormal_count: int
    candidates: tuple[GapItem, ...]
    top_dates: tuple[dict[str, object], ...]

    @property
    def candidate_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.symbol for item in self.candidates))

    def to_dict(self, sample_limit: int = 30) -> dict[str, object]:
        return {
            "end_date": self.end_date,
            "window_start": self.trading_dates[0] if self.trading_dates else None,
            "window_end": self.trading_dates[-1] if self.trading_dates else None,
            "trading_date_count": len(self.trading_dates),
            "raw_missing_count": self.raw_missing_count,
            "exception_count": self.exception_count,
            "effective_missing_count": self.effective_missing_count,
            "abnormal_count": self.abnormal_count,
            "candidate_symbol_count": len(self.candidate_symbols),
            "candidate_samples": [
                {
                    "symbol": item.symbol,
                    "trade_date": item.trade_date,
                    "market": item.market,
                    "reason": item.reason,
                }
                for item in self.candidates[:sample_limit]
            ],
            "top_dates": list(self.top_dates),
        }


def readonly_connect(db_path: str | Path) -> sqlite3.Connection:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _date(value: str) -> str:
    return datetime.fromisoformat(str(value)[:10]).date().isoformat()


def _day_bounds(value: str) -> tuple[str, str]:
    start = datetime.fromisoformat(_date(value)).date()
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _list_date_sql(column_name: str) -> str:
    return (
        "CASE "
        f"WHEN {column_name} IS NULL OR {column_name} = '' THEN NULL "
        f"WHEN LENGTH({column_name}) = 8 AND {column_name} NOT LIKE '%-%' "
        f"THEN SUBSTR({column_name}, 1, 4) || '-' || SUBSTR({column_name}, 5, 2) || '-' || SUBSTR({column_name}, 7, 2) "
        f"ELSE {column_name} END"
    )


def stock_symbol_column(conn: sqlite3.Connection) -> str | None:
    if not table_exists(conn, "stock_info"):
        return None
    columns = table_columns(conn, "stock_info")
    if "symbol" in columns:
        return "symbol"
    if "code" in columns:
        return "code"
    return None


def recent_trading_dates(
    conn: sqlite3.Connection,
    *,
    end_date: str,
    window_trading_days: int,
) -> tuple[str, ...]:
    if table_exists(conn, "trading_calendar"):
        rows = conn.execute(
            """
            SELECT DISTINCT DATE(trade_date) AS trade_date
            FROM trading_calendar
            WHERE DATE(trade_date) <= DATE(?)
              AND is_open = 1
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (end_date, max(1, window_trading_days)),
        ).fetchall()
        dates = [str(row[0]) for row in rows if row[0]]
        if dates:
            return tuple(reversed(dates))

    if table_exists(conn, "stock_daily_prices"):
        rows = conn.execute(
            """
            SELECT DISTINCT DATE(trade_date) AS trade_date
            FROM stock_daily_prices
            WHERE DATE(trade_date) <= DATE(?)
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (end_date, max(1, window_trading_days)),
        ).fetchall()
        dates = [str(row[0]) for row in rows if row[0]]
        if dates:
            return tuple(reversed(dates))

    return (end_date,)


def expected_symbols(conn: sqlite3.Connection, trade_date: str, market: str) -> set[str]:
    symbol_column = stock_symbol_column(conn)
    if symbol_column:
        columns = table_columns(conn, "stock_info")
        filters = [market_where_sql(market, f"s.{symbol_column}")]
        params: list[object] = []
        if "list_date" in columns:
            list_date = _list_date_sql("s.list_date")
            filters.append("(s.list_date IS NULL OR s.list_date = '' OR DATE(" + list_date + ") <= DATE(?))")
            params.append(trade_date)
        rows = conn.execute(
            f"""
            SELECT DISTINCT s.{symbol_column}
            FROM stock_info s
            WHERE {' AND '.join(filters)}
            ORDER BY s.{symbol_column}
            """,
            tuple(params),
        ).fetchall()
        return {str(row[0]).zfill(6) for row in rows}

    if not table_exists(conn, "stock_daily_prices"):
        return set()
    rows = conn.execute(
        f"""
        SELECT DISTINCT symbol
        FROM stock_daily_prices
        WHERE {market_where_sql(market, 'symbol')}
          AND DATE(trade_date) <= DATE(?)
        """,
        (trade_date,),
    ).fetchall()
    return {str(row[0]).zfill(6) for row in rows}


def present_symbols(conn: sqlite3.Connection, trade_date: str, market: str) -> set[str]:
    if not table_exists(conn, "stock_daily_prices"):
        return set()
    day_start, day_end = _day_bounds(trade_date)
    rows = conn.execute(
        f"""
        SELECT DISTINCT symbol
        FROM stock_daily_prices
        WHERE trade_date >= ?
          AND trade_date < ?
          AND {market_where_sql(market, 'symbol')}
        """,
        (day_start, day_end),
    ).fetchall()
    return {str(row[0]).zfill(6) for row in rows}


def accepted_exception_symbols(conn: sqlite3.Connection, trade_date: str, market: str) -> set[str]:
    if not table_exists(conn, GAP_EXCEPTION_TABLE):
        return set()
    day_start, day_end = _day_bounds(trade_date)
    rows = conn.execute(
        f"""
        SELECT DISTINCT symbol
        FROM {GAP_EXCEPTION_TABLE}
        WHERE trade_date >= ?
          AND trade_date < ?
          AND status = ?
          AND {market_where_sql(market, 'symbol')}
        """,
        (day_start, day_end, ACCEPTED_GAP_STATUS),
    ).fetchall()
    return {str(row[0]).zfill(6) for row in rows}


def abnormal_symbols(conn: sqlite3.Connection, trade_date: str, market: str) -> set[str]:
    if not table_exists(conn, "stock_daily_prices"):
        return set()
    columns = table_columns(conn, "stock_daily_prices")
    conditions: list[str] = []
    if {"high", "low"}.issubset(columns):
        conditions.append("high < low")
    if {"close", "high", "low"}.issubset(columns):
        conditions.append("close < low OR close > high")
    for column in ("open", "high", "low", "close"):
        if column in columns:
            conditions.append(f"{column} IS NULL OR {column} <= 0")
    for column in ("volume", "amount"):
        if column in columns:
            conditions.append(f"{column} IS NULL OR {column} < 0")
    if "turnover_rate" in columns:
        if market == MARKET_BSE and {"source", "adjustment_mode"}.issubset(columns):
            sources = ", ".join(f"'{source}'" for source in BSE_UNADJUSTED_SOURCES)
            conditions.append(
                "turnover_rate IS NULL "
                f"AND NOT (source IN ({sources}) AND adjustment_mode = 'none')"
            )
        else:
            conditions.append("turnover_rate IS NULL")
    if {"pct_chg", "symbol", "trade_date", "close"}.issubset(columns):
        conditions.append(
            "pct_chg IS NULL AND EXISTS ("
            "SELECT 1 FROM stock_daily_prices previous "
            "WHERE previous.symbol = stock_daily_prices.symbol "
            "AND previous.trade_date < stock_daily_prices.trade_date "
            "AND previous.close IS NOT NULL AND previous.close > 0"
            ")"
        )
    if not conditions:
        return set()
    day_start, day_end = _day_bounds(trade_date)
    rows = conn.execute(
        f"""
        SELECT DISTINCT symbol
        FROM stock_daily_prices
        WHERE trade_date >= ?
          AND trade_date < ?
          AND {market_where_sql(market, 'symbol')}
          AND ({' OR '.join(f'({condition})' for condition in conditions)})
        """,
        (day_start, day_end),
    ).fetchall()
    return {str(row[0]).zfill(6) for row in rows}


def scan_stock_daily_gaps(
    db_path: str | Path,
    *,
    end_date: str,
    window_trading_days: int = DEFAULT_WINDOW_TRADING_DAYS,
    markets: Iterable[str] = P0_MARKETS,
    readonly: bool = True,
) -> GapScan:
    db = Path(db_path)
    connect = readonly_connect if readonly else sqlite3.connect
    with connect(db) as conn:
        dates = recent_trading_dates(
            conn,
            end_date=_date(end_date),
            window_trading_days=max(1, int(window_trading_days)),
        )
        raw_missing_count = 0
        exception_count = 0
        effective_missing_count = 0
        abnormal_count = 0
        candidates: list[GapItem] = []
        date_counts: Counter[str] = Counter()
        market_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)

        for trade_date in dates:
            for market in markets:
                normalized_market = str(market).upper()
                expected = expected_symbols(conn, trade_date, normalized_market)
                if not expected:
                    continue
                present = present_symbols(conn, trade_date, normalized_market)
                raw_missing = expected - present
                accepted = raw_missing & accepted_exception_symbols(conn, trade_date, normalized_market)
                effective_missing = raw_missing - accepted
                abnormal = abnormal_symbols(conn, trade_date, normalized_market)

                raw_missing_count += len(raw_missing)
                exception_count += len(accepted)
                effective_missing_count += len(effective_missing)
                abnormal_count += len(abnormal)
                date_counts[trade_date] += len(effective_missing) + len(abnormal)
                market_counts[trade_date][normalized_market] += len(effective_missing) + len(abnormal)

                candidates.extend(
                    GapItem(
                        symbol=symbol,
                        trade_date=trade_date,
                        market=normalized_market,
                        reason="missing_kline",
                    )
                    for symbol in sorted(effective_missing)
                )
                candidates.extend(
                    GapItem(
                        symbol=symbol,
                        trade_date=trade_date,
                        market=normalized_market,
                        reason="abnormal_kline",
                    )
                    for symbol in sorted(abnormal)
                )

    top_dates = tuple(
        {
            "trade_date": trade_date,
            "candidate_count": count,
            "markets": dict(market_counts[trade_date]),
        }
        for trade_date, count in date_counts.most_common(10)
        if count > 0
    )
    return GapScan(
        end_date=_date(end_date),
        trading_dates=dates,
        raw_missing_count=raw_missing_count,
        exception_count=exception_count,
        effective_missing_count=effective_missing_count,
        abnormal_count=abnormal_count,
        candidates=tuple(candidates),
        top_dates=top_dates,
    )


def write_symbols_file(run_id: str, symbols: Iterable[str]) -> Path:
    temp_dir = BACKEND_DIR / "data" / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / f"{run_id}_symbols.txt"
    path.write_text("\n".join(symbols) + "\n", encoding="utf-8")
    return path


def build_collector_command(args: argparse.Namespace, run_id: str, symbols_path: Path, result_json: Path) -> list[str]:
    return [
        sys.executable,
        "scripts/standalone/collect_stock_kline_mootdx_qfq.py",
        "--symbols-file",
        str(symbols_path),
        "--end",
        args.end_date,
        "--workers",
        str(args.workers),
        "--pool-size",
        str(args.pool_size),
        "--submit-window",
        str(args.submit_window),
        "--batch-size",
        str(args.batch_size),
        "--lookback-days",
        str(args.lookback_days),
        "--max-bars",
        str(args.max_bars),
        "--max-fail-rate",
        "1.0",
        "--force-window",
        "--run-id",
        run_id,
        "--result-json",
        str(result_json),
    ]


def load_collector_results(result_json: Path) -> dict[str, dict[str, object]]:
    if not result_json.exists():
        return {}
    data = json.loads(result_json.read_text(encoding="utf-8"))
    results = data.get("results", [])
    if not isinstance(results, list):
        return {}
    by_symbol: dict[str, dict[str, object]] = {}
    for item in results:
        if isinstance(item, dict) and item.get("symbol"):
            by_symbol[str(item["symbol"]).zfill(6)] = item
    return by_symbol


def exception_is_allowed(item: GapItem, collector_result: dict[str, object] | None) -> bool:
    if item.reason != "missing_kline" or collector_result is None:
        return False
    status = str(collector_result.get("status") or "")
    reason = str(collector_result.get("reason") or "").lower()
    if status == "success":
        return True
    return status == "fail" and "no raw data" in reason


def upsert_gap_exceptions(
    conn: sqlite3.Connection,
    *,
    items: Iterable[GapItem],
    collector_results: dict[str, dict[str, object]],
    run_id: str,
    collector_returncode: int,
) -> int:
    accepted = 0
    for item in items:
        result = collector_results.get(item.symbol)
        if not exception_is_allowed(item, result):
            continue
        source = (
            str(result.get("source"))
            if result and result.get("source")
            else (TDX_STANDARD_BJ_SOURCE_NAME if item.market == MARKET_BSE else "tdx_mootdx_qfq")
        )
        detail = {
            "run_id": run_id,
            "collector_returncode": collector_returncode,
            "collector_status": result.get("status") if result else None,
            "collector_reason": result.get("reason") if result else None,
            "repair_reason": "tdx_window_returned_no_row"
            if result and result.get("status") == "success"
            else "tdx_no_raw_data",
        }
        conn.execute(
            f"""
            INSERT INTO {GAP_EXCEPTION_TABLE} (
                symbol, trade_date, market, reason, source, attempts,
                first_seen_at, last_seen_at, status, detail_json
            ) VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                market = excluded.market,
                reason = excluded.reason,
                source = excluded.source,
                attempts = COALESCE({GAP_EXCEPTION_TABLE}.attempts, 0) + 1,
                last_seen_at = CURRENT_TIMESTAMP,
                status = excluded.status,
                detail_json = excluded.detail_json
            """,
            (
                item.symbol,
                item.trade_date,
                item.market,
                detail["repair_reason"],
                source,
                ACCEPTED_GAP_STATUS,
                json.dumps(detail, ensure_ascii=False),
            ),
        )
        accepted += 1
    return accepted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair recent stock_daily_prices gaps with TDX-only data")
    parser.add_argument("--db", default=settings.LOCAL_DB_PATH, help="SQLite database path")
    parser.add_argument("--end-date", required=True, help="target completed trade date")
    parser.add_argument("--window-trading-days", type=int, default=DEFAULT_WINDOW_TRADING_DAYS)
    parser.add_argument("--max-bars", type=int, default=DEFAULT_MAX_BARS)
    parser.add_argument("--lookback-days", type=int, default=150)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--pool-size", type=int, default=DEFAULT_POOL_SIZE)
    parser.add_argument("--submit-window", type=int, default=DEFAULT_SUBMIT_WINDOW)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target = _date(args.end_date)
    started = time.time()
    run_id = datetime.now().strftime("gap_repair_%Y%m%d_%H%M%S")

    if not args.dry_run:
        ensure_data_core_schema(args.db)

    before = scan_stock_daily_gaps(
        args.db,
        end_date=target,
        window_trading_days=args.window_trading_days,
        readonly=True,
    )
    print("stock_daily_gap_scan_before=" + json.dumps(before.to_dict(), ensure_ascii=False))

    if args.dry_run or not before.candidate_symbols:
        if not before.candidate_symbols:
            print("stock_daily_gap_repair=no_candidates")
        else:
            print("stock_daily_gap_repair=dry_run")
        return 0

    symbols_path = write_symbols_file(run_id, before.candidate_symbols)
    result_json = symbols_path.with_name(f"{run_id}_collector_results.json")
    command = build_collector_command(args, run_id, symbols_path, result_json)
    print("$ " + " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=BACKEND_DIR, check=False)

    collector_results = load_collector_results(result_json)
    after_download = scan_stock_daily_gaps(
        args.db,
        end_date=target,
        window_trading_days=args.window_trading_days,
        readonly=True,
    )
    remaining_attempted = [
        item
        for item in after_download.candidates
        if item.symbol in before.candidate_symbols
    ]

    with sqlite3.connect(args.db) as conn:
        accepted_count = upsert_gap_exceptions(
            conn,
            items=remaining_attempted,
            collector_results=collector_results,
            run_id=run_id,
            collector_returncode=int(completed.returncode),
        )
        elapsed = time.time() - started
        log_data_source_run(
            conn,
            run_id=run_id,
            trade_date=target,
            source=SOURCE_NAME,
            api_name="stock_daily_gap_repair",
            market="ALL",
            status="PASS" if completed.returncode == 0 else "WARNING",
            success_count=len(before.candidate_symbols) - int(completed.returncode != 0),
            fail_count=int(completed.returncode != 0),
            error_message=None if completed.returncode == 0 else f"collector_returncode={completed.returncode}",
            end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            duration=elapsed,
            detail_json=json.dumps(
                {
                    "before": before.to_dict(sample_limit=10),
                    "after_download": after_download.to_dict(sample_limit=10),
                    "accepted_exception_count": accepted_count,
                    "collector_returncode": completed.returncode,
                    "symbols_file": str(symbols_path),
                    "result_json": str(result_json),
                },
                ensure_ascii=False,
            ),
        )
        conn.commit()

    after_exceptions = scan_stock_daily_gaps(
        args.db,
        end_date=target,
        window_trading_days=args.window_trading_days,
        readonly=True,
    )
    print("stock_daily_gap_scan_after=" + json.dumps(after_exceptions.to_dict(), ensure_ascii=False))
    print(f"stock_daily_gap_repair=done accepted_exceptions={accepted_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
