#!/usr/bin/env python3
"""Targeted stock daily quality repair for one completed trade date.

The repair path stays TDX-only for formal K-line rows.  The only local
derivation here is turnover_rate, and derived values are explicitly tagged.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from data_core.quality import generate_stock_daily_quality_report, report_allows_rps
from data_core.schema import ensure_data_core_schema, log_data_source_run
from data_core.symbols import P0_MARKETS, market_where_sql


DB_PATH = Path(settings.LOCAL_DB_PATH)
LOCAL_REPAIR_SOURCE = "local_quality_repair"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair stock_daily_prices quality for one date")
    parser.add_argument("--date", required=True, help="target trade date, e.g. 2026-05-29")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-redownload", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pool-size", type=int, default=2)
    parser.add_argument("--submit-window", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--max-bars", type=int, default=240)
    parser.add_argument("--lookback-days", type=int, default=10)
    return parser


def _stock_symbol_column(conn: sqlite3.Connection) -> str:
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(stock_info)").fetchall()
    }
    return "symbol" if "symbol" in columns else "code"


def missing_symbols(conn: sqlite3.Connection, trade_date: str) -> set[str]:
    symbol_column = _stock_symbol_column(conn)
    symbols: set[str] = set()
    for market in P0_MARKETS:
        rows = conn.execute(
            f"""
            SELECT s.{symbol_column}
            FROM stock_info s
            LEFT JOIN stock_daily_prices k
              ON k.symbol = s.{symbol_column}
             AND DATE(k.trade_date) = DATE(?)
            WHERE {market_where_sql(market, f's.{symbol_column}')}
              AND k.symbol IS NULL
            """,
            (trade_date,),
        ).fetchall()
        symbols.update(str(row[0]).zfill(6) for row in rows)
    return symbols


def abnormal_symbols(conn: sqlite3.Connection, trade_date: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT symbol
        FROM stock_daily_prices
        WHERE DATE(trade_date) = DATE(?)
          AND (
            high < low
            OR close < low
            OR close > high
            OR open IS NULL
            OR open <= 0
            OR high IS NULL
            OR high <= 0
            OR low IS NULL
            OR low <= 0
            OR close IS NULL
            OR close <= 0
            OR volume IS NULL
            OR volume < 0
            OR amount IS NULL
            OR amount < 0
            OR (
                turnover_rate IS NULL
                AND NOT (
                    market = 'BSE'
                    AND source IN ('tdx_standard_bj', 'tdx_exhq')
                    AND adjustment_mode = 'none'
                )
            )
          )
        """,
        (trade_date,),
    ).fetchall()
    return {str(row[0]).zfill(6) for row in rows}


def write_symbols_file(symbols: list[str]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="evoalpha_repair_symbols_",
        suffix=".txt",
        delete=False,
    )
    with handle:
        handle.write("\n".join(symbols))
        handle.write("\n")
    return Path(handle.name)


def run_targeted_redownload(symbols: list[str], args: argparse.Namespace) -> int:
    if not symbols or args.skip_redownload:
        return 0

    symbols_file = write_symbols_file(symbols)
    command = [
        sys.executable,
        "scripts/standalone/collect_stock_kline_mootdx_qfq.py",
        "--symbols-file",
        str(symbols_file),
        "--end",
        args.date,
        "--workers",
        str(args.workers),
        "--pool-size",
        str(args.pool_size),
        "--submit-window",
        str(args.submit_window),
        "--batch-size",
        str(args.batch_size),
        "--max-bars",
        str(args.max_bars),
        "--lookback-days",
        str(args.lookback_days),
        "--max-fail-rate",
        "1.0",
        "--run-id",
        f"repair_kline_{args.date}_{datetime.now():%Y%m%d_%H%M%S}",
    ]
    print("$ " + " ".join(command), flush=True)
    if args.dry_run:
        return 0
    try:
        completed = subprocess.run(command, cwd=BACKEND_DIR, check=False)
        return int(completed.returncode)
    finally:
        symbols_file.unlink(missing_ok=True)


