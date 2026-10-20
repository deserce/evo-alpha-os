#!/usr/bin/env python3
"""
Daily EvoAlpha OS data workflow.

This is the stable entrypoint for the 16:00 automation.  It runs the fast
mootdx qfq K-line updater first, then refreshes market side tables and rebuilds
the core stock pool from the latest available inputs.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import selectors
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from data_core.quality import (
    BSE_UNADJUSTED_SOURCES,
    MODULE_SECTOR_COMPOSITE,
    MODULE_SECTOR_RPS,
    MODULE_SECTOR_VENDOR,
    SECTOR_COMPOSITE_TABLE,
    SECTOR_RPS_TABLES,
    SECTOR_VENDOR_TABLE,
    accepted_gap_exception_count,
    actual_stock_count,
    expected_stock_count,
    format_report_line,
    generate_stock_daily_quality_report,
    latest_quality_reports,
    latest_source_failures,
)
from data_core.schema import ensure_data_core_schema, table_columns
from data_core.market_calendar import is_a_share_session
from data_core.symbols import P0_MARKETS
from data_core.source_registry import (
    MODULE_STOCK_DAILY,
    MODULE_STOCK_VALUATION,
    all_sources,
    official_sources_for,
)
from quant_engine.utils.calculation_dates import table_has_complete_date
from data_job.common.network_utils import sanitize_direct_env

DB_PATH = BACKEND_DIR / "data" / "local_quant.db"
LOCK_PATH = BACKEND_DIR / "data" / ".daily_data_workflow.lock"
LOG_DIR = BACKEND_DIR / "logs"
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
RECENT_QUALITY_WINDOW_DAYS = 45
MODEL_ENV_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GLM_API_KEY",
    "BIGMODEL_API_KEY",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "QWEN_API_KEY",
)
BSE_UNADJUSTED_SOURCE_SQL = "(" + ", ".join(f"'{source}'" for source in BSE_UNADJUSTED_SOURCES) + ")"


@dataclass(frozen=True)
class Task:
    name: str
    command: tuple[str, ...]
    critical: bool = False
    skip_reason: str | None = None
    timeout_seconds: int | None = None
    independent: bool = False
    run_if_failed: str | None = None


@dataclass(frozen=True)
class TaskResult:
    name: str
    returncode: int
    elapsed_seconds: float
    critical: bool
    skipped: bool = False
    deferred: bool = False
    deferred_reason: str | None = None

    @property
    def ok(self) -> bool:
        return not self.skipped and self.returncode == 0

    @property
    def failed(self) -> bool:
        return not self.skipped and self.returncode != 0

    @property
    def status_label(self) -> str:
        if self.deferred:
            return "DEFERRED"
        if self.skipped:
            return "SKIPPED"
        return "OK" if self.ok else "FAILED"


@dataclass(frozen=True)
class QualityCheck:
    name: str
    status: str
    message: str
    critical: bool = False

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


TABLE_SPECS = (
    ("trading_calendar", "trade_date"),
    ("stock_daily_prices", "trade_date"),
    ("stock_valuation_daily", "trade_date"),
    ("index_daily", "trade_date"),
    ("sector_daily_prices", "trade_date"),
    (SECTOR_COMPOSITE_TABLE, "trade_date"),
    (SECTOR_VENDOR_TABLE, "trade_date"),
    ("etf_daily_prices", "trade_date"),
    ("limit_board_trading", "trade_date"),
    ("limit_down_trading", "trade_date"),
    ("limit_break_trading", "trade_date"),
    ("yesterday_limit_trading", "trade_date"),
    ("consecutive_boards_stats", "trade_date"),
    ("news_articles", "publish_time"),
    ("macro_indicators", "publish_date"),
    ("stock_northbound_holdings", "hold_date"),
    ("northbound_flow_daily", "trade_date"),
    ("finance_fund_holdings", "report_date"),
    ("stock_finance_summary", "report_date"),
    ("stock_finance_snapshot_tdx", "report_date"),
    ("stock_f10_sections", "fetched_at"),
    ("stock_financial_reports", "report_date"),
    ("stock_research_reports", "publish_date"),
    ("industry_research_reports", "publish_date"),
    ("stock_announcements_cninfo", "publish_date"),
    ("stock_dragon_tiger_records", "trade_date"),
    ("stock_dragon_tiger_seats", "trade_date"),
    ("stock_lockup_expiry", "free_date"),
    ("stock_margin_trading", "trade_date"),
    ("stock_block_trades", "trade_date"),
    ("stock_holder_num", "report_date"),
    ("stock_dividend_history", "ex_dividend_date"),
    ("stock_fund_flow_daily", "trade_date"),
    ("stock_concept_blocks", "fetched_at"),
    ("market_hot_stocks", "rank_date"),
    ("market_hot_concepts", "rank_date"),
    ("quant_stock_pool", "add_date"),
    ("quant_feature_rps", "trade_date"),
    ("quant_feature_sector_rps", "trade_date"),
    ("quant_feature_sector_rps_v2", "trade_date"),
    ("quant_feature_etf_rps", "trade_date"),
    ("market_strength_daily", "trade_date"),
    ("industry_strength_daily", "trade_date"),
    ("structure_health_daily", "trade_date"),
    ("timing_signals_daily", "trade_date"),
    ("trade_plans", "trade_date"),
    ("stock_lifecycle_state", "trade_date"),
    ("research_stock_daily_snapshot", "trade_date"),
    ("watch_daily_snapshot", "trade_date"),
    ("watch_events", "trade_date"),
    ("pipeline_run_log", "started_at"),
    ("data_source_log", "created_at"),
    ("data_quality_report", "created_at"),
    ("stock_daily_gap_exception", "last_seen_at"),
)

DUPLICATE_CHECKS = (
    ("stock_daily_prices", ("symbol", "trade_date"), True),
    ("stock_valuation_daily", ("code", "trade_date"), True),
    ("limit_break_trading", ("symbol", "trade_date"), False),
    ("yesterday_limit_trading", ("symbol", "trade_date"), False),
    ("stock_finance_snapshot_tdx", ("symbol", "report_date", "source"), False),
    ("stock_f10_sections", ("symbol", "category", "source"), False),
    ("stock_financial_reports", ("symbol", "report_type", "report_date", "source"), False),
    ("stock_announcements_cninfo", ("symbol", "announcement_id"), False),
    ("stock_fund_flow_daily", ("symbol", "trade_date"), False),
    ("stock_northbound_holdings", ("symbol", "hold_date"), False),
    ("quant_stock_pool", ("pool_name", "symbol", "add_date"), True),
    ("quant_feature_rps", ("symbol", "trade_date"), True),
    ("quant_feature_sector_rps", ("sector_name", "trade_date"), True),
    ("quant_feature_sector_rps_v2", ("sector_name", "trade_date"), True),
    ("quant_feature_etf_rps", ("symbol", "trade_date"), True),
    ("market_strength_daily", ("index_code", "trade_date"), True),
    ("industry_strength_daily", ("sector_name", "trade_date"), True),
    ("structure_health_daily", ("symbol", "trade_date"), True),
    ("timing_signals_daily", ("symbol", "trade_date", "signal_type"), True),
    ("trade_plans", ("signal_id",), True),
    ("stock_lifecycle_state", ("symbol", "trade_date"), True),
    ("research_stock_daily_snapshot", ("theme_name", "symbol", "trade_date"), False),
    ("watch_daily_snapshot", ("symbol", "trade_date"), True),
    ("watch_events", ("dedupe_key",), True),
)

FRESHNESS_CHECKS = (
    ("index_daily", "index_daily", "trade_date", False),
    ("etf_daily_prices", "etf_daily_prices", "trade_date", False),
    ("rps_features", "quant_feature_rps", "trade_date", True),
    ("sector_rps_v2", "quant_feature_sector_rps_v2", "trade_date", True),
    ("etf_rps_features", "quant_feature_etf_rps", "trade_date", False),
    ("strength_features", "market_strength_daily", "trade_date", True),
    ("strength_features", "industry_strength_daily", "trade_date", True),
    ("structure_health", "structure_health_daily", "trade_date", True),
    ("lifecycle_state", "stock_lifecycle_state", "trade_date", True),
    ("timing_signals", "timing_signals_daily", "trade_date", False),
    ("risk_plans", "trade_plans", "trade_date", False),
    ("research_snapshot", "research_stock_daily_snapshot", "trade_date", False),
    ("watch_engine", "watch_daily_snapshot", "trade_date", False),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run EvoAlpha OS daily data workflow")
    parser.add_argument("--kline-workers", type=int, default=4, help="mootdx K-line workers")
    parser.add_argument("--kline-pool-size", type=int, default=4, help="mootdx adapter pool size")
    parser.add_argument("--kline-batch-size", type=int, default=80, help="K-line save batch size")
    parser.add_argument("--kline-lookback-days", type=int, default=7, help="incremental lookback days")
    parser.add_argument("--kline-max-bars", type=int, default=40, help="recent bars fetched for incremental K-line updates")
    parser.add_argument("--kline-submit-window", type=int, default=64, help="max pending K-line futures")
    parser.add_argument("--kline-market", choices=["ALL", "SH", "SZ", "BSE"], default="ALL", help="optional K-line market filter")
    parser.add_argument("--kline-timeout", type=int, default=10, help="mootdx adapter timeout seconds")
    parser.add_argument("--kline-connect-retries", type=int, default=3, help="mootdx adapter startup retries")
    parser.add_argument(
        "--kline-max-fail-rate",
        type=float,
        default=1.0,
        help="let Data Core quality gates decide market coverage failures",
    )
    parser.add_argument("--kline-end", help="optional K-line end date, e.g. 2026-04-27")
    parser.add_argument("--morning", action="store_true", help="run bounded previous-day gap repair")
    parser.add_argument("--retry", action="store_true", help="run only target-date stale tasks")
    parser.add_argument(
        "--pending-days",
        type=int,
        default=7,
        help="recent trading dates inspected in morning mode",
    )
    parser.add_argument(
        "--time-budget-minutes",
        type=int,
        default=25,
        help="morning task-boundary runtime budget",
    )
    parser.add_argument("--skip-kline", action="store_true", help="skip stock K-line collection")
    parser.add_argument("--skip-kline-repair", action="store_true", help="skip targeted stock daily quality repair")
    parser.add_argument("--skip-gap-repair", action="store_true", help="skip recent rolling stock daily gap repair")
    parser.add_argument("--gap-window-trading-days", type=int, default=45, help="recent trading-day window for gap repair")
    parser.add_argument("--gap-max-bars", type=int, default=120, help="recent bars fetched for rolling gap repair")
    parser.add_argument(
        "--force-kline",
        action="store_true",
        help="run K-line download even if local stock_daily_prices is already fresh",
    )
    parser.add_argument(
        "--force-valuation",
        action="store_true",
        help="run valuation collector even if local stock_valuation_daily is already fresh",
    )
    parser.add_argument("--skip-market-side", action="store_true", help="skip index/sector/ETF/news/limit/macro")
    parser.add_argument(
        "--skip-sector-kline-repair",
        action="store_true",
        help="skip sector K-line continuity scan and auto repair",
    )
    parser.add_argument(
        "--skip-sector-composite",
        action="store_true",
        help="skip local equal-weight sector net/OHLC build and quality gate",
    )
    parser.add_argument(
        "--skip-sector-vendor",
        action="store_true",
        help="skip best-effort Eastmoney BK vendor reference sync",
    )
    parser.add_argument("--include-quarterly", action="store_true", help="also run finance/fund holdings collectors")
    parser.add_argument(
        "--include-rps",
        action="store_true",
        help="deprecated: RPS now runs by default unless --skip-rps is set",
    )
    parser.add_argument(
        "--include-strength",
        action="store_true",
        help="deprecated: P0 strength now runs by default unless --skip-p0 is set",
    )
    parser.add_argument("--skip-rps", action="store_true", help="skip stock/sector/ETF RPS calculators")
    parser.add_argument("--skip-p0", action="store_true", help="skip strength/structure/lifecycle calculators")
    parser.add_argument("--skip-p1", action="store_true", help="skip timing/risk/research calculators")
    parser.add_argument("--skip-watch", action="store_true", help="skip Watch Engine and alert dispatch")
    parser.add_argument(
        "--skip-notifications", action="store_true",
        help="keep calculations but do not create or send notification deliveries",
    )
    parser.add_argument(
        "--skip-portfolio",
        action="store_true",
        help="skip daily Portfolio performance snapshots",
    )
    parser.add_argument("--include-strategy", action="store_true", help="also run MRGC preselect strategy before lifecycle")
    parser.add_argument("--northbound-limit", type=int, default=0, help="optional northbound symbol chunk size")
    parser.add_argument("--skip-enrichment", action="store_true", help="skip core stock enrichment caches")
    parser.add_argument("--enrichment-limit", type=int, default=250, help="max core symbols for enrichment")
    parser.add_argument("--check-only", action="store_true", help="print current database status and task plan")
    parser.add_argument(
        "--allow-model-calls",
        action="store_true",
        help="allow downstream model API env vars; default daily workflow is local-code-only",
    )
    return parser


def resolve_workflow_mode(args: argparse.Namespace) -> str:
    if args.check_only:
        return "check-only"
    if args.morning:
        return "morning"
    if args.retry:
        return "retry"
    return "daily"


def log_line(log_file, message: str = "") -> None:
    print(message, flush=True)
    log_file.write(message + "\n")
    log_file.flush()


def write_lock_metadata(lock_file, *, run_id: str, mode: str, target_trade_date: str, stage: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metadata = {
        "pid": os.getpid(),
        "run_id": run_id,
        "mode": mode,
        "target_trade_date": target_trade_date,
        "stage": stage,
        "stage_started_at": now,
        "heartbeat_at": now,
    }
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(json.dumps(metadata, ensure_ascii=False))
    lock_file.flush()


def build_task_env(allow_model_calls: bool = False) -> dict[str, str]:
    """Build a deterministic subprocess env for local daily jobs."""
    env = sanitize_direct_env(dict(os.environ))
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(BACKEND_DIR) if not pythonpath else f"{BACKEND_DIR}:{pythonpath}"
    env["APP_DEBUG"] = "false"

    if allow_model_calls:
        env["EVOALPHA_LOCAL_ONLY"] = "0"
        env.pop("EVOALPHA_DISABLE_LLM", None)
        return env

    env["EVOALPHA_LOCAL_ONLY"] = "1"
    env["EVOALPHA_DISABLE_LLM"] = "1"
    for key in MODEL_ENV_KEYS:
        env.pop(key, None)
    return env


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def snapshot_tables(target_trade_date: str | None = None) -> dict[str, dict[str, object]]:
    if not DB_PATH.exists():
        return {}

    snapshot: dict[str, dict[str, object]] = {}
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        for table_name, date_column in TABLE_SPECS:
            if not table_exists(conn, table_name):
                continue

            if target_trade_date:
                count = conn.execute(
                    f"""
                    SELECT COUNT(*) FROM {table_name}
                    WHERE {date_column} >= ?
                      AND {date_column} < DATE(?, '+1 day')
                    """,
                    (target_trade_date, target_trade_date),
                ).fetchone()[0]
                min_date = target_trade_date if count else None
                max_date = latest_table_date(conn, table_name, date_column)
            else:
                count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                min_date, max_date = conn.execute(
                    f"SELECT MIN(DATE({date_column})), MAX(DATE({date_column})) FROM {table_name}"
                ).fetchone()
            snapshot[table_name] = {
                "count": count,
                "min_date": min_date,
                "max_date": max_date,
            }

    return snapshot


def print_snapshot(log_file, title: str, snapshot: dict[str, dict[str, object]]) -> None:
    log_line(log_file, f"\n[{title}]")
    if not snapshot:
        log_line(log_file, "  数据库不存在或暂无可检查表。")
        return

    for table_name, info in snapshot.items():
        log_line(
            log_file,
            "  "
            f"{table_name}: rows={info['count']}, "
            f"range={info['min_date']}..{info['max_date']}",
        )


def count_duplicate_keys(
    conn: sqlite3.Connection,
    table_name: str,
    columns: Sequence[str],
    *,
    target_trade_date: str | None = None,
    date_column: str | None = None,
) -> int:
    col_expr = ", ".join(columns)
    where_clause = ""
    parameters: tuple[object, ...] = ()
    if target_trade_date and date_column:
        where_clause = (
            f"WHERE {date_column} >= ? "
            f"AND {date_column} < DATE(?, '+1 day')"
        )
        parameters = (target_trade_date, target_trade_date)
    return conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT 1
            FROM {table_name}
            {where_clause}
            GROUP BY {col_expr}
            HAVING COUNT(*) > 1
        )
        """,
        parameters,
    ).fetchone()[0]


def print_duplicate_checks(log_file, target_trade_date: str | None = None) -> None:
    if not DB_PATH.exists():
        return

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        log_line(log_file, "\n[Duplicate Check]")
        for table_name, columns, _critical in DUPLICATE_CHECKS:
            if not table_exists(conn, table_name):
                continue

            date_column = next(
                (column for column in columns if column.endswith("_date")),
                None,
            )
            if target_trade_date and date_column is None:
                continue
            duplicate_count = count_duplicate_keys(
                conn,
                table_name,
                columns,
                target_trade_date=target_trade_date,
                date_column=date_column,
            )
            log_line(log_file, f"  {table_name}: duplicate_keys={duplicate_count}")


def latest_table_date(
    conn: sqlite3.Connection, table_name: str, date_column: str,
    *, on_or_before: str | None = None,
) -> str | None:
    if not table_exists(conn, table_name):
        return None
    query = f"SELECT MAX({date_column}) FROM {table_name}"
    if on_or_before is not None:
        row = conn.execute(
            query + f" WHERE {date_column} < DATE(?, '+1 day')", (on_or_before,)
        ).fetchone()
    else:
        row = conn.execute(query).fetchone()
    return str(row[0])[:10] if row and row[0] else None


def support_target_expected_count(conn: sqlite3.Connection, table_name: str) -> int | None:
    if table_name == "index_daily":
        return 8
    if table_name in {"etf_daily_prices", "quant_feature_etf_rps"} and table_exists(conn, "etf_info"):
        return conn.execute("SELECT COUNT(DISTINCT symbol) FROM etf_info").fetchone()[0]
    return None


def accepted_gap_exception_rows(
    conn: sqlite3.Connection,
    target_trade_date: str | None,
) -> list[dict[str, object]]:
    if not target_trade_date or not table_exists(conn, "stock_daily_gap_exception"):
        return []
    rows = conn.execute(
        """
        SELECT market, reason, source, status, COUNT(*) AS count, GROUP_CONCAT(symbol) AS symbols
        FROM stock_daily_gap_exception
        WHERE DATE(trade_date) = DATE(?)
          AND status = 'accepted_no_raw_data'
        GROUP BY market, reason, source, status
        ORDER BY count DESC, market
        """,
        (target_trade_date,),
    ).fetchall()
    return [
        {
            "market": row[0],
            "reason": row[1],
            "source": row[2],
            "status": row[3],
            "count": row[4],
            "symbols": [symbol for symbol in str(row[5] or "").split(",") if symbol],
        }
        for row in rows
    ]


def _accepted_gap_symbols(rows: Sequence[dict[str, object]]) -> set[str]:
    symbols: set[str] = set()
    for row in rows:
        for symbol in row.get("symbols") or []:
            symbols.add(str(symbol).zfill(6))
    return symbols


def is_accepted_gap_source_failure(
    failure: dict[str, object],
    accepted_symbols: set[str],
) -> bool:
    if not accepted_symbols:
        return False
    if failure.get("source") != "tdx_mootdx_qfq":
        return False
    if failure.get("api_name") != "stock_daily_qfq":
        return False
    error = str(failure.get("error_message") or "")
    return any(error.startswith(f"{symbol}:") for symbol in accepted_symbols)


def latest_completed_trading_date(now: datetime | None = None) -> str:
    """Resolve the latest completed A-share session without network calls."""
    current = now or datetime.now()
    candidate = current.date()
    before_close = (current.hour, current.minute) < (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)
    if before_close:
        candidate -= timedelta(days=1)
    while not is_a_share_session(candidate):
        candidate -= timedelta(days=1)

    return candidate.isoformat()


def stock_turnover_quality_stats(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
) -> tuple[int, int, int, int, int]:
    """Return turnover defects while exempting BSE raw rows and suspended stocks."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(stock_daily_prices)").fetchall()
    }
    has_bse_exemption = {"market", "source", "adjustment_mode"}.issubset(columns)
    has_suspension_fields = {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    }.issubset(columns)
    bse_predicate = (
        f"(market = 'BSE' "
        f"AND source IN {BSE_UNADJUSTED_SOURCE_SQL} "
        f"AND adjustment_mode = 'none')"
        if has_bse_exemption
        else "0"
    )
    suspended_predicate = (
        "("
        "turnover_rate = 0 "
        "AND COALESCE(volume, 0) = 0 "
        "AND COALESCE(amount, 0) = 0 "
        "AND open IS NOT NULL "
        "AND open = high AND high = low AND low = close"
        ")"
        if has_suspension_fields
        else "0"
    )
    row_count, null_turnover, zero_turnover, suspended_exempt = conn.execute(
        f"""
        SELECT
            COUNT(*),
            COALESCE(SUM(turnover_rate IS NULL), 0),
            COALESCE(SUM(turnover_rate = 0 AND NOT {suspended_predicate}), 0),
            COALESCE(SUM({suspended_predicate}), 0)
        FROM stock_daily_prices
        WHERE trade_date >= ?
          AND trade_date <= ?
          AND NOT {bse_predicate}
        """,
        (start_date, end_date),
    ).fetchone()
    bse_exempt = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM stock_daily_prices
        WHERE trade_date >= ?
          AND trade_date <= ?
          AND {bse_predicate}
          AND (turnover_rate IS NULL OR turnover_rate = 0)
        """,
        (start_date, end_date),
    ).fetchone()[0]
    return (
        int(row_count or 0),
        int(null_turnover or 0),
        int(zero_turnover or 0),
        int(bse_exempt or 0),
        int(suspended_exempt or 0),
    )


def stock_kline_freshness(target_date: str | None = None) -> tuple[bool, str]:
    """Return whether local K-line data is fresh enough to skip network download."""
    expected_date = target_date or latest_completed_trading_date()
    if not DB_PATH.exists():
        return False, f"database_missing target={expected_date}"

    coverage_details: list[str] = []
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as conn:
        if not table_exists(conn, "stock_daily_prices"):
            return False, f"stock_daily_prices_missing target={expected_date}"

        latest_date = latest_table_date(conn, "stock_daily_prices", "trade_date")
        if not latest_date:
            return False, f"stock_daily_prices_empty target={expected_date}"

        if latest_date < expected_date:
            return False, f"latest={latest_date}, target={expected_date}"

        if table_exists(conn, "stock_info"):
            incomplete = False
            for market in P0_MARKETS:
                expected = expected_stock_count(conn, market, expected_date)
                actual = actual_stock_count(conn, expected_date, market)
                exceptions = accepted_gap_exception_count(conn, expected_date, market)
                coverage_details.append(f"{market}={actual}/{expected}")
                effective_actual = min(expected, actual + exceptions)
                # Require the same completeness as a PASS quality report before skipping.
                if expected > 0 and effective_actual / expected < 0.99:
                    incomplete = True
            if incomplete:
                return False, (
                    f"latest={latest_date}, target={expected_date}, "
                    f"coverage_incomplete={','.join(coverage_details)}"
                )

        (
            row_count,
            null_turnover,
            zero_turnover,
            bse_turnover_exempt,
            suspended_turnover_exempt,
        ) = stock_turnover_quality_stats(conn, expected_date, expected_date)

    if row_count == 0 or null_turnover > 0 or zero_turnover > 0:
        return (
            False,
            (
                f"latest={latest_date}, target={expected_date}, rows={row_count}, "
                f"null_turnover={null_turnover}, zero_turnover={zero_turnover}, "
                f"bse_unadjusted_exempt={bse_turnover_exempt}, "
                f"suspended_turnover_exempt={suspended_turnover_exempt}"
            ),
        )

    return (
        True,
        (
            f"latest={latest_date}, target={expected_date}, rows={row_count}, "
            f"turnover_ok=true, bse_unadjusted_exempt={bse_turnover_exempt}, "
            f"suspended_turnover_exempt={suspended_turnover_exempt}, "
            f"coverage={','.join(coverage_details) or 'universe_unavailable'}"
        ),
    )