def repair_turnover_from_previous_valuation(conn: sqlite3.Connection, trade_date: str) -> int:
    rows = conn.execute(
        """
        SELECT k.symbol, k.trade_date, k.volume, pv.price, pv.circ_mv
        FROM stock_daily_prices k
        JOIN (
            SELECT v.code, v.price, v.circ_mv
            FROM stock_valuation_daily v
            JOIN (
                SELECT code, MAX(DATE(trade_date)) AS trade_date
                FROM stock_valuation_daily
                WHERE DATE(trade_date) < DATE(?)
                  AND COALESCE(price, 0) > 0
                  AND COALESCE(circ_mv, 0) > 0
                GROUP BY code
            ) latest
              ON latest.code = v.code
             AND DATE(latest.trade_date) = DATE(v.trade_date)
        ) pv ON pv.code = k.symbol
        WHERE DATE(k.trade_date) BETWEEN DATE(?, '-30 day') AND DATE(?)
          AND k.market != 'BSE'
          AND k.turnover_rate IS NULL
          AND COALESCE(k.volume, 0) > 0
        """,
        (trade_date, trade_date, trade_date),
    ).fetchall()

    updates: list[tuple[float, str, str, str, str, str]] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for symbol, row_trade_date, volume, price, circ_mv in rows:
        float_shares = float(circ_mv) / float(price) if price and circ_mv else 0.0
        if float_shares <= 0:
            continue
        turnover_rate = float(volume) / float_shares * 100.0
        if turnover_rate <= 0 or turnover_rate > 100:
            continue
        updates.append(
            (
                turnover_rate,
                "repaired_turnover_local",
                f"repair_turnover_{trade_date}_{datetime.now():%Y%m%d_%H%M%S}",
                now,
                str(row_trade_date)[:10],
                str(symbol).zfill(6),
            )
        )

    if not updates:
        return 0

    conn.executemany(
        """
        UPDATE stock_daily_prices
        SET turnover_rate = ?,
            quality_flag = ?,
            run_id = ?,
            updated_at = ?
        WHERE DATE(trade_date) = DATE(?)
          AND symbol = ?
        """,
        [
            (turnover, flag, run_id, updated_at, row_trade_date, symbol)
            for turnover, flag, run_id, updated_at, row_trade_date, symbol in updates
        ],
    )
    return len(updates)


def main() -> int:
    args = build_parser().parse_args()
    ensure_data_core_schema(DB_PATH)

    with sqlite3.connect(DB_PATH) as conn:
        missing = missing_symbols(conn, args.date)
        abnormal = abnormal_symbols(conn, args.date)
        repair_symbols = sorted(missing | abnormal)
        print(
            "repair_plan="
            + json.dumps(
                {
                    "date": args.date,
                    "missing_count": len(missing),
                    "abnormal_count": len(abnormal),
                    "target_symbol_count": len(repair_symbols),
                    "symbols_sample": repair_symbols[:30],
                },
                ensure_ascii=False,
            )
        )

    redownload_rc = run_targeted_redownload(repair_symbols, args)
    if redownload_rc != 0:
        print(f"targeted_redownload_returncode={redownload_rc}")

    with sqlite3.connect(DB_PATH) as conn:
        if args.dry_run:
            repaired_turnover = 0
        else:
            repaired_turnover = repair_turnover_from_previous_valuation(conn, args.date)
            log_data_source_run(
                conn,
                run_id=f"repair_stock_daily_{args.date}_{datetime.now():%Y%m%d_%H%M%S}",
                trade_date=args.date,
                source=LOCAL_REPAIR_SOURCE,
                api_name="stock_daily_turnover_repair",
                market="SH_SZ",
                status="PASS",
                success_count=repaired_turnover,
                fail_count=0,
                error_message=None,
                end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                detail_json=json.dumps(
                    {
                        "method": "turnover_rate = volume / (previous circ_mv / previous price) * 100",
                        "quality_flag": "repaired_turnover_local",
                    },
                    ensure_ascii=False,
                ),
            )
            conn.commit()

    print(f"repaired_turnover_rows={repaired_turnover}")
    if args.dry_run:
        return 0

    reports = generate_stock_daily_quality_report(
        DB_PATH,
        trade_date=args.date,
        run_id=f"repair_quality_{datetime.now():%Y%m%d_%H%M%S}",
        persist=True,
    )
    for report in reports:
        print(
            f"post_repair_quality {report['market']}={report['status']} "
            f"missing={report['missing_count']} abnormal={report['abnormal_count']}"
        )
    return 0 if report_allows_rps(reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