def recent_source_trading_dates(target_date: str, limit: int = 7) -> list[str]:
    """Return recent source dates plus the expected target not downloaded yet."""
    if not DB_PATH.exists():
        return [target_date]
    try:
        with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as conn, conn:
            if not table_exists(conn, "stock_daily_prices"):
                return [target_date]
            rows = conn.execute(
                """
                SELECT trade_date
                FROM (
                    SELECT DISTINCT trade_date
                    FROM stock_daily_prices
                    WHERE trade_date <= ?
                    ORDER BY trade_date DESC
                    LIMIT ?
                )
                ORDER BY trade_date
                """,
                (target_date, max(1, int(limit))),
            ).fetchall()
        dates = [str(row[0])[:10] for row in rows if row[0]]
        dates.append(target_date)
        return sorted(set(dates))[-max(1, int(limit)):]
    except sqlite3.Error:
        return [target_date]


def _table_target_count(
    conn: sqlite3.Connection,
    table_name: str,
    date_column: str,
    target_date: str,
) -> int:
    if not table_exists(conn, table_name):
        return 0
    return int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM {table_name}
            WHERE {date_column} >= ?
              AND {date_column} < DATE(?, '+1 day')
            """,
            (target_date, target_date),
        ).fetchone()[0]
        or 0
    )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _watch_engine_target_count(conn: sqlite3.Connection, target_date: str) -> tuple[int, int]:
    snapshot_count = _table_target_count(
        conn,
        "watch_daily_snapshot",
        "trade_date",
        target_date,
    )
    if not table_exists(conn, "stock_pools") or not table_exists(conn, "stock_pool_members"):
        return snapshot_count, snapshot_count

    pool_columns = _table_columns(conn, "stock_pools")
    member_columns = _table_columns(conn, "stock_pool_members")
    if "symbol" not in member_columns or "pool_id" not in member_columns:
        return snapshot_count, snapshot_count
    if "monitor_enabled" in pool_columns:
        enabled_clause = "COALESCE(p.monitor_enabled, 0) = 1"
    elif "is_default" in pool_columns:
        enabled_clause = "COALESCE(p.is_default, 0) = 1"
    else:
        return snapshot_count, snapshot_count

    expected_count = int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT UPPER(TRIM(m.symbol)))
            FROM stock_pool_members m
            JOIN stock_pools p ON p.pool_id = m.pool_id
            WHERE {enabled_clause}
              AND m.symbol IS NOT NULL
              AND TRIM(m.symbol) <> ''
            """
        ).fetchone()[0]
        or 0
    )
    if expected_count == 0:
        return 0, 0
    snapshot_columns = _table_columns(conn, "watch_daily_snapshot")
    if not {"symbol", "trade_date"} <= snapshot_columns:
        return expected_count, 0
    actual_count = int(
        conn.execute(
            f"""
            WITH monitored AS (
                SELECT DISTINCT UPPER(TRIM(m.symbol)) AS symbol
                FROM stock_pool_members m
                JOIN stock_pools p ON p.pool_id = m.pool_id
                WHERE {enabled_clause}
                  AND m.symbol IS NOT NULL
                  AND TRIM(m.symbol) <> ''
            )
            SELECT COUNT(DISTINCT UPPER(TRIM(s.symbol)))
            FROM watch_daily_snapshot s
            JOIN monitored m ON m.symbol = UPPER(TRIM(s.symbol))
            WHERE s.trade_date >= ?
              AND s.trade_date < DATE(?, '+1 day')
            """,
            (target_date, target_date),
        ).fetchone()[0]
        or 0
    )
    return expected_count, actual_count


def _lifecycle_target_count(conn: sqlite3.Connection, target_date: str) -> tuple[int, int]:
    """核对核心池与全部手工分组的并集是否都有生命周期行。"""
    actual_total = _table_target_count(
        conn,
        "stock_lifecycle_state",
        "trade_date",
        target_date,
    )
    universe_symbols: set[str] = set()
    has_universe_table = False

    if table_exists(conn, "quant_stock_pool"):
        columns = _table_columns(conn, "quant_stock_pool")
        if "symbol" in columns:
            has_universe_table = True
            clauses = ["symbol IS NOT NULL", "TRIM(symbol) <> ''"]
            if "pool_name" in columns:
                clauses.append("pool_name='core_pool'")
            if "is_active" in columns:
                clauses.append("COALESCE(is_active, 1)=1")
            universe_symbols.update(
                str(row[0]).strip().upper()
                for row in conn.execute(
                    "SELECT DISTINCT symbol FROM quant_stock_pool WHERE "
                    + " AND ".join(clauses)
                ).fetchall()
                if row[0]
            )

    if table_exists(conn, "stock_pool_members"):
        columns = _table_columns(conn, "stock_pool_members")
        if "symbol" in columns:
            has_universe_table = True
            universe_symbols.update(
                str(row[0]).strip().upper()
                for row in conn.execute(
                    "SELECT DISTINCT symbol FROM stock_pool_members "
                    "WHERE symbol IS NOT NULL AND TRIM(symbol) <> ''"
                ).fetchall()
                if row[0]
            )

    if not has_universe_table:
        return actual_total, actual_total
    if not universe_symbols:
        return 0, 0
    lifecycle_columns = _table_columns(conn, "stock_lifecycle_state")
    if not {"symbol", "trade_date"} <= lifecycle_columns:
        return len(universe_symbols), 0

    symbols = sorted(universe_symbols)
    placeholders = ",".join("?" for _ in symbols)
    actual_count = int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT UPPER(TRIM(symbol)))
            FROM stock_lifecycle_state
            WHERE trade_date >= ?
              AND trade_date < DATE(?, '+1 day')
              AND UPPER(TRIM(symbol)) IN ({placeholders})
            """,
            (target_date, target_date, *symbols),
        ).fetchone()[0]
        or 0
    )
    return len(symbols), actual_count


def _stock_quality_passed(conn: sqlite3.Connection, target_date: str) -> bool:
    if not table_exists(conn, "data_quality_report"):
        return False
    rows = conn.execute(
        """
        SELECT market, status
        FROM data_quality_report
        WHERE trade_date >= ?
          AND trade_date < DATE(?, '+1 day')
          AND module = 'stock_daily'
        ORDER BY created_at DESC
        """,
        (target_date, target_date),
    ).fetchall()
    latest: dict[str, str] = {}
    for market, status in rows:
        latest.setdefault(str(market), str(status))
    return all(latest.get(market) == "PASS" for market in ("SH", "SZ", "BSE"))


def _latest_quality_status(
    conn: sqlite3.Connection,
    *,
    module: str,
    target_date: str,
) -> str | None:
    """Return the newest status for a Data Core module/date, if persisted."""
    if not table_exists(conn, "data_quality_report"):
        return None
    row = conn.execute(
        """
        SELECT status
        FROM data_quality_report
        WHERE DATE(trade_date) = DATE(?) AND module = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (target_date, module),
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _eastmoney_vendor_circuit_open(conn: sqlite3.Connection) -> bool:
    """Detect a persisted vendor cooldown without importing the network adapter."""
    if not table_exists(conn, "data_source_log"):
        return False
    row = conn.execute(
        """
        SELECT status, detail_json
        FROM data_source_log
        WHERE source IN ('eastmoney_bk_vendor', 'eastmoney_bk', 'eastmoney')
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return False
    status = str(row[0] or "").lower()
    if status not in {"circuit_open", "blocked", "cooldown", "rate_limited"}:
        return False
    if row[1]:
        try:
            detail = json.loads(str(row[1]))
            until = detail.get("circuit_open_until") if isinstance(detail, dict) else None
            if until:
                until_dt = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
                now_dt = datetime.now(until_dt.tzinfo) if until_dt.tzinfo else datetime.now()
                return until_dt > now_dt
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return True


def _sector_table_target_complete(
    conn: sqlite3.Connection,
    table_name: str,
    target_date: str,
) -> bool:
    """Require full sector coverage when a sector universe is available."""
    if not table_exists(conn, table_name):
        return False
    expected = 0
    if table_exists(conn, "sector_list"):
        columns = table_columns(conn, "sector_list")
        if {"sector_name", "sector_type"}.issubset(columns):
            expected = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT TRIM(sector_name))
                    FROM sector_list
                    WHERE sector_type = 'Industry'
                      AND sector_name IS NOT NULL AND TRIM(sector_name) <> ''
                    """
                ).fetchone()[0]
                or 0
            )
    if expected <= 0:
        return table_has_complete_date(conn, table_name, target_date)
    columns = table_columns(conn, table_name)
    if not {"sector_name", "trade_date"}.issubset(columns):
        return False
    actual = int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT TRIM(sector_name))
            FROM {table_name}
            WHERE DATE(trade_date) = DATE(?)
              AND sector_name IS NOT NULL AND TRIM(sector_name) <> ''
            """,
            (target_date,),
        ).fetchone()[0]
        or 0
    )
    return actual >= expected


def sector_vendor_incremental_skip_reason(
    conn: sqlite3.Connection,
    target_date: str,
    *,
    max_catchup_days: int = 7,
    target_only: bool = False,
) -> str | None:
    """Prevent an unbounded vendor backfill while allowing one-day bulk bootstrap."""
    if target_only:
        return None
    if not table_exists(conn, SECTOR_VENDOR_TABLE):
        return "vendor table not initialized; run an explicit bounded vendor backfill"
    columns = table_columns(conn, SECTOR_VENDOR_TABLE)
    if not {"sector_name", "trade_date"}.issubset(columns):
        return "vendor table schema incomplete; run an explicit bounded vendor backfill"
    if table_exists(conn, "sector_list"):
        list_columns = table_columns(conn, "sector_list")
        if {"sector_name", "sector_type"}.issubset(list_columns):
            missing_sectors = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM sector_list s
                    LEFT JOIN (
                        SELECT DISTINCT sector_name FROM {SECTOR_VENDOR_TABLE}
                    ) v ON v.sector_name = s.sector_name
                    WHERE s.sector_type = 'Industry'
                      AND s.sector_name IS NOT NULL
                      AND TRIM(s.sector_name) <> ''
                      AND v.sector_name IS NULL
                    """
                ).fetchone()[0]
                or 0
            )
            if missing_sectors:
                return f"{missing_sectors} vendor sectors have no history; explicit bounded backfill required"
    latest = latest_table_date(conn, SECTOR_VENDOR_TABLE, "trade_date")
    if not latest:
        return "vendor table has no history; explicit bounded vendor backfill required"
    gap_days = int(
        conn.execute(
            "SELECT CAST(julianday(DATE(?)) - julianday(DATE(?)) AS INTEGER)",
            (target_date, latest),
        ).fetchone()[0]
        or 0
    )
    if gap_days > max_catchup_days:
        return f"vendor gap {gap_days} calendar days exceeds bounded catchup {max_catchup_days}d"
    return None


def retry_task_skip_reason(
    task_name: str,
    target_date: str,
    *,
    mode: str = "retry",
    target_dates: Sequence[str] | None = None,
) -> str | None:
    if not DB_PATH.exists():
        return None
    if mode == "morning" and task_name in {
        "mootdx_qfq_stock_kline",
        "stock_valuation",
        "rps_features",
        "etf_rps_features",
    }:
        return None
    fresh_prefix = f"{mode} target already fresh"
    if task_name == "mootdx_qfq_stock_kline":
        is_fresh, reason = stock_kline_freshness(target_date)
        return f"{fresh_prefix}: {reason}" if is_fresh else None
    if mode == "retry" and task_name == "stock_enrichment_core":
        return f"{fresh_prefix}: non-target enrichment skipped {target_date}"
    table_requirements = {
        "mootdx_qfq_stock_kline": (("stock_daily_prices", "trade_date"),),
        "stock_valuation": (("stock_valuation_daily", "trade_date"),),
        "index_daily": (("index_daily", "trade_date"),),
        "sector_daily_prices": (("sector_daily_prices", "trade_date"),),
        "sector_composite_daily": ((SECTOR_COMPOSITE_TABLE, "trade_date"),),
        "sector_vendor_daily": ((SECTOR_VENDOR_TABLE, "trade_date"),),
        "etf_daily_prices": (("etf_daily_prices", "trade_date"),),
        "limit_boards": (("limit_board_trading", "trade_date"),),
        "news": (("news_articles", "publish_time"),),
        "rps_features": (
            ("quant_feature_rps", "trade_date"),
        ),
        "sector_rps_legacy_shadow": (("quant_feature_sector_rps", "trade_date"),),
        "sector_rps_v2": ((SECTOR_RPS_TABLES[0], "trade_date"),),
        "etf_rps_features": (("quant_feature_etf_rps", "trade_date"),),
        "strength_features": (
            ("market_strength_daily", "trade_date"),
            ("industry_strength_daily", "trade_date"),
        ),
        "structure_health": (("structure_health_daily", "trade_date"),),
        "core_stock_pool": (("quant_stock_pool", "add_date"),),
        "lifecycle_state_base": (("stock_lifecycle_state", "trade_date"),),
        "lifecycle_state": (("stock_lifecycle_state", "trade_date"),),
        "timing_signals": (("timing_signals_daily", "trade_date"),),
        "risk_plans": (("trade_plans", "trade_date"),),
        "research_snapshot": (("research_stock_daily_snapshot", "trade_date"),),
        "watch_engine": (("watch_daily_snapshot", "trade_date"),),
    }
    quality_tasks = {
        "data_core_stock_daily_quality_preview",
        "stock_daily_quality_repair",
        "data_core_stock_daily_quality",
    }
    date_complete_tasks = {
        "rps_features",
        "sector_rps_legacy_shadow",
        "etf_rps_features",
        "strength_features",
        "structure_health",
        "lifecycle_state_base",
        "lifecycle_state",
        "mrgc_preselect",
    }
    required_dates = (
        sorted(set(target_dates or (target_date,)))
        if task_name in date_complete_tasks
        else [target_date]
    )
    try:
        with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as conn, conn:
            if task_name == "etf_daily_prices":
                if not all(
                    table_exists(conn, table)
                    for table in ("etf_info", "etf_daily_prices")
                ):
                    return None
                expected, actual = conn.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(EXISTS (
                        SELECT 1 FROM etf_daily_prices prices
                        WHERE prices.symbol = universe.symbol
                          AND prices.trade_date >= ?
                          AND prices.trade_date < DATE(?, '+1 day')
                    )), 0)
                    FROM (SELECT DISTINCT symbol FROM etf_info) universe
                    """,
                    (target_date, target_date),
                ).fetchone()
                if expected > 0 and actual == expected:
                    return f"{fresh_prefix}: ETF coverage {actual}/{expected} {target_date}"
                return None
            if task_name == "etf_rps_features":
                if retry_task_skip_reason("etf_daily_prices", target_date, mode=mode) is None:
                    return None
                if not table_exists(conn, "quant_feature_etf_rps"):
                    return None
                for trade_date in required_dates:
                    expected, missing = conn.execute(
                        """
                        SELECT COUNT(*), COALESCE(SUM(NOT EXISTS (
                            SELECT 1 FROM quant_feature_etf_rps factors
                            WHERE factors.symbol = source.symbol
                              AND factors.trade_date >= ?
                              AND factors.trade_date < DATE(?, '+1 day')
                        )), 0)
                        FROM (
                            SELECT DISTINCT symbol FROM etf_daily_prices
                            WHERE trade_date >= ? AND trade_date < DATE(?, '+1 day')
                        ) source
                        """,
                        (trade_date, trade_date, trade_date, trade_date),
                    ).fetchone()
                    if expected == 0 or missing:
                        return None
                return (
                    f"{fresh_prefix}: ETF RPS source coverage "
                    f"{required_dates[0]}..{required_dates[-1]}"
                )
            if task_name in quality_tasks and _stock_quality_passed(conn, target_date):
                return f"{fresh_prefix}: stock_daily quality PASS {target_date}"
            if task_name == "sector_composite_quality":
                if _latest_quality_status(
                    conn,
                    module=MODULE_SECTOR_COMPOSITE,
                    target_date=target_date,
                ) == "PASS":
                    return f"{fresh_prefix}: sector composite quality PASS {target_date}"
                return None
            if task_name == "sector_rps_quality":
                if _latest_quality_status(
                    conn,
                    module=MODULE_SECTOR_RPS,
                    target_date=target_date,
                ) == "PASS":
                    return f"{fresh_prefix}: sector RPS quality PASS {target_date}"
                return None
            if task_name == "sector_rps_v2_quality":
                if _latest_quality_status(
                    conn,
                    module=MODULE_SECTOR_RPS,
                    target_date=target_date,
                ) == "PASS":
                    return f"{fresh_prefix}: sector RPS v2 quality PASS {target_date}"
                return None
            if task_name == "sector_vendor_daily" and _eastmoney_vendor_circuit_open(conn):
                return f"{fresh_prefix}: Eastmoney BK vendor circuit open; skip retry"
            if task_name == "sector_daily_prices":
                return f"{fresh_prefix}: legacy sector vendor task deferred to tail"
            if task_name == "mrgc_preselect" and table_exists(conn, "strategy_run_log"):
                if all(
                    conn.execute(
                        """
                        SELECT 1 FROM strategy_run_log
                        WHERE strategy_name = 'mrgc_v1'
                          AND trade_date >= ?
                          AND trade_date < DATE(?, '+1 day')
                          AND status = 'success'
                        LIMIT 1
                        """,
                        (trade_date, trade_date),
                    ).fetchone()
                    for trade_date in required_dates
                ):
                    return f"{fresh_prefix}: mrgc_v1 {required_dates[0]}..{required_dates[-1]}"
            if task_name in {"lifecycle_state_base", "lifecycle_state"}:
                counts = [
                    _lifecycle_target_count(conn, trade_date)
                    for trade_date in required_dates
                ]
                if all(
                    expected == actual
                    and table_has_complete_date(
                        conn,
                        "stock_lifecycle_state",
                        trade_date,
                    )
                    for trade_date, (expected, actual) in zip(required_dates, counts)
                ):
                    ranges = ", ".join(
                        f"{trade_date} {actual}/{expected}"
                        for trade_date, (expected, actual) in zip(required_dates, counts)
                    )
                    return f"{fresh_prefix}: lifecycle coverage {ranges}"
                return None
            if task_name == "watch_engine":
                counts = [
                    _watch_engine_target_count(conn, trade_date)
                    for trade_date in required_dates
                ]
                if all(expected == actual for expected, actual in counts):
                    ranges = ", ".join(
                        f"{trade_date} {actual}/{expected}"
                        for trade_date, (expected, actual) in zip(required_dates, counts)
                    )
                    return f"{fresh_prefix}: watch_engine coverage {ranges}"
                return None
            requirements = table_requirements.get(task_name)
            if requirements and all(
                (
                    (
                        _sector_table_target_complete(conn, table_name, trade_date)
                        if table_name in {
                            SECTOR_COMPOSITE_TABLE,
                            SECTOR_VENDOR_TABLE,
                            SECTOR_RPS_TABLES[0],
                        }
                        else table_has_complete_date(conn, table_name, trade_date)
                    )
                    if date_column == "trade_date"
                    else _table_target_count(
                        conn, table_name, date_column, trade_date
                    ) > 0
                )
                for trade_date in required_dates
                for table_name, date_column in requirements
            ):
                return (
                    f"{fresh_prefix}: {task_name} "
                    f"{required_dates[0]}..{required_dates[-1]}"
                )
            if task_name in {"macro_indicators", "sector_kline_continuity_repair"}:
                return f"{fresh_prefix}: non-target maintenance skipped {target_date}"
    except sqlite3.Error:
        return None
    return None


def stock_valuation_freshness(target_date: str | None = None) -> tuple[bool, str]:
    """Return whether valuation data already covers the latest local K-line date."""
    if not DB_PATH.exists():
        return False, "database_missing"

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        latest_stock_date = latest_table_date(conn, "stock_daily_prices", "trade_date")
        if not latest_stock_date:
            return False, "stock_daily_prices_empty"

        expected_date = target_date or latest_stock_date
        latest_valuation_date = latest_table_date(conn, "stock_valuation_daily", "trade_date")
        if not latest_valuation_date:
            return False, f"stock_valuation_daily_empty stock_latest={latest_stock_date}"

        row = conn.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(turnover IS NULL), 0),
                COALESCE(SUM(turnover = 0), 0),
                COALESCE(SUM(total_mv IS NULL), 0),
                COALESCE(SUM(total_mv = 0), 0),
                COALESCE(SUM(circ_mv IS NULL), 0),
                COALESCE(SUM(circ_mv = 0), 0)
            FROM stock_valuation_daily
            WHERE trade_date = ?
            """,
            (expected_date,),
        ).fetchone()
        (
            row_count,
            null_turnover,
            zero_turnover,
            null_total_mv,
            zero_total_mv,
            null_circ_mv,
            zero_circ_mv,
        ) = row

        missing_count = conn.execute(
            """
            WITH latest AS (
                SELECT MAX(trade_date) AS dt FROM stock_daily_prices
            ),
            missing AS (
                SELECT DISTINCT k.trade_date
                FROM stock_daily_prices k
                JOIN latest
                LEFT JOIN stock_valuation_daily v ON v.trade_date = k.trade_date
                WHERE v.trade_date IS NULL
                  AND k.trade_date >= DATE(latest.dt, '-30 day')
            )
            SELECT COUNT(*)
            FROM missing
            """
        ).fetchone()[0]

    fields_ok = (
        row_count > 0
        and null_turnover == 0
        and zero_turnover == 0
        and null_total_mv == 0
        and zero_total_mv == 0
        and null_circ_mv == 0
        and zero_circ_mv == 0
        and missing_count == 0
    )
    message = (
        f"valuation_latest={latest_valuation_date}, target={expected_date}, "
        f"stock_latest={latest_stock_date}, "
        f"rows={row_count}, null_turnover={null_turnover}, zero_turnover={zero_turnover}, "
        f"null_total_mv={null_total_mv}, zero_total_mv={zero_total_mv}, "
        f"null_circ_mv={null_circ_mv}, zero_circ_mv={zero_circ_mv}, "
        f"missing_recent_dates={missing_count}"
    )
    return fields_ok, message


def sector_kline_continuity_stats(conn: sqlite3.Connection) -> dict[str, object] | None:
    if not table_exists(conn, "sector_daily_prices"):
        return None

    has_sector_list = table_exists(conn, "sector_list")
    universe_sql = (
        """
        SELECT DISTINCT sector_name
        FROM sector_list
        WHERE sector_type = 'Industry'
          AND sector_name IS NOT NULL
          AND TRIM(sector_name) <> ''
        """
        if has_sector_list
        else """
        SELECT DISTINCT sector_name
        FROM sector_daily_prices
        WHERE sector_name IS NOT NULL
          AND TRIM(sector_name) <> ''
        """
    )
    row = conn.execute(
        f"""
        WITH universe AS (
            {universe_sql}
        ),
        ordered AS (
            SELECT p.sector_name,
                   DATE(p.trade_date) AS trade_date,
                   p.open,
                   p.close,
                   p.high,
                   p.low,
                   p.pct_chg,
                   LAG(p.close) OVER (
                       PARTITION BY p.sector_name
                       ORDER BY DATE(p.trade_date)
                   ) AS prev_close
            FROM sector_daily_prices p
            JOIN universe u ON u.sector_name = p.sector_name
            WHERE p.close IS NOT NULL
              AND p.close > 0
        )
        SELECT
            COUNT(*) AS rows,
            COUNT(DISTINCT sector_name) AS sectors,
            MIN(trade_date) AS date_min,
            MAX(trade_date) AS date_max,
            COALESCE(SUM(
                CASE
                    WHEN high < MAX(open, close) OR low > MIN(open, close)
                    THEN 1 ELSE 0
                END
            ), 0) AS invalid_ohlc_count,
            COALESCE(SUM(
                CASE
                    WHEN prev_close > 0
                     AND (
                         pct_chg IS NULL
                         OR ABS(((close / prev_close) - 1) * 100 - pct_chg) > 0.45
                     )
                    THEN 1 ELSE 0
                END
            ), 0) AS pct_mismatch_count,
            COALESCE(SUM(
                CASE
                    WHEN prev_close > 0
                     AND (close / prev_close > 2 OR close / prev_close < 0.5)
                    THEN 1 ELSE 0
                END
            ), 0) AS huge_jump_count
        FROM ordered
        """
    ).fetchone()
    if not row:
        return None
    keys = (
        "rows",
        "sectors",
        "date_min",
        "date_max",
        "invalid_ohlc_count",
        "pct_mismatch_count",
        "huge_jump_count",
    )
    return dict(zip(keys, row))


def add_quality_check(
    checks: list[QualityCheck],
    name: str,
    ok: bool,
    message: str,
    critical: bool = False,
) -> None:
    status = "OK" if ok else ("FAIL" if critical else "WARN")
    checks.append(QualityCheck(name=name, status=status, message=message, critical=critical))


def collect_quality_checks(
    tasks: Sequence[Task],
    target_trade_date: str | None = None,
    *,
    persist_missing: bool = True,
) -> list[QualityCheck]:
    """Run deterministic local data quality checks for the daily workflow."""
    checks: list[QualityCheck] = []
    task_names = {task.name for task in tasks}

    if not DB_PATH.exists():
        add_quality_check(
            checks,
            "database_exists",
            False,
            f"数据库不存在: {DB_PATH}",
            critical=True,
        )
        return checks

    expected_stock_date = target_trade_date or latest_completed_trading_date()
    try:
        with closing(sqlite3.connect(DB_PATH)) as quality_conn, quality_conn:
            data_core_reports = latest_quality_reports(
                quality_conn,
                trade_date=expected_stock_date,
            )
        report_markets = {str(report.get("market")) for report in data_core_reports}
        if not {"SH", "SZ", "BSE"}.issubset(report_markets):
            if persist_missing:
                data_core_reports = generate_stock_daily_quality_report(
                    DB_PATH,
                    trade_date=expected_stock_date,
                    run_id="workflow_quality_check",
                    persist=True,
                )
            else:
                with closing(sqlite3.connect(DB_PATH)) as readonly_quality_conn, readonly_quality_conn:
                    data_core_reports = [
                        build_market_report(
                            readonly_quality_conn,
                            run_id="check_only",
                            trade_date=expected_stock_date,
                            market=market,
                        )
                        for market in ("SH", "SZ", "BSE")
                    ]
        for report in data_core_reports:
            status = str(report["status"])
            if status == "FAIL":
                check_status = "FAIL"
            elif status == "WARNING":
                check_status = "WARN"
            else:
                check_status = "OK"
            checks.append(
                QualityCheck(
                    name=f"data_core_stock_daily_{report['market']}",
                    status=check_status,
                    message=format_report_line(report),
                    critical=True,
                )
            )
    except Exception as exc:
        checks.append(
            QualityCheck(
                name="data_core_stock_daily_quality",
                status="FAIL",
                message=f"Data Core quality report failed: {exc}",
                critical=True,
            )
        )

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        latest_stock_date = latest_table_date(
            conn, "stock_daily_prices", "trade_date", on_or_before=target_trade_date,
        )
        add_quality_check(
            checks,
            "stock_kline_latest_date",
            bool(latest_stock_date) and (
                target_trade_date is None or latest_stock_date == target_trade_date
            ),
            f"latest={latest_stock_date or 'none'}",
            critical=True,
        )

        if latest_stock_date:
            (
                latest_count,
                null_turnover,
                zero_turnover,
                bse_turnover_exempt,
                suspended_turnover_exempt,
            ) = stock_turnover_quality_stats(
                conn,
                latest_stock_date,
                latest_stock_date,
            )
            add_quality_check(
                checks,
                "stock_kline_latest_turnover",
                latest_count > 0 and null_turnover == 0 and zero_turnover == 0,
                (
                    f"date={latest_stock_date}, rows={latest_count}, "
                    f"null_turnover={null_turnover}, zero_turnover={zero_turnover}, "
                    f"bse_unadjusted_exempt={bse_turnover_exempt}, "
                    f"suspended_turnover_exempt={suspended_turnover_exempt}"
                ),
                critical=True,
            )

            recent_start = conn.execute(
                "SELECT DATE(?, '-' || ? || ' day')",
                (latest_stock_date, RECENT_QUALITY_WINDOW_DAYS),
            ).fetchone()[0]
            (
                all_rows,
                all_null_turnover,
                all_zero_turnover,
                all_bse_turnover_exempt,
                all_suspended_turnover_exempt,
            ) = stock_turnover_quality_stats(
                conn,
                recent_start,
                latest_stock_date,
            )
            add_quality_check(
                checks,
                "stock_kline_turnover_recent_window",
                all_rows > 0 and all_null_turnover == 0 and all_zero_turnover == 0,
                (
                    f"window={recent_start}..{latest_stock_date}, rows={all_rows}, "
                    f"null_turnover={all_null_turnover}, zero_turnover={all_zero_turnover}, "
                    f"bse_unadjusted_exempt={all_bse_turnover_exempt}, "
                    f"suspended_turnover_exempt={all_suspended_turnover_exempt}"
                ),
                critical=True,
            )

        if "stock_valuation" in task_names:
            latest_valuation_date = latest_table_date(conn, "stock_valuation_daily", "trade_date")
            add_quality_check(
                checks,
                "valuation_latest_matches_kline",
                bool(latest_stock_date and latest_valuation_date and latest_valuation_date >= latest_stock_date),
                f"valuation_latest={latest_valuation_date or 'none'}, stock_latest={latest_stock_date or 'none'}",
                critical=False,
            )

            if table_exists(conn, "stock_valuation_daily"):
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*),
                        COALESCE(SUM(turnover IS NULL), 0),
                        COALESCE(SUM(turnover = 0), 0),
                        COALESCE(SUM(total_mv IS NULL), 0),
                        COALESCE(SUM(total_mv = 0), 0),
                        COALESCE(SUM(circ_mv IS NULL), 0),
                        COALESCE(SUM(circ_mv = 0), 0)
                    FROM stock_valuation_daily
                    WHERE trade_date >= ?
                      AND trade_date < DATE(?, '+1 day')
                    """,
                    (expected_stock_date, expected_stock_date),
                ).fetchone()
                (
                    valuation_rows,
                    null_turnover,
                    zero_turnover,
                    null_total_mv,
                    zero_total_mv,
                    null_circ_mv,
                    zero_circ_mv,
                ) = row
                add_quality_check(
                    checks,
                    "valuation_numeric_fields_target_date",
                    (
                        valuation_rows > 0
                        and null_turnover == 0
                        and zero_turnover == 0
                        and null_total_mv == 0
                        and zero_total_mv == 0
                        and null_circ_mv == 0
                        and zero_circ_mv == 0
                    ),
                    (
                        f"rows={valuation_rows}, null_turnover={null_turnover}, "
                        f"zero_turnover={zero_turnover}, null_total_mv={null_total_mv}, "
                        f"zero_total_mv={zero_total_mv}, null_circ_mv={null_circ_mv}, "
                        f"zero_circ_mv={zero_circ_mv}"
                    ),
                    critical=False,
                )

                if latest_stock_date:
                    missing_count, sample_dates = conn.execute(
                        """
                        WITH latest AS (
                            SELECT MAX(trade_date) AS dt FROM stock_daily_prices
                        ),
                        missing AS (
                            SELECT DISTINCT k.trade_date
                            FROM stock_daily_prices k
                            JOIN latest
                            LEFT JOIN stock_valuation_daily v ON v.trade_date = k.trade_date
                            WHERE v.trade_date IS NULL
                              AND k.trade_date >= DATE(latest.dt, '-30 day')
                            ORDER BY k.trade_date
                        )
                        SELECT COUNT(*), GROUP_CONCAT(trade_date)
                        FROM missing
                        """
                    ).fetchone()
                    add_quality_check(
                        checks,
                        "valuation_recent_date_gaps",
                        missing_count == 0,
                        f"missing_recent_dates={missing_count}, dates={sample_dates or '-'}",
                        critical=False,
                    )

        if "core_stock_pool" in task_names:
            pool_count = 0
            if table_exists(conn, "quant_stock_pool"):
                pool_count = conn.execute("SELECT COUNT(*) FROM quant_stock_pool").fetchone()[0]
            add_quality_check(
                checks,
                "core_stock_pool_nonempty",
                pool_count > 0,
                f"rows={pool_count}",
                critical=True,
            )

        def add_sector_module_quality(
            module: str,
            check_name: str,
            *,
            critical: bool,
        ) -> None:
            reports = latest_quality_reports(
                conn,
                trade_date=expected_stock_date,
                module=module,
            )
            report = reports[0] if reports else None
            if report is None:
                add_quality_check(
                    checks,
                    check_name,
                    False,
                    f"quality report missing: module={module}, date={expected_stock_date}",
                    critical=critical,
                )
                return
            detail = report.get("detail") or {}
            add_quality_check(
                checks,
                check_name,
                str(report.get("status")) in {"PASS", "WARNING"}
                if not critical
                else str(report.get("status")) == "PASS",
                (
                    f"status={report.get('status')}, table={detail.get('table', '-')}, "
                    f"coverage={detail.get('coverage_ratio', '-')}, "
                    f"method_version={','.join(detail.get('method_versions', [])) or '-'}"
                ),
                critical=critical,
            )

        if "sector_composite_quality" in task_names:
            add_sector_module_quality(
                MODULE_SECTOR_COMPOSITE,
                "sector_composite_quality",
                critical=True,
            )
        if "sector_vendor_daily" in task_names:
            add_sector_module_quality(
                MODULE_SECTOR_VENDOR,
                "sector_vendor_quality",
                critical=False,
            )
        if {"sector_rps_v2", "sector_rps_v2_quality"}.intersection(task_names):
            add_sector_module_quality(
                MODULE_SECTOR_RPS,
                "sector_rps_v2_quality",
                critical=True,
            )

        if task_names.intersection(
            {"sector_kline_continuity_repair", "rps_features", "strength_features"}
        ) and "sector_composite_quality" not in task_names:
            stats = sector_kline_continuity_stats(conn)
            if stats is None:
                add_quality_check(
                    checks,
                    "sector_kline_continuity",
                    False,
                    "sector_daily_prices missing or unreadable",
                    critical=True,
                )
            else:
                latest_ok = not latest_stock_date or str(stats["date_max"] or "") >= latest_stock_date
                clean_ok = (
                    int(stats["rows"] or 0) > 0
                    and int(stats["sectors"] or 0) > 0
                    and int(stats["invalid_ohlc_count"] or 0) == 0
                    and int(stats["pct_mismatch_count"] or 0) == 0
                    and int(stats["huge_jump_count"] or 0) == 0
                )
                add_quality_check(
                    checks,
                    "sector_kline_continuity",
                    latest_ok and clean_ok,
                    (
                        f"rows={stats['rows']}, sectors={stats['sectors']}, "
                        f"range={stats['date_min']}..{stats['date_max']}, "
                        f"stock_latest={latest_stock_date or 'none'}, "
                        f"invalid_ohlc={stats['invalid_ohlc_count']}, "
                        f"pct_mismatch={stats['pct_mismatch_count']}, "
                        f"huge_jumps={stats['huge_jump_count']}"
                    ),
                    critical=True,
                )

        for table_name, columns, critical in DUPLICATE_CHECKS:
            if not table_exists(conn, table_name):
                continue

            date_column = next(
                (column for column in columns if column.endswith("_date")),
                None,
            )
            if date_column is None:
                continue
            duplicate_count = count_duplicate_keys(
                conn,
                table_name,
                columns,
                target_trade_date=expected_stock_date,
                date_column=date_column,
            )
            add_quality_check(
                checks,
                f"{table_name}_duplicate_keys",
                duplicate_count == 0,
                f"duplicate_keys={duplicate_count}",
                critical=critical,
            )

        if latest_stock_date:
            for task_name, table_name, date_column, critical in FRESHNESS_CHECKS:
                if task_name not in task_names:
                    continue

                table_latest = latest_table_date(
                    conn, table_name, date_column, on_or_before=target_trade_date,
                )
                if table_latest is None:
                    add_quality_check(
                        checks,
                        f"{table_name}_freshness",
                        False,
                        "table_missing_or_empty",
                        critical=critical,
                    )
                    continue

                add_quality_check(
                    checks,
                    f"{table_name}_freshness",
                    table_latest == target_trade_date if target_trade_date else table_latest >= latest_stock_date,
                    f"latest={table_latest}, stock_latest={latest_stock_date}",
                    critical=critical,
                )
                if table_latest > expected_stock_date:
                    add_quality_check(
                        checks,
                        f"{table_name}_future_date",
                        False,
                        f"latest={table_latest}, target={expected_stock_date}",
                        critical=False,
                    )

                expected_count = support_target_expected_count(conn, table_name)
                if expected_count is not None:
                    actual_count = conn.execute(
                        f"""
                        SELECT COUNT(DISTINCT {('index_code' if table_name == 'index_daily' else 'symbol')})
                        FROM {table_name}
                        WHERE DATE({date_column}) = DATE(?)
                        """,
                        (expected_stock_date,),
                    ).fetchone()[0]
                    add_quality_check(
                        checks,
                        f"{table_name}_target_coverage",
                        actual_count >= expected_count,
                        (
                            f"target={expected_stock_date}, rows={actual_count}, "
                            f"expected={expected_count}, missing={max(expected_count - actual_count, 0)}"
                        ),
                        critical=critical,
                    )

        if "limit_boards" in task_names:
            limit_date = latest_table_date(conn, "limit_board_trading", "trade_date")
            board_stats_date = latest_table_date(conn, "consecutive_boards_stats", "trade_date")
            if limit_date or board_stats_date:
                add_quality_check(
                    checks,
                    "limit_board_date_alignment",
                    limit_date == board_stats_date,
                    f"limit_board_trading={limit_date or 'none'}, consecutive_boards_stats={board_stats_date or 'none'}",
                )

    return checks


def print_quality_checks(
    log_file,
    tasks: Sequence[Task],
    target_trade_date: str | None = None,
    *,
    persist_missing: bool = True,
) -> list[QualityCheck]:
    checks = collect_quality_checks(
        tasks,
        target_trade_date=target_trade_date,
        persist_missing=persist_missing,
    )
    log_line(log_file, "\n[Local Quality Gates]")
    if target_trade_date:
        log_line(log_file, f"  target_trade_date={target_trade_date}")
    if not checks:
        log_line(log_file, "  no checks")
        return checks

    for check in checks:
        marker = "critical" if check.critical else "advisory"
        log_line(log_file, f"  {check.status} {check.name} ({marker}): {check.message}")

    stock_daily_checks = [
        check for check in checks if check.name.startswith("data_core_stock_daily_")
    ]
    stock_daily_failures = [check for check in stock_daily_checks if check.failed]
    log_line(log_file, "\n[RPS Gate]")
    if not stock_daily_checks:
        log_line(log_file, "  allowed=unknown reason=no stock_daily market quality checks")
    elif stock_daily_failures:
        reason = ", ".join(check.name for check in stock_daily_failures)
        log_line(log_file, f"  allowed=no reason={reason}")
    else:
        market_status = ", ".join(
            f"{check.name.removeprefix('data_core_stock_daily_')}={check.status}"
            for check in stock_daily_checks
        )
        log_line(log_file, f"  allowed=yes markets={market_status}")

    sector_composite_check = next(
        (check for check in checks if check.name == "sector_composite_quality"),
        None,
    )
    if sector_composite_check is None:
        log_line(
            log_file,
            "\n[Sector RPS Gate]\n  allowed=unknown reason=sector composite task not scheduled",
        )
    elif sector_composite_check.failed:
        log_line(
            log_file,
            f"\n[Sector RPS Gate]\n  allowed=no reason={sector_composite_check.message}",
        )
    else:
        log_line(log_file, "\n[Sector RPS Gate]\n  allowed=yes source=sector_composite_daily")

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        accepted_gaps = accepted_gap_exception_rows(conn, target_trade_date)
        accepted_symbols = _accepted_gap_symbols(accepted_gaps)
        source_failures = latest_source_failures(conn, trade_date=target_trade_date)
        source_failure_scope = "target_trade_date"
        if not source_failures and target_trade_date:
            source_failures = latest_source_failures(conn)
            source_failure_scope = "recent"

    if not stock_daily_failures:
        source_failures = [
            failure
            for failure in source_failures
            if not is_accepted_gap_source_failure(failure, accepted_symbols)
        ]

    log_line(log_file, "\n[Accepted Gap Exceptions]")
    if not accepted_gaps:
        log_line(log_file, "  none")
    else:
        for row in accepted_gaps:
            symbols = row.get("symbols") or []
            preview = ",".join(str(symbol) for symbol in symbols[:5])
            suffix = f" symbols={preview}" if preview else ""
            log_line(
                log_file,
                "  "
                f"market={row.get('market') or '-'} "
                f"reason={row.get('reason') or '-'} "
                f"source={row.get('source') or '-'} "
                f"count={row.get('count')}{suffix}",
            )

    log_line(log_file, "\n[Latest Source Failures]")
    if not source_failures:
        log_line(log_file, "  none")
    elif source_failure_scope == "recent":
        log_line(log_file, "  none for target_trade_date; showing recent failures")
    for failure in source_failures:
        log_line(
            log_file,
            "  "
            f"{failure.get('source')}.{failure.get('api_name')} "
            f"market={failure.get('market') or '-'} "
            f"status={failure.get('status')} "
            f"fail_count={failure.get('fail_count')} "
            f"error={failure.get('error_message') or '-'}",
        )
    return checks


def print_source_status(log_file) -> None:
    log_line(log_file, "\n[Data Core Source Registry]")
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        for source in official_sources_for(MODULE_STOCK_DAILY):
            latest = conn.execute(
                """
                SELECT status, success_count, fail_count, error_message, created_at
                FROM data_source_log
                WHERE source = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (source.id,),
            ).fetchone()
            if latest:
                status, success_count, fail_count, error_message, created_at = latest
                latest_label = (
                    f"latest={status} success={success_count} fail={fail_count} "
                    f"at={created_at} error={error_message or '-'}"
                )
            else:
                latest_label = "latest=no_source_log"
            if source.id == "tdx_standard_bj" and latest:
                detail_row = conn.execute(
                    """
                    SELECT detail_json
                    FROM data_source_log
                    WHERE source = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (source.id,),
                ).fetchone()
                if detail_row and detail_row[0]:
                    try:
                        detail = json.loads(detail_row[0])
                        best_server = detail.get("best_server") or {}
                        success_rate = detail.get("success_rate")
                        tdx_market = detail.get("tdx_market")
                        markets_seen = detail.get("markets_seen") or []
                        if best_server or success_rate is not None or markets_seen or tdx_market is not None:
                            latest_label += (
                                f" success_rate={success_rate} "
                                f"tdx_market={tdx_market or '-'} "
                                f"best_server={best_server.get('name', '-') if isinstance(best_server, dict) else '-'} "
                                f"best_market={best_server.get('markets', '-') if isinstance(best_server, dict) else '-'} "
                                f"markets_seen={markets_seen}"
                            )
                    except Exception:
                        pass
            log_line(
                log_file,
                "  "
                f"{source.id} official_write={source.allowed_official_write} "
                f"blocks_downstream={source.blocks_downstream} {latest_label}",
            )

    valuation_sources = [
        source
        for source in all_sources()
        if source.module == MODULE_STOCK_VALUATION
    ]
    if valuation_sources:
        log_line(log_file, "  valuation_sources_non_blocking=" + ", ".join(source.id for source in valuation_sources))


def ensure_pipeline_log_table() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_run_log (
                run_id TEXT PRIMARY KEY,
                pipeline_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                elapsed_seconds REAL,
                preflight_seconds REAL,
                backup_seconds REAL,
                workflow_seconds REAL,
                verification_seconds REAL,
                total_elapsed_seconds REAL,
                critical_failed_tasks TEXT,
                failed_tasks TEXT,
                log_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_task_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                status TEXT NOT NULL,
                target_dates TEXT,
                critical INTEGER NOT NULL DEFAULT 0,
                returncode INTEGER,
                elapsed_seconds REAL,
                error_message TEXT,
                deferred_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_id, task_name)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_task_log_run "
            "ON pipeline_task_log (run_id, status, task_name)"
        )
        run_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(pipeline_run_log)").fetchall()
        }
        for column in (
            "preflight_seconds",
            "backup_seconds",
            "workflow_seconds",
            "verification_seconds",
            "total_elapsed_seconds",
        ):
            if column not in run_columns:
                conn.execute(f"ALTER TABLE pipeline_run_log ADD COLUMN {column} REAL")
        conn.commit()


def record_pipeline_task(
    run_id: str,
    target_dates: str | Sequence[str],
    result: TaskResult,
) -> None:
    if isinstance(target_dates, str):
        target_text = target_dates
    else:
        target_text = ",".join(str(value) for value in target_dates)
    if result.deferred:
        status = "deferred"
    elif result.skipped:
        status = "skipped"
    elif result.ok:
        status = "success"
    else:
        status = "failed"
    error_message = None if result.returncode == 0 else f"returncode={result.returncode}"
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            INSERT INTO pipeline_task_log (
                run_id, task_name, status, target_dates, critical,
                returncode, elapsed_seconds, error_message, deferred_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(run_id, task_name) DO UPDATE SET
                status = excluded.status,
                target_dates = excluded.target_dates,
                critical = excluded.critical,
                returncode = excluded.returncode,
                elapsed_seconds = excluded.elapsed_seconds,
                error_message = excluded.error_message,
                deferred_reason = excluded.deferred_reason,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                run_id,
                result.name,
                status,
                target_text,
                int(result.critical),
                result.returncode,
                round(result.elapsed_seconds, 2),
                error_message,
                result.deferred_reason,
            ),
        )
        conn.commit()


def start_pipeline_log(run_id: str, mode: str, log_path: Path) -> None:
    ensure_pipeline_log_table()
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO pipeline_run_log (
                run_id, pipeline_name, mode, status, started_at, log_path
            ) VALUES (?, 'daily_data_workflow', ?, 'running', ?, ?)
            """,
            (run_id, mode, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(log_path)),
        )
        conn.commit()


def finish_pipeline_log(
    run_id: str,
    status: str,
    started_at: float,
    critical_failed_tasks: Sequence[str],
    failed_tasks: Sequence[str],
) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            UPDATE pipeline_run_log
            SET status = ?,
                finished_at = ?,
                elapsed_seconds = ?,
                critical_failed_tasks = ?,
                failed_tasks = ?
            WHERE run_id = ?
            """,
            (
                status,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                round(time.perf_counter() - started_at, 2),
                ",".join(critical_failed_tasks),
                ",".join(failed_tasks),
                run_id,
            ),
        )
        conn.commit()


def make_tasks(args: argparse.Namespace) -> list[Task]:
    python = sys.executable
    tasks: list[Task] = []
    target_trade_date = args.kline_end or latest_completed_trading_date()
    calculation_target_dates = recent_source_trading_dates(
        target_trade_date,
        args.pending_days,
    )
    target_date_args = ("--target-dates", *calculation_target_dates)
    rps_incremental_args = ("--missing-only",)
    kline_task_will_run = False

    if not args.skip_kline:
        kline_is_fresh, kline_reason = (
            (False, "force_kline=true")
            if args.force_kline
            else stock_kline_freshness(args.kline_end)
        )
        if kline_is_fresh:
            tasks.append(
                Task(
                    name="mootdx_qfq_stock_kline",
                    command=(),
                    critical=True,
                    skip_reason=f"local K-line fresh: {kline_reason}",
                )
            )
        else:
            kline_command = [
                python,
                "scripts/standalone/collect_stock_kline_mootdx_qfq.py",
                "--workers",
                str(args.kline_workers),
                "--pool-size",
                str(args.kline_pool_size),
                "--batch-size",
                str(args.kline_batch_size),
                "--lookback-days",
                str(args.kline_lookback_days),
                "--max-bars",
                str(args.kline_max_bars),
                "--submit-window",
                str(args.kline_submit_window),
                "--market",
                args.kline_market,
                "--timeout",
                str(args.kline_timeout),
                "--connect-retries",
                str(args.kline_connect_retries),
                "--max-fail-rate",
                str(args.kline_max_fail_rate),
            ]
            if args.kline_end:
                kline_command.extend(["--end", args.kline_end])

            tasks.append(
                Task(
                    name="mootdx_qfq_stock_kline",
                    command=tuple(kline_command),
                    critical=True,
                )
            )
            kline_task_will_run = True

    tasks.append(
        Task(
            name="data_core_stock_daily_quality_preview",
            command=(
                python,
                "-m",
                "data_core.quality",
                "--date",
                target_trade_date,
                "--fail-on-fail",
            ),
            critical=False,
        )
    )

    if not args.skip_kline_repair:
        tasks.append(
            Task(
                name="stock_daily_quality_repair",
                command=(
                    python,
                    "scripts/repair_stock_daily_quality.py",
                    "--date",
                    target_trade_date,
                ),
                critical=False,
                run_if_failed="data_core_stock_daily_quality_preview",
            )
        )

    if not args.skip_gap_repair:
        gap_window = args.pending_days if args.morning else args.gap_window_trading_days
        gap_command = [
            python,
            "scripts/repair_stock_daily_gaps.py",
            "--end-date",
            target_trade_date,
            "--window-trading-days",
            str(max(1, gap_window)),
            "--max-bars",
            str(args.gap_max_bars),
            "--workers",
            "2",
            "--pool-size",
            "2",
            "--submit-window",
            "16",
        ]
        if args.check_only:
            gap_command.append("--dry-run")
        tasks.append(
            Task(
                name="stock_daily_gap_repair",
                command=tuple(gap_command),
                critical=False,
                timeout_seconds=900,
            )
        )

    tasks.append(
        Task(
            name="data_core_stock_daily_quality",
            command=(
                python,
                "-m",
                "data_core.quality",
                "--date",
                target_trade_date,
                "--fail-on-fail",
            ),
            critical=True,
            run_if_failed="data_core_stock_daily_quality_preview",
        )
    )

    # The local sector net-value track is part of the core analytics chain. It
    # runs immediately after the stock P0 gate and before sector RPS. The
    # calculator itself is supplied by the data layer; the workflow only owns
    # ordering, idempotency and the quality gate.
    if not args.skip_sector_composite and not args.skip_market_side:
        composite_reason = retry_task_skip_reason(
            "sector_composite_daily",
            target_trade_date,
            mode="retry" if args.retry else "daily",
        ) if args.retry else None
        composite_command = (
            python,
            "scripts/rebuild_sector_composite.py",
            "--target-date",
            target_trade_date,
            "--missing-only",
            "--membership-status",
            "daily_snapshot",
        )
        tasks.append(
            Task(
                name="sector_composite_daily",
                command=tuple(composite_command),
                critical=True,
                timeout_seconds=300,
                skip_reason=(
                    f"local sector composite fresh: {composite_reason}"
                    if composite_reason
                    else None
                ),
            )
        )
        tasks.append(
            Task(
                name="sector_composite_quality",
                command=(
                    python,
                    "-m",
                    "data_core.quality",
                    "--module",
                    MODULE_SECTOR_COMPOSITE,
                    "--date",
                    target_trade_date,
                    "--fail-on-fail",
                ),
                critical=True,
                timeout_seconds=60,
            )
        )

    if args.force_valuation:
        valuation_is_fresh, valuation_reason = False, "force_valuation=true"
    elif kline_task_will_run:
        valuation_is_fresh, valuation_reason = False, "stock_kline_task_runs=true"
    else:
        valuation_is_fresh, valuation_reason = stock_valuation_freshness(target_trade_date)

    if valuation_is_fresh:
        tasks.append(
            Task(
                name="stock_valuation",
                command=(),
                critical=False,
                skip_reason=f"local valuation fresh: {valuation_reason}",
            )
        )
    else:
        tasks.append(
            Task(
                name="stock_valuation",
                command=(
                    python,
                    "-m",
                    "data_job.collectors.stock_valuation_collector",
                    "--target-date",
                    target_trade_date,
                ),
                critical=False,
            )
        )

    if not args.skip_market_side:
        tasks.extend(
            [
                Task(
                    "index_daily",
                    (
                        python,
                        "-m",
                        "data_job.collectors.index_data_collector",
                        "--end",
                        target_trade_date,
                        "--max-bars",
                        "420",
                    ),
                    timeout_seconds=180,
                    independent=True,
                ),
                Task(
                    "sector_daily_prices",
                    (),
                    timeout_seconds=300,
                    independent=True,
                    skip_reason="legacy sector vendor task deferred to workflow tail",
                ),
                Task(
                    "etf_daily_prices",
                    (
                        python,
                        "-m",
                        "data_job.collectors.etf_kline_collector",
                        "--end",
                        target_trade_date,
                        "--max-bars",
                        "60",
                    ),
                    timeout_seconds=240,
                    independent=True,
                ),
            ]
        )
        if not args.morning:
            tasks.extend(
                [
                    Task(
                        "limit_boards",
                        (
                            python,
                            "-c",
                            "from data_job.collectors.limit_boards_collector import "
                            "LimitBoardsCollector; "
                            f"LimitBoardsCollector().run(start='{target_trade_date}', end='{target_trade_date}')",
                        ),
                        timeout_seconds=180,
                        independent=True,
                    ),
                    Task(
                        "news",
                        (
                            python,
                            "-c",
                            "from data_job.collectors.news_collector import NewsCollector; "
                            f"NewsCollector().run(start='{target_trade_date}', end='{target_trade_date}')",
                        ),
                        timeout_seconds=180,
                        independent=True,
                    ),
                    Task(
                        "macro_indicators",
                        (python, "-m", "data_job.collectors.macro_data_collector"),
                        timeout_seconds=180,
                        independent=True,
                    ),
                ]
            )

    if args.include_quarterly:
        tasks.extend(
            [
                Task("finance_summary", (python, "-m", "data_job.collectors.finance_summary_collector")),
                Task("fund_holdings", (python, "-m", "data_job.collectors.fund_holdings_collector")),
            ]
        )

    if args.northbound_limit > 0:
        tasks.append(
            Task(
                name="northbound_holdings_chunk",
                command=(
                    python,
                    "-m",
                    "data_job.collectors.northbound_holdings_collector",
                    "--limit",
                    str(args.northbound_limit),
                    "--workers",
                    "2",
                    "--delay",
                    "0.2",
                    "--batch-size",
                    "50",
                ),
            )
        )

    if not args.skip_sector_kline_repair and not args.morning:
        tasks.append(
            Task(
                name="sector_kline_continuity_repair",
                command=(),
                critical=False,
                timeout_seconds=0,
                independent=True,
                skip_reason=(
                    "legacy sector continuity repair disabled; "
                    "vendor reference sync runs at workflow tail"
                ),
            )
        )

    if not args.skip_rps or args.include_rps:
        tasks.append(
            Task(
                name="rps_features",
                command=(
                    python,
                    "-m",
                    "quant_engine.runner.feature_runner",
                    "--mode",
                    "daily",
                    "--calculators",
                    "stock",
                    *rps_incremental_args,
                    *target_date_args,
                ),
                critical=True,
            )
        )
        if not args.skip_sector_composite and not args.skip_market_side:
            tasks.append(
                Task(
                    name="sector_rps_v2",
                    command=(
                        python,
                        "-m",
                        "quant_engine.runner.feature_runner",
                        "--mode",
                        "daily",
                        "--calculators",
                        "sector_composite",
                        *rps_incremental_args,
                        *target_date_args,
                    ),
                    critical=True,
                    timeout_seconds=300,
                )
            )
            tasks.append(
                Task(
                    name="sector_rps_v2_quality",
                    command=(
                        python,
                        "-m",
                        "data_core.quality",
                        "--module",
                        MODULE_SECTOR_RPS,
                        "--date",
                        target_trade_date,
                        "--fail-on-fail",
                    ),
                    critical=True,
                    timeout_seconds=60,
                )
            )
        tasks.append(
            Task(
                name="sector_rps_legacy_shadow",
                command=(
                    python,
                    "-m",
                    "quant_engine.runner.feature_runner",
                    "--mode",
                    "daily",
                    "--calculators",
                    "sector",
                    *rps_incremental_args,
                    *target_date_args,
                ),
                # Legacy sector RPS is retained for shadow comparison only.
                # It is deliberately non-critical and non-independent: an
                # upstream canonical quality gate may defer it, while a legacy
                # failure never blocks the canonical sector chain.
                critical=False,
                timeout_seconds=300,
            )
        )
        tasks.append(
            Task(
                name="etf_rps_features",
                command=(
                    python,
                    "-m",
                    "quant_engine.runner.feature_runner",
                    "--mode",
                    "daily",
                    "--calculators",
                    "etf",
                    *target_date_args,
                ),
                critical=False,
            )
        )

    run_p0 = (not args.skip_p0) or args.include_strength
    run_p1 = not args.skip_p1

    if run_p0:
        tasks.extend(
            [
                Task(
                    name="strength_features",
                    command=(
                        python,
                        "-m",
                        "quant_engine.runner.feature_runner",
                        "--mode",
                        "daily",
                        "--calculators",
                        "strength",
                        *rps_incremental_args,
                        *target_date_args,
                    ),
                    critical=True,
                ),
                Task(
                    name="structure_health",
                    command=(
                        python,
                        "-m",
                        "quant_engine.runner.feature_runner",
                        "--mode",
                        "daily",
                        "--calculators",
                        "structure",
                        *rps_incremental_args,
                        *target_date_args,
                    ),
                    critical=True,
                ),
            ]
        )

    tasks.append(
        Task(
            name="core_stock_pool",
            command=(python, "-m", "quant_engine.pool.maintain_pool"),
            critical=True,
        )
    )

    if run_p0:
        tasks.append(
            Task(
                name="lifecycle_state_base",
                command=(
                    python,
                    "-m",
                    "quant_engine.runner.feature_runner",
                    "--mode",
                    "daily",
                    "--calculators",
                    "lifecycle",
                    *rps_incremental_args,
                    *target_date_args,
                ),
                critical=True,
            )
        )

    if run_p1:
        p1_tasks = []
        if args.include_strategy:
            mrgc_command = (
                python,
                "-m",
                "quant_engine.runner.strategy_runner",
                "--strategy",
                "mrgc",
                "--dates",
                *calculation_target_dates,
                "--missing-only",
            )
            p1_tasks.append(
                Task(
                    name="mrgc_preselect",
                    command=mrgc_command,
                    critical=True,
                )
            )
        p1_tasks.extend(
            [
                Task(
                    name="timing_signals",
                    command=(
                        python,
                        "-m",
                        "quant_engine.runner.feature_runner",
                        "--mode",
                        "daily",
                        "--calculators",
                        "timing",
                        *target_date_args,
                    ),
                    critical=True,
                ),
                Task(
                    name="risk_plans",
                    command=(
                        python,
                        "-m",
                        "quant_engine.runner.feature_runner",
                        "--mode",
                        "daily",
                        "--calculators",
                        "risk",
                        *target_date_args,
                    ),
                    critical=True,
                ),
            ]
        )
        tasks.extend(p1_tasks)

    if run_p0:
        tasks.append(
            Task(
                name="lifecycle_state",
                command=(
                    python,
                    "-m",
                    "quant_engine.runner.feature_runner",
                    "--mode",
                    "daily",
                    "--calculators",
                    "lifecycle",
                    *rps_incremental_args,
                    *target_date_args,
                ),
                critical=True,
            )
        )

    if run_p0 and not args.skip_watch and not args.morning:
        tasks.extend(
            [
                Task(
                    name="watch_engine",
                    command=(
                        python,
                        "-m",
                        "quant_engine.watch.engine",
                        "--mode",
                        "daily",
                        "--trade-date",
                        target_trade_date,
                    ),
                    critical=True,
                ),
                Task(
                    name="watch_email_immediate",
                    command=(
                        python,
                        "-m",
                        "quant_engine.watch.dispatcher",
                        "--mode",
                        "immediate",
                        "--trade-date",
                        target_trade_date,
                    ),
                    critical=False,
                    timeout_seconds=60,
                ),
            ]
        )

    if run_p0 and not args.morning:
        tasks.append(
            Task(
                name="structure_monitor",
                command=(
                    python,
                    "scripts/run_structure_monitor.py",
                    "--trade-date",
                    target_trade_date,
                    "--workflow-child",
                ),
                critical=False,
                independent=True,
                timeout_seconds=120,
            )
        )

    if run_p0 and not args.skip_watch and not args.morning:
        tasks.append(
            Task(
                name="three_group_report_prepare",
                command=(
                    python,
                    "-m",
                    "quant_engine.watch.three_group_daily",
                    "--trade-date",
                    target_trade_date,
                    "--mode",
                    "prepare",
                    "--workflow-child",
                ),
                critical=False,
                timeout_seconds=180,
            )
        )

    if run_p0 and not args.skip_watch and not args.morning:
        tasks.append(
            Task(
                name="watch_email_digest",
                command=(
                    python,
                    "-m",
                    "quant_engine.watch.dispatcher",
                    "--mode",
                    "digest",
                    "--trade-date",
                    target_trade_date,
                    "--require-trusted",
                ),
                critical=False,
                timeout_seconds=60,
            )
        )

    if not args.skip_portfolio and not args.morning:
        tasks.extend(
            [
                Task(
                    name="portfolio_daily_snapshots",
                    command=(
                        python,
                        "scripts/build_portfolio_snapshots.py",
                        "--trade-date",
                        target_trade_date,
                    ),
                    critical=False,
                    timeout_seconds=60,
                ),
                Task(
                    name="portfolio_exit_engine",
                    command=(
                        python,
                        "scripts/run_portfolio_exit_engine.py",
                        "--trade-date",
                        target_trade_date,
                    ),
                    critical=False,
                    timeout_seconds=60,
                ),
                Task(
                    name="portfolio_exit_digest",
                    command=(
                        python,
                        "-m",
                        "quant_engine.watch.dispatcher",
                        "--mode",
                        "portfolio-digest",
                        "--trade-date",
                        target_trade_date,
                    ),
                    critical=False,
                    timeout_seconds=60,
                ),
            ]
        )

    if run_p1 and not args.morning:
        tasks.append(
            Task(
                name="research_snapshot",
                command=(
                    python,
                    "-m",
                    "quant_engine.runner.feature_runner",
                    "--mode",
                    "daily",
                    "--calculators",
                    "research",
                    *target_date_args,
                ),
            )
        )

    if not args.skip_enrichment and not args.morning:
        tasks.append(
            Task(
                name="stock_enrichment_core",
                command=(
                    python,
                    "-m",
                    "data_job.collectors.stock_enrichment_collector",
                    "--mode",
                    "core",
                    "--trade-date",
                    target_trade_date,
                    "--limit",
                    str(args.enrichment_limit),
                ),
                critical=False,
                timeout_seconds=600 if args.retry else 900,
            )
        )

    # Eastmoney BK is a non-blocking reference track. Keep it at the very end
    # so a WAF/rate-limit/timeout cannot delay or poison the local composite
    # net-value, RPS, strategy and Watch chain. The collector owns its global
    # request gate; retry mode only revisits missing dates.
    if (
        not args.skip_sector_vendor
        and not args.skip_market_side
        and not args.morning
    ):
        vendor_reason = None
        if DB_PATH.exists():
            try:
                with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as vendor_conn, vendor_conn:
                    vendor_reason = sector_vendor_incremental_skip_reason(
                        vendor_conn,
                        target_trade_date,
                        target_only=True,
                    )
                    if args.retry:
                        vendor_reason = retry_task_skip_reason(
                            "sector_vendor_daily",
                            target_trade_date,
                            mode="retry",
                        ) or vendor_reason
            except sqlite3.Error as exc:
                vendor_reason = f"vendor preflight unavailable: {exc}"
        tasks.append(
            Task(
                name="sector_vendor_daily",
                command=(
                    python,
                    "-m",
                    "data_job.collectors.sector_kline_collector",
                    "--end",
                    target_trade_date,
                    "--target-only",
                ),
                critical=False,
                independent=True,
                timeout_seconds=420 if args.retry else 900,
                skip_reason=(
                    f"sector vendor deferred/skipped: {vendor_reason}"
                    if vendor_reason
                    else None
                ),
            )
        )
    if args.retry or args.morning:
        tasks = [
            replace(task, skip_reason=reason)
            if (
                reason := retry_task_skip_reason(
                    task.name,
                    target_trade_date,
                    mode="morning" if args.morning else "retry",
                    target_dates=calculation_target_dates,
                )
            )
            else task
            for task in tasks
        ]
    if args.skip_notifications:
        tasks = [
            task for task in tasks
            if "quant_engine.watch.dispatcher" not in task.command
        ]
    return tasks


def run_task(task: Task, log_file, allow_model_calls: bool = False) -> TaskResult:
    start = time.perf_counter()

    if task.skip_reason:
        log_line(log_file, f"\n=== SKIP {task.name} ===")
        log_line(log_file, task.skip_reason)
        return TaskResult(task.name, 0, 0.0, task.critical, skipped=True)

    command_desc = " ".join(task.command)
    log_line(log_file, f"\n=== START {task.name} ===")
    log_line(log_file, f"$ {command_desc}")

    env = build_task_env(allow_model_calls=allow_model_calls)

    process = subprocess.Popen(
        task.command,
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    assert process.stdout is not None
    deadline = time.monotonic() + task.timeout_seconds if task.timeout_seconds else None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    timed_out = False

    try:
        while process.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                log_line(
                    log_file,
                    f"任务超时，准备终止: {task.name} timeout={task.timeout_seconds}s",
                )
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    log_line(log_file, f"任务未响应 SIGTERM，强制结束: {task.name}")
                    process.kill()
                break

            for key, _events in selector.select(timeout=0.5):
                line = key.fileobj.readline()
                if not line:
                    continue
                print(line, end="", flush=True)
                log_file.write(line)
                log_file.flush()

        for line in process.stdout:
            print(line, end="", flush=True)
            log_file.write(line)
            log_file.flush()
    finally:
        selector.close()

    returncode = process.wait()
    if timed_out and returncode == 0:
        returncode = 124
    elapsed = time.perf_counter() - start
    status = "OK" if returncode == 0 else f"FAILED rc={returncode}"
    log_line(log_file, f"=== END {task.name}: {status}, elapsed={elapsed:.1f}s ===")
    return TaskResult(task.name, returncode, elapsed, task.critical)


def run_tasks_until_blocked(
    tasks: Sequence[Task],
    log_file,
    runner: Callable[[Task, object], TaskResult] = run_task,
    on_stage: Callable[[Task], None] | None = None,
    on_result: Callable[[TaskResult], None] | None = None,
    deadline_monotonic: float | None = None,
) -> list[TaskResult]:
    """Run ordered tasks while allowing independent support work after core failure."""
    results: list[TaskResult] = []
    results_by_name: dict[str, TaskResult] = {}
    core_blocked = False

    for task in tasks:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            result = TaskResult(
                name=task.name,
                returncode=0,
                elapsed_seconds=0.0,
                critical=task.critical,
                skipped=True,
                deferred=True,
                deferred_reason="time_budget_exhausted",
            )
            results.append(result)
            results_by_name[result.name] = result
            if on_result:
                on_result(result)
            continue

        if core_blocked and not task.independent:
            result = TaskResult(
                name=task.name,
                returncode=0,
                elapsed_seconds=0.0,
                critical=task.critical,
                skipped=True,
                deferred=True,
                deferred_reason="blocked_by_core_failure",
            )
            results.append(result)
            results_by_name[result.name] = result
            if on_result:
                on_result(result)
            continue

        if task.run_if_failed:
            dependency = results_by_name.get(task.run_if_failed)
            if dependency is None or not dependency.failed:
                result = TaskResult(
                    name=task.name,
                    returncode=0,
                    elapsed_seconds=0.0,
                    critical=task.critical,
                    skipped=True,
                )
                results.append(result)
                results_by_name[result.name] = result
                if on_result:
                    on_result(result)
                continue

        if on_stage:
            on_stage(task)
        result = runner(task, log_file)
        results.append(result)
        results_by_name[result.name] = result
        if on_result:
            on_result(result)

        if result.failed and result.critical and not task.independent:
            core_blocked = True
            log_line(log_file, f"关键任务失败，递延依赖任务: {task.name}")

    return results


def print_plan(log_file, tasks: Sequence[Task]) -> None:
    log_line(log_file, "\n[Task Plan]")
    for index, task in enumerate(tasks, 1):
        marker = "critical" if task.critical else "best-effort"
        suffix = f" - skip: {task.skip_reason}" if task.skip_reason else ""
        log_line(log_file, f"  {index}. {task.name} ({marker}){suffix}")


def print_count_delta(
    log_file,
    before: dict[str, dict[str, object]],
    after: dict[str, dict[str, object]],
) -> None:
    log_line(log_file, "\n[Row Delta]")
    for table_name, info in after.items():
        before_count = int(before.get(table_name, {}).get("count", 0) or 0)
        after_count = int(info.get("count", 0) or 0)
        log_line(log_file, f"  {table_name}: {after_count - before_count:+d}")


def main() -> int:
    args = build_parser().parse_args()
    if args.kline_end:
        try:
            target_day = date.fromisoformat(args.kline_end)
        except ValueError:
            print(f"invalid --kline-end date: {args.kline_end}", file=sys.stderr)
            return 2
        if not is_a_share_session(target_day):
            print(f"--kline-end {args.kline_end} is not an A-share trading date", file=sys.stderr)
            return 2
    workflow_mode = resolve_workflow_mode(args)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"daily_data_workflow_{datetime.now():%Y%m%d_%H%M%S}.log"
    run_id = datetime.now().strftime(f"{workflow_mode}_%Y%m%d_%H%M%S")
    run_started_at = time.perf_counter()

    with open(LOCK_PATH, "w") as lock_file, open(log_path, "w", encoding="utf-8") as log_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log_line(log_file, "已有 daily data workflow 正在运行，本次退出。")
            return 2

        log_line(log_file, f"EvoAlpha daily data workflow started at {datetime.now():%F %T}")
        log_line(log_file, f"backend={BACKEND_DIR}")
        log_line(log_file, f"log={log_path}")
        if args.check_only:
            log_line(log_file, "data_core_schema=skipped_read_only_check")
        else:
            schema_result = ensure_data_core_schema(DB_PATH)
            log_line(log_file, f"data_core_schema={schema_result}")
        if args.allow_model_calls:
            log_line(log_file, "execution_policy=model_calls_allowed")
        else:
            log_line(log_file, "execution_policy=local_code_only, model_env=masked")
        if not args.check_only:
            start_pipeline_log(run_id, workflow_mode, log_path)
        target_trade_date = args.kline_end or latest_completed_trading_date()
        log_line(log_file, f"target_trade_date={target_trade_date}")
        write_lock_metadata(
            lock_file,
            run_id=run_id,
            mode=workflow_mode,
            target_trade_date=target_trade_date,
            stage="planning",
        )

        tasks = make_tasks(args)
        before = snapshot_tables(target_trade_date)
        print_snapshot(log_file, "Before Snapshot", before)
        print_plan(log_file, tasks)

        if args.check_only:
            write_lock_metadata(
                lock_file,
                run_id=run_id,
                mode="check-only",
                target_trade_date=target_trade_date,
                stage="check-only",
            )
            print_source_status(log_file)
            print_duplicate_checks(log_file, target_trade_date)
            print_quality_checks(
                log_file,
                tasks,
                target_trade_date,
                persist_missing=False,
            )
            log_line(log_file, "\ncheck-only 模式，不执行采集。")
            return 0

        deadline_monotonic = None
        if args.morning and args.time_budget_minutes > 0:
            deadline_monotonic = run_started_at + args.time_budget_minutes * 60
        results = run_tasks_until_blocked(
            tasks,
            log_file,
            runner=lambda task, output: run_task(
                task,
                output,
                allow_model_calls=args.allow_model_calls,
            ),
            on_stage=lambda task: write_lock_metadata(
                lock_file,
                run_id=run_id,
                mode=workflow_mode,
                target_trade_date=target_trade_date,
                stage=task.name,
            ),
            on_result=lambda result: record_pipeline_task(
                run_id,
                target_trade_date,
                result,
            ),
            deadline_monotonic=deadline_monotonic,
        )

        after = snapshot_tables(target_trade_date)
        print_snapshot(log_file, "After Snapshot", after)
        print_count_delta(log_file, before, after)
        print_source_status(log_file)
        print_duplicate_checks(log_file, target_trade_date)
        quality_checks = print_quality_checks(log_file, tasks, target_trade_date)

        total_elapsed = sum(result.elapsed_seconds for result in results)
        result_by_name = {result.name: result for result in results}
        final_quality = result_by_name.get("data_core_stock_daily_quality")
        recovered_failures = {
            "data_core_stock_daily_quality_preview"
            if final_quality and final_quality.ok
            else ""
        }
        failed = [
            result
            for result in results
            if result.failed and result.name not in recovered_failures
        ]
        deferred = [result for result in results if result.deferred]
        critical_failed = [result for result in failed if result.critical]
        quality_failed = [check for check in quality_checks if check.failed and check.critical]

        log_line(log_file, "\n[Summary]")
        for result in results:
            log_line(
                log_file,
                f"  {result.name}: {result.status_label}, {result.elapsed_seconds:.1f}s",
            )
        log_line(log_file, f"  total_task_elapsed={total_elapsed:.1f}s")

        if critical_failed:
            names = ", ".join(result.name for result in critical_failed)
            log_line(log_file, f"关键任务失败: {names}")
            finish_pipeline_log(
                run_id,
                "failed",
                run_started_at,
                [result.name for result in critical_failed],
                [result.name for result in failed],
            )
            return 1

        if quality_failed:
            names = ", ".join(check.name for check in quality_failed)
            log_line(log_file, f"本地质量门失败: {names}")
            finish_pipeline_log(
                run_id,
                "failed",
                run_started_at,
                [f"quality:{check.name}" for check in quality_failed],
                [result.name for result in failed],
            )
            return 1

        if failed:
            names = ", ".join(result.name for result in failed)
            log_line(log_file, f"非关键任务失败，核心链路已完成: {names}")

        if deferred:
            names = ", ".join(result.name for result in deferred)
            log_line(log_file, f"任务已递延到后续流程: {names}")

        log_line(log_file, "EvoAlpha daily data workflow finished.")
        finish_pipeline_log(
            run_id,
            "success_with_warnings" if failed or deferred else "success",
            run_started_at,
            [],
            [result.name for result in failed],
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
