"""
High-speed stock daily K-line collector using mootdx + local qfq.

Key behaviors:
1. Fetch raw bars from mootdx
2. Fetch xdxr actions from mootdx
3. Apply local qfq adjustment
4. Write back to SQLite in batches with a single writer
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from queue import Empty, Queue
from threading import Condition
from typing import Optional

# Must precede pandas/numpy imports in the standalone entry point.
for _thread_env in (
    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
):
    os.environ[_thread_env] = "1"
del _thread_env

import pandas as pd
from tqdm import tqdm

backend_dir = Path(__file__).parent.parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from data_core.bse_codes import TDX_STANDARD_BJ_SOURCE_NAME
from data_core.schema import ensure_data_core_schema, log_data_source_run
from data_core.market_calendar import is_a_share_session
from data_core.symbols import MARKET_BSE, MARKET_SH, MARKET_SZ, P0_MARKETS, infer_market, is_p0_symbol
from data_job.core.mootdx_adapter import MootdxAdapter
from data_job.core.mootdx_qfq import apply_mootdx_qfq

DEFAULT_START_DATE = "2023-01-01"
SOURCE_NAME = "tdx_mootdx_qfq"
RAW_SOURCE_NAME = "tdx_mootdx_raw"
DEFAULT_MAX_BARS = 160
SAVE_COLUMNS = [
    "symbol",
    "trade_date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "pct_chg",
    "turnover_rate",
    "source",
    "quality_flag",
    "run_id",
    "updated_at",
    "adjustment_mode",
    "market",
]
RAW_SAVE_COLUMNS = [
    "symbol",
    "trade_date",
    "close",
    "source",
    "quality_flag",
    "run_id",
    "updated_at",
    "adjustment_mode",
    "market",
]


@dataclass
class FetchResult:
    symbol: str
    status: str
    market: str
    source: str = SOURCE_NAME
    data: Optional[pd.DataFrame] = None
    raw_data: Optional[pd.DataFrame] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: str = ""
    write_policy: str = "new_only"
    anchor_date: Optional[date] = None
    anchor_raw_close: Optional[float] = None


class SourceOutageCircuitBreaker:
    """Stop a batch after repeated failures that prove the whole source is down."""

    def __init__(self, threshold: int):
        self.threshold = max(1, int(threshold))
        self.consecutive_failures = 0
        self.tripped = False

    def observe(self, result: FetchResult) -> bool:
        if self.tripped:
            return True
        if result.status == "fail" and result.reason == "source unavailable":
            self.consecutive_failures += 1
        elif result.status == "success":
            self.consecutive_failures = 0
        if self.consecutive_failures >= self.threshold:
            self.tripped = True
        return self.tripped


class AdapterPool:
    """One bounded discovery round, followed by clean streams to its validated node."""

    MAX_SIZE = 8
    STARTUP_BUDGET_SECONDS = 30.0

    def __init__(self, size: int, timeout: int, max_retries: int, retry_delay: float):
        if not isinstance(size, int) or not 1 <= size <= self.MAX_SIZE:
            raise ValueError(f"pool size must be between 1 and {self.MAX_SIZE}")
        self._queue: Queue[MootdxAdapter] = Queue(maxsize=size)
        self._condition = Condition()
        self._closed = False
        self._owned: dict[int, MootdxAdapter] = {}
        self._leased: set[int] = set()
        self.failures: list[str] = []
        deadline = time.monotonic() + self.STARTUP_BUDGET_SECONDS
        seed = None
        # Legacy retry arguments are intentionally not multiplied by pool slots.
        for slot in range(size):
            if time.monotonic() >= deadline:
                self.failures.append(f"slot={slot + 1}, startup budget exhausted")
                break
            try:
                adapter = (
                    MootdxAdapter(timeout=timeout, max_retries=1, connection_deadline=deadline,
                                  managed_recovery=True)
                    if seed is None else seed.fork_connection(connection_deadline=deadline)
                )
            except Exception as exc:
                self.failures.append(f"slot={slot + 1}, startup {type(exc).__name__}")
                break
            if not adapter.is_available():
                self.failures.append(f"slot={slot + 1}, discovery/connection unavailable")
                adapter.disconnect()
                break
            self._owned[id(adapter)] = adapter
            self._queue.put_nowait(adapter)
            seed = adapter

    @property
    def available(self) -> int:
        return self._queue.qsize()

    def acquire(self, timeout: int = 30) -> MootdxAdapter:
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("acquire timeout must be nonnegative and finite")
        deadline = time.monotonic() + min(30.0, timeout)
        with self._condition:
            while self._queue.empty() and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._owned:
                    raise Empty("no adapter available")
                self._condition.wait(remaining)
            if self._closed:
                raise RuntimeError("adapter pool is closed")
            adapter = self._queue.get_nowait()
            self._leased.add(id(adapter))
            return adapter

    def release(self, adapter: MootdxAdapter):
        with self._condition:
            if id(adapter) not in self._leased:
                raise ValueError("adapter is not leased from this pool")
            self._leased.remove(id(adapter))
            should_close = self._closed
            if should_close:
                self._owned.pop(id(adapter), None)
            else:
                self._queue.put_nowait(adapter)
            self._condition.notify()
        if should_close:
            adapter.disconnect()

    def close(self):
        idle = []
        with self._condition:
            self._closed = True
            while not self._queue.empty():
                adapter = self._queue.get_nowait()
                self._owned.pop(id(adapter), None)
                idle.append(adapter)
            self._condition.notify_all()
        for adapter in idle:
            adapter.disconnect()

    def failure_summary(self) -> str:
        if not self.failures:
            return ""
        return "; ".join(self.failures[-10:])


def parse_date(value: str | date | None) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return pd.to_datetime(value).date()


def dedupe_symbols(symbols: list[str]) -> list[str]:
    """保持原有顺序去重，避免重复采集同一股票。"""
    return list(dict.fromkeys(symbols))


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.LOCAL_DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def validate_table_name(table_name: str):
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table_name):
        raise ValueError(f"invalid table name: {table_name}")


def validate_target_tables(table_name: str, raw_table_name: str) -> None:
    validate_table_name(table_name)
    validate_table_name(raw_table_name)
    if table_name.casefold() == raw_table_name.casefold():
        raise ValueError("qfq and raw table names must be different")


def init_target_table(conn: sqlite3.Connection, table_name: str):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            symbol VARCHAR(20),
            trade_date DATE,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            amount FLOAT,
            pct_chg FLOAT,
            turnover_rate FLOAT,
            source TEXT DEFAULT 'tdx_mootdx_qfq',
            quality_flag TEXT DEFAULT 'legacy_ok',
            run_id TEXT,
            updated_at TEXT,
            adjustment_mode TEXT DEFAULT 'qfq',
            market TEXT,
            PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_symbol ON {table_name} (symbol)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_date ON {table_name} (trade_date)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_market_date ON {table_name} (market, trade_date)")
    conn.commit()


def init_raw_target_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            symbol VARCHAR(20),
            trade_date DATE,
            close FLOAT,
            source TEXT DEFAULT 'tdx_mootdx_raw',
            quality_flag TEXT DEFAULT 'ok_unadjusted',
            run_id TEXT,
            updated_at TEXT,
            adjustment_mode TEXT DEFAULT 'none',
            market TEXT,
            PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_date "
        f"ON {table_name} (trade_date)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_market_date "
        f"ON {table_name} (market, trade_date)"
    )
    conn.commit()


def filter_symbols_by_market(symbols: list[str], market: str) -> list[str]:
    normalized = str(market or "ALL").upper()
    if normalized == "ALL":
        return symbols
    return [symbol for symbol in symbols if infer_market(symbol) == normalized]


def load_symbols(
    conn: sqlite3.Connection,
    symbol: Optional[str],
    symbols_file: Optional[str],
    limit: Optional[int],
    market: str = "ALL",
) -> list[str]:
    if symbol:
        return filter_symbols_by_market([str(symbol).zfill(6)], market)

    if symbols_file:
        path = Path(symbols_file)
        symbols = dedupe_symbols(
            [
                item.strip().zfill(6)
                for item in path.read_text(encoding="utf-8").splitlines()
                if item.strip() and not item.strip().startswith("#")
            ]
        )
        symbols = [
            item
            for item in symbols
            if is_p0_symbol(item, include_bse=True)
        ]
        symbols = filter_symbols_by_market(symbols, market)
        if limit:
            return symbols[:limit]
        return symbols

    queries = [
        "SELECT symbol FROM stock_info ORDER BY symbol",
        "SELECT DISTINCT symbol FROM stock_sector_map ORDER BY symbol",
    ]

    for query in queries:
        try:
            df = pd.read_sql_query(query, conn)
            if not df.empty:
                symbols = dedupe_symbols(
                    [
                        str(item).zfill(6)
                        for item in df["symbol"].tolist()
                        if is_p0_symbol(str(item).zfill(6), include_bse=True)
                    ]
                )
                symbols = filter_symbols_by_market(symbols, market)
                if limit:
                    return symbols[:limit]
                return symbols
        except Exception:
            continue

    try:
        adapter = MootdxAdapter(timeout=10)
        if adapter.is_available():
            df = adapter.get_stock_list()
            adapter.disconnect()

            if df is not None and not df.empty and "code" in df.columns:
                symbols = dedupe_symbols(
                    [
                        str(item).zfill(6)
                        for item in df["code"].tolist()
                        if is_p0_symbol(str(item).zfill(6), include_bse=True)
                    ]
                )
                symbols = filter_symbols_by_market(symbols, market)
                if limit:
                    return symbols[:limit]
                return symbols
    except Exception:
        pass

    return []


def load_last_dates(conn: sqlite3.Connection, table_name: str) -> dict[str, date]:
    try:
        df = pd.read_sql_query(
            f"SELECT symbol, MAX(trade_date) AS last_date FROM {table_name} GROUP BY symbol",
            conn,
        )
    except Exception:
        return {}

    if df.empty:
        return {}

    df["last_date"] = pd.to_datetime(df["last_date"]).dt.date
    return dict(zip(df["symbol"], df["last_date"]))


def select_resume_symbols(
    conn: sqlite3.Connection,
    table_name: str,
    raw_table_name: str,
    symbols: list[str],
    target: date,
    *, restrict_to_symbols: bool = False,
) -> tuple[list[str], list[str]]:
    """Inspect one indexed date in both tables; never infer completeness from MAX(date)."""
    validate_target_tables(table_name, raw_table_name)
    start, end = target.isoformat(), (target + timedelta(days=1)).isoformat()

    def read_rows(table: str, required: tuple[str, ...]) -> dict[str, list[dict]]:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not set(required).issubset(columns):
            return {}
        rows: dict[str, list[dict]] = {}
        chunks = [symbols[i:i + 500] for i in range(0, len(symbols), 500)] if restrict_to_symbols else [None]
        for chunk in chunks:
            condition = (" AND symbol IN (" + ",".join("?" for _ in chunk) + ")"
                         if chunk is not None else "")
            cursor = conn.execute(
                f"SELECT {','.join(required)} FROM {table} WHERE trade_date>=? AND trade_date<?" + condition,
                (start, end, *(chunk or [])),
            )
            for values in cursor:
                row = dict(zip(required, values))
                rows.setdefault(str(row["symbol"]), []).append(row)
        return rows

    adjusted = read_rows(table_name, (
        "symbol", "open", "high", "low", "close", "volume", "amount",
        "turnover_rate", "source", "adjustment_mode", "quality_flag",
    ))
    raw = read_rows(raw_table_name, (
        "symbol", "close", "source", "adjustment_mode", "quality_flag",
    ))

    def finite(value: object, *, positive: bool = False) -> bool:
        try:
            number = float(value)
            return math.isfinite(number) and (number > 0 if positive else number >= 0)
        except (TypeError, ValueError, OverflowError):
            return False

    accepted_flags = {"ok", "ok_unadjusted", "legacy_ok", "pass"}
    complete = []
    pending = []
    for symbol in dedupe_symbols(symbols):
        price_rows, raw_rows = adjusted.get(symbol, []), raw.get(symbol, [])
        valid = len(price_rows) == len(raw_rows) == 1
        if valid:
            price, unadjusted = price_rows[0], raw_rows[0]
            bse = infer_market(symbol) == MARKET_BSE
            valid = (
                all(finite(price[key], positive=True) for key in ("open", "high", "low", "close"))
                and all(finite(price[key]) for key in ("volume", "amount"))
                and finite(unadjusted["close"], positive=True)
                and price["source"] == (TDX_STANDARD_BJ_SOURCE_NAME if bse else SOURCE_NAME)
                and price["adjustment_mode"] == ("none" if bse else "qfq")
                and unadjusted["source"] == RAW_SOURCE_NAME
                and unadjusted["adjustment_mode"] == "none"
                and math.isclose(float(price["close"]), float(unadjusted["close"]),
                                 rel_tol=1e-5, abs_tol=0.011)
                and str(price["quality_flag"]).lower() in accepted_flags
                and str(unadjusted["quality_flag"]).lower() in accepted_flags
            )
            if valid:
                low, high = float(price["low"]), float(price["high"])
                valid = (
                    low <= float(price["open"]) <= high
                    and low <= float(price["close"]) <= high
                    and (bse or float(price["volume"]) == 0
                         or finite(price["turnover_rate"], positive=True))
                )
        (complete if valid else pending).append(symbol)
    return pending, complete


def choose_start_date(
    default_start: date,
    end_date: date,
    last_date: Optional[date],
    xdxr_df: Optional[pd.DataFrame],
    lookback_days: int,
    full_refresh: bool,
    force_window: bool = False,
) -> Optional[date]:
    if full_refresh or last_date is None:
        return default_start

    latest_event_date = None
    if xdxr_df is not None and not xdxr_df.empty and "trade_date" in xdxr_df.columns:
        latest_event_date = pd.to_datetime(xdxr_df["trade_date"]).dt.date.max()

    if last_date >= end_date:
        if latest_event_date == end_date:
            return default_start
        if force_window:
            return max(default_start, end_date - timedelta(days=max(1, lookback_days)))
        return None

    if latest_event_date is not None and latest_event_date > last_date and latest_event_date >= default_start:
        return default_start

    return max(default_start, last_date - timedelta(days=lookback_days))


def choose_request_max_bars(
    default_start: date,
    end_date: date,
    start_date: date,
    last_date: Optional[date],
    full_refresh: bool,
    force_window: bool,
    max_bars: Optional[int],
) -> Optional[int]:
    """Bound recent exact-day repairs without truncating older historical targets."""
    if (not full_refresh and force_window and default_start == end_date
            and max_bars is not None and max_bars > 0
            and 0 <= (date.today() - end_date).days <= max_bars - 2):
        # At most one daily bar per calendar day, plus a seed-bar margin.
        return max_bars
    if full_refresh or last_date is None or start_date == default_start:
        return None
    return max_bars


def seed_incremental_pct_chg(data: pd.DataFrame, previous_close: object) -> pd.DataFrame:
    """Use the last stored close when an incremental source window has no seed bar."""
    if data is None or data.empty or "close" not in data.columns:
        return data

    seeded = data.sort_values("trade_date").copy()
    if "pct_chg" not in seeded.columns:
        seeded["pct_chg"] = None

    prior = pd.to_numeric(pd.Series([previous_close]), errors="coerce").iloc[0]
    first_index = seeded.index[0]
    first_close = pd.to_numeric(pd.Series([seeded.at[first_index, "close"]]), errors="coerce").iloc[0]
    if pd.isna(seeded.at[first_index, "pct_chg"]) and pd.notna(prior) and prior > 0 and pd.notna(first_close):
        seeded.at[first_index, "pct_chg"] = (float(first_close) / float(prior) - 1.0) * 100.0
    return seeded


def source_anchor_close(raw_data: pd.DataFrame, last_date: Optional[date]) -> Optional[float]:
    """Read the source's overlapping raw close before filtering to new rows."""
    if last_date is None or raw_data is None or raw_data.empty:
        return None
    dates = pd.to_datetime(raw_data["trade_date"], errors="coerce").dt.date
    anchor = pd.to_numeric(raw_data.loc[dates == last_date, "close"], errors="coerce")
    if anchor.empty or pd.isna(anchor.iloc[-1]) or anchor.iloc[-1] <= 0:
        return None
    return float(anchor.iloc[-1])


def fetch_one_symbol(
    symbol: str,
    end_date: date,
    default_start: date,
    last_date: Optional[date],
    lookback_days: int,
    full_refresh: bool,
    pool: AdapterPool | None,
    max_bars: int | None,
    bse_pool: object | None = None,
    force_window: bool = False,
) -> FetchResult:
    market = infer_market(symbol)
    if not full_refresh and not force_window and last_date is not None and last_date >= end_date:
        return FetchResult(symbol=symbol, status="skip", market=market, reason="up-to-date")

    if market == MARKET_BSE:
        if pool is None:
            return FetchResult(
                symbol=symbol,
                status="fail",
                market=market,
                source=TDX_STANDARD_BJ_SOURCE_NAME,
                reason="no standard TDX adapter pool for BSE market=2",
            )

        try:
            adapter = pool.acquire()
        except Exception as exc:
            return FetchResult(
                symbol=symbol,
                status="fail",
                market=market,
                source=TDX_STANDARD_BJ_SOURCE_NAME,
                reason=f"acquire BSE standard TDX adapter failed: {exc}",
            )

        try:
            start_date = choose_start_date(
                default_start=default_start,
                end_date=end_date,
                last_date=last_date,
                xdxr_df=None,
                lookback_days=lookback_days,
                full_refresh=full_refresh,
                force_window=force_window,
            )
            if start_date is None:
                return FetchResult(
                    symbol=symbol,
                    status="skip",
                    market=market,
                    source=TDX_STANDARD_BJ_SOURCE_NAME,
                    reason="up-to-date",
                )

            request_max_bars = choose_request_max_bars(
                default_start, end_date, start_date, last_date,
                full_refresh, force_window, max_bars,
            )
            raw_df = adapter.get_stock_kline(
                symbol=symbol,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                adjust="none",
                max_bars=request_max_bars,
            )
            if raw_df is None or raw_df.empty:
                reason = (
                    "source unavailable"
                    if getattr(adapter, "last_failure_kind", None) == "connectivity"
                    else "no raw data"
                )
                return FetchResult(
                    symbol=symbol,
                    status="fail",
                    market=market,
                    source=TDX_STANDARD_BJ_SOURCE_NAME,
                    reason=reason,
                )

            raw_output_df = raw_df.copy()
            anchor_raw_close = source_anchor_close(raw_output_df, last_date)
            raw_output_df["symbol"] = symbol
            raw_output_df["trade_date"] = pd.to_datetime(
                raw_output_df["trade_date"]
            ).dt.date
            raw_output_df["source"] = RAW_SOURCE_NAME
            raw_output_df["quality_flag"] = "ok_unadjusted"
            raw_output_df["run_id"] = CURRENT_RUN_ID
            raw_output_df["updated_at"] = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            raw_output_df["adjustment_mode"] = "none"
            raw_output_df["market"] = market
            raw_output_df = raw_output_df[RAW_SAVE_COLUMNS].copy()

            if "pct_chg" not in raw_df.columns:
                raw_df["pct_chg"] = raw_df["close"].pct_change() * 100 if "close" in raw_df.columns else None
            if "turnover_rate" not in raw_df.columns:
                raw_df["turnover_rate"] = None
            raw_df["symbol"] = symbol
            raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"]).dt.date
            raw_df["source"] = TDX_STANDARD_BJ_SOURCE_NAME
            raw_df["quality_flag"] = "ok_unadjusted"
            raw_df["run_id"] = CURRENT_RUN_ID
            raw_df["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            raw_df["adjustment_mode"] = "none"
            raw_df["market"] = market
            raw_df = raw_df[[column for column in SAVE_COLUMNS if column in raw_df.columns]].copy()
            write_policy = "replace_window" if full_refresh else ("repair" if force_window else "new_only")
            if write_policy == "new_only" and last_date is not None:
                raw_df = raw_df[raw_df["trade_date"] > last_date].copy()
                raw_output_df = raw_output_df[
                    raw_output_df["trade_date"] > last_date
                ].copy()
            if raw_df.empty:
                return FetchResult(
                    symbol=symbol,
                    status="skip",
                    market=market,
                    source=TDX_STANDARD_BJ_SOURCE_NAME,
                    reason="no new rows after seed filtering",
                )

            return FetchResult(
                symbol=symbol,
                status="success",
                market=market,
                source=TDX_STANDARD_BJ_SOURCE_NAME,
                data=raw_df,
                raw_data=raw_output_df,
                start_date=raw_df["trade_date"].min(),
                end_date=raw_df["trade_date"].max(),
                write_policy=write_policy,
                anchor_date=last_date if anchor_raw_close is not None else None,
                anchor_raw_close=anchor_raw_close,
            )

        except Exception as exc:
            return FetchResult(
                symbol=symbol,
                status="fail",
                market=market,
                source=TDX_STANDARD_BJ_SOURCE_NAME,
                reason=str(exc),
            )
        finally:
            pool.release(adapter)

    if pool is None:
        return FetchResult(symbol=symbol, status="fail", market=market, reason="no standard TDX adapter pool")

    try:
        adapter = pool.acquire()
    except Exception as exc:
        return FetchResult(
            symbol=symbol,
            status="fail",
            market=market,
            reason=f"acquire adapter failed: {exc}",
        )

    try:
        xdxr_df = adapter.get_xdxr_info(symbol)
        if getattr(adapter, "last_failure_kind", None) == "connectivity":
            return FetchResult(
                symbol=symbol,
                status="fail",
                market=market,
                reason="source unavailable",
            )
        start_date = choose_start_date(
            default_start=default_start,
            end_date=end_date,
            last_date=last_date,
            xdxr_df=xdxr_df,
            lookback_days=lookback_days,
            full_refresh=full_refresh,
            force_window=force_window,
        )

        if start_date is None:
            return FetchResult(symbol=symbol, status="skip", market=market, reason="up-to-date")

        request_max_bars = choose_request_max_bars(
            default_start, end_date, start_date, last_date,
            full_refresh, force_window, max_bars,
        )
        raw_df = adapter.get_stock_kline(
            symbol=symbol,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            adjust="none",
            max_bars=request_max_bars,
        )
        if raw_df is None or raw_df.empty:
            reason = (
                "source unavailable"
                if getattr(adapter, "last_failure_kind", None) == "connectivity"
                else "no raw data"
            )
            return FetchResult(symbol=symbol, status="fail", market=market, reason=reason)

        raw_output_df = raw_df.copy()
        anchor_raw_close = source_anchor_close(raw_output_df, last_date)
        raw_output_df["symbol"] = symbol
        raw_output_df["trade_date"] = pd.to_datetime(
            raw_output_df["trade_date"]
        ).dt.date
        raw_output_df["source"] = RAW_SOURCE_NAME
        raw_output_df["quality_flag"] = "ok_unadjusted"
        raw_output_df["run_id"] = CURRENT_RUN_ID
        raw_output_df["updated_at"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        raw_output_df["adjustment_mode"] = "none"
        raw_output_df["market"] = market
        raw_output_df = raw_output_df[RAW_SAVE_COLUMNS].copy()

        qfq_df = apply_mootdx_qfq(raw_df, xdxr_df)
        if qfq_df is None or qfq_df.empty:
            return FetchResult(symbol=symbol, status="fail", market=market, reason="qfq empty")

        if "turnover_rate" not in qfq_df.columns:
            qfq_df["turnover_rate"] = None

        qfq_df["symbol"] = symbol
        qfq_df["trade_date"] = pd.to_datetime(qfq_df["trade_date"]).dt.date
        qfq_df["source"] = SOURCE_NAME
        qfq_df["quality_flag"] = "ok"
        qfq_df["run_id"] = CURRENT_RUN_ID
        qfq_df["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        qfq_df["adjustment_mode"] = "qfq"
        qfq_df["market"] = market
        qfq_df = qfq_df[[column for column in SAVE_COLUMNS if column in qfq_df.columns]].copy()
        rebuild_required = bool(
            full_refresh
            or (
                last_date is not None
                and not force_window
                and start_date == default_start
            )
        )
        write_policy = (
            "replace_window"
            if rebuild_required
            else ("repair" if force_window else "new_only")
        )
        if write_policy == "new_only" and last_date is not None:
            qfq_df = qfq_df[qfq_df["trade_date"] > last_date].copy()
            raw_output_df = raw_output_df[
                raw_output_df["trade_date"] > last_date
            ].copy()
        if qfq_df.empty:
            return FetchResult(
                symbol=symbol,
                status="skip",
                market=market,
                reason="no new rows after seed filtering",
            )

        return FetchResult(
            symbol=symbol,
            status="success",
            market=market,
            data=qfq_df,
            raw_data=raw_output_df,
            start_date=qfq_df["trade_date"].min(),
            end_date=qfq_df["trade_date"].max(),
            write_policy=write_policy,
            anchor_date=last_date if anchor_raw_close is not None else None,
            anchor_raw_close=anchor_raw_close,
        )

    except Exception as exc:
        return FetchResult(symbol=symbol, status="fail", market=market, reason=str(exc))
    finally:
        pool.release(adapter)


def save_batch(
    conn: sqlite3.Connection,
    table_name: str,
    results: list[FetchResult],
    *,
    manage_transaction: bool = True,
) -> int:
    if not results:
        return 0

    delete_ranges = []
    delete_days = []
    delete_keys = []
    insert_rows = []

    for result in results:
        if result.data is None or result.data.empty:
            continue

        data = result.data
        if result.write_policy in {"new_only", "replace_window", "repair", "resume_target"}:
            previous = conn.execute(
                f"""
                SELECT close
                FROM {table_name}
                WHERE symbol = ? AND trade_date < ? AND close > 0
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (result.symbol, result.start_date.isoformat()),
            ).fetchone()
            data = seed_incremental_pct_chg(data, previous[0] if previous else None)
        if result.write_policy == "resume_target":
            delete_days.append((result.symbol, result.end_date.isoformat(),
                                (result.end_date + timedelta(days=1)).isoformat()))
        elif result.write_policy == "repair":
            existing_rows = conn.execute(
                f"""
                SELECT trade_date, open, close, high, low, volume, amount,
                       turnover_rate, pct_chg, source, adjustment_mode
                FROM {table_name}
                WHERE symbol = ? AND trade_date BETWEEN ? AND ?
                """,
                (
                    result.symbol,
                    result.start_date.isoformat(),
                    result.end_date.isoformat(),
                ),
            ).fetchall()
            existing_by_date = {str(row[0])[:10]: row for row in existing_rows}

            def needs_repair(row) -> bool:
                existing = existing_by_date.get(row.trade_date.isoformat())
                if existing is None:
                    return True
                (
                    _, open_, close, high, low, volume, amount,
                    turnover, pct_chg, source, adjustment,
                ) = existing
                invalid_price = any(value is None or float(value) <= 0 for value in (open_, close, high, low))
                invalid_range = bool(
                    high is not None
                    and low is not None
                    and close is not None
                    and (float(high) < float(low) or float(close) < float(low) or float(close) > float(high))
                )
                invalid_amount = volume is None or float(volume) < 0 or amount is None or float(amount) < 0
                bse_turnover_exempt = (
                    result.market == MARKET_BSE
                    and source in {TDX_STANDARD_BJ_SOURCE_NAME, "tdx_exhq"}
                    and adjustment == "none"
                )
                incoming_pct_chg = getattr(row, "pct_chg", None)
                recoverable_pct_chg = pct_chg is None and pd.notna(incoming_pct_chg)
                return (
                    invalid_price
                    or invalid_range
                    or invalid_amount
                    or recoverable_pct_chg
                    or (turnover is None and not bse_turnover_exempt)
                )

            repair_mask = [needs_repair(row) for row in data.itertuples(index=False)]
            data = data.loc[repair_mask].copy()
            delete_keys.extend(
                (result.symbol, value.isoformat())
                for value in data["trade_date"].tolist()
            )
        else:
            delete_ranges.append(
                (
                    result.symbol,
                    result.start_date.isoformat(),
                    result.end_date.isoformat(),
                )
            )

        for row in data.itertuples(index=False):
            insert_rows.append(
                (
                    row.symbol,
                    row.trade_date.isoformat() if hasattr(row.trade_date, "isoformat") else str(row.trade_date),
                    row.open,
                    row.close,
                    row.high,
                    row.low,
                    row.volume,
                    row.amount,
                    row.pct_chg,
                    row.turnover_rate,
                    row.source,
                    row.quality_flag,
                    row.run_id,
                    row.updated_at,
                    row.adjustment_mode,
                    row.market,
                )
            )

    if not insert_rows:
        return 0

    def write_rows() -> None:
        if delete_days:
            conn.executemany(
                f"DELETE FROM {table_name} WHERE symbol=? AND trade_date>=? AND trade_date<?",
                delete_days,
            )
        if delete_ranges:
            conn.executemany(
                f"DELETE FROM {table_name} WHERE symbol = ? AND trade_date BETWEEN ? AND ?",
                delete_ranges,
            )
        if delete_keys:
            conn.executemany(
                f"DELETE FROM {table_name} WHERE symbol = ? AND trade_date = ?",
                delete_keys,
            )
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO {table_name} (
                symbol, trade_date, open, close, high, low,
                volume, amount, pct_chg, turnover_rate,
                source, quality_flag, run_id, updated_at, adjustment_mode, market
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_rows,
        )

    if manage_transaction:
        with conn:
            write_rows()
    else:
        write_rows()

    return len(insert_rows)


def save_raw_batch(
    conn: sqlite3.Connection,
    table_name: str,
    results: list[FetchResult],
    *,
    manage_transaction: bool = True,
) -> int:
    if not results:
        return 0

    delete_ranges: list[tuple[str, str, str]] = []
    delete_days: list[tuple[str, str, str]] = []
    delete_keys: list[tuple[str, str]] = []
    insert_rows = []

    for result in results:
        if result.raw_data is None or result.raw_data.empty:
            continue
        data = result.raw_data
        if result.write_policy == "resume_target":
            delete_days.append((result.symbol, result.end_date.isoformat(),
                                (result.end_date + timedelta(days=1)).isoformat()))
        elif result.write_policy == "repair":
            delete_keys.extend(
                (result.symbol, value.isoformat())
                for value in data["trade_date"].tolist()
            )
        else:
            delete_ranges.append(
                (
                    result.symbol,
                    result.start_date.isoformat(),
                    result.end_date.isoformat(),
                )
            )

        for row in data.itertuples(index=False):
            insert_rows.append(
                (
                    row.symbol,
                    (
                        row.trade_date.isoformat()
                        if hasattr(row.trade_date, "isoformat")
                        else str(row.trade_date)
                    ),
                    row.close,
                    row.source,
                    row.quality_flag,
                    row.run_id,
                    row.updated_at,
                    row.adjustment_mode,
                    row.market,
                )
            )

    if not insert_rows:
        return 0

    def write_rows() -> None:
        if delete_days:
            conn.executemany(
                f"DELETE FROM {table_name} WHERE symbol=? AND trade_date>=? AND trade_date<?",
                delete_days,
            )
        if delete_ranges:
            conn.executemany(
                f"""
                DELETE FROM {table_name}
                WHERE symbol = ? AND trade_date BETWEEN ? AND ?
                """,
                delete_ranges,
            )
        if delete_keys:
            conn.executemany(
                f"DELETE FROM {table_name} WHERE symbol = ? AND trade_date = ?",
                delete_keys,
            )
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO {table_name} (
                symbol, trade_date, close, source, quality_flag,
                run_id, updated_at, adjustment_mode, market
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_rows,
        )

    if manage_transaction:
        with conn:
            write_rows()
    else:
        write_rows()
    return len(insert_rows)


def validate_batch_results(results: list[FetchResult]) -> None:
    """Reject an active-trading OHLCV payload cloned across different symbols."""
    fingerprints: dict[tuple[object, ...], set[str]] = {}
    required = {"trade_date", "open", "high", "low", "close", "volume", "amount"}

    for result in results:
        if result.data is None or result.data.empty or not required.issubset(result.data.columns):
            continue
        for row in result.data.itertuples(index=False):
            values = [
                getattr(row, "open", None),
                getattr(row, "high", None),
                getattr(row, "low", None),
                getattr(row, "close", None),
                getattr(row, "volume", None),
                getattr(row, "amount", None),
            ]
            numeric = pd.to_numeric(pd.Series(values), errors="coerce")
            if numeric.isna().any() or float(numeric.iloc[4]) < 1000 or float(numeric.iloc[5]) <= 0:
                continue
            fingerprint = (
                str(getattr(row, "trade_date", ""))[:10],
                *(round(float(value), 4) for value in numeric),
            )
            fingerprints.setdefault(fingerprint, set()).add(result.symbol)

    cloned = []
    for fingerprint, symbols in fingerprints.items():
        close = float(fingerprint[4])
        threshold = 2 if close >= 100 else 3
        if len(symbols) >= threshold:
            cloned.append((fingerprint, sorted(symbols)))

    if cloned:
        fingerprint, symbols = cloned[0]
        raise ValueError(
            "cross-symbol cloned OHLCV detected: "
            f"date={fingerprint[0]}, symbols={','.join(symbols[:10])}, count={len(symbols)}"
        )


def save_dual_batch(
    conn: sqlite3.Connection,
    table_name: str,
    raw_table_name: str,
    results: list[FetchResult],
) -> tuple[int, int]:
    validate_target_tables(table_name, raw_table_name)
    validate_batch_results(results)
    for result in results:
        resuming_target = result.write_policy == "resume_target"
        if resuming_target:
            if result.start_date != result.end_date or result.end_date is None:
                raise ValueError("resume target validation requires one date")
            for data in (result.data, result.raw_data):
                if (data is None or len(data) != 1
                        or set(data["symbol"].astype(str)) != {result.symbol}
                        or set(pd.to_datetime(data["trade_date"]).dt.date) != {result.end_date}):
                    raise ValueError("resume target data validation failed")
        if (result.anchor_date is not None and result.anchor_raw_close is not None
                and not (resuming_target and result.anchor_date == result.end_date)):
            existing_anchor = conn.execute(
                f"SELECT close FROM {raw_table_name} WHERE symbol = ? AND trade_date = ?",
                (result.symbol, result.anchor_date.isoformat()),
            ).fetchone()
            if existing_anchor is not None and existing_anchor[0] is not None:
                prior_close = float(existing_anchor[0])
                if prior_close > 0 and abs(result.anchor_raw_close / prior_close - 1.0) > 0.005:
                    raise ValueError(
                        "source anchor mismatch: "
                        f"symbol={result.symbol}, date={result.anchor_date}, "
                        f"source={result.anchor_raw_close}, stored={prior_close}"
                    )
        has_qfq = result.data is not None and not result.data.empty
        has_raw = result.raw_data is not None and not result.raw_data.empty
        if has_qfq != has_raw:
            raise ValueError(
                f"{result.symbol} must provide both qfq data and raw_data"
            )

    with conn:
        qfq_rows = save_batch(
            conn,
            table_name,
            results,
            manage_transaction=False,
        )
        raw_rows = save_raw_batch(
            conn,
            raw_table_name,
            results,
            manage_transaction=False,
        )
        for target in {r.end_date for r in results if r.write_policy == "resume_target"}:
            symbols = [r.symbol for r in results if r.write_policy == "resume_target" and r.end_date == target]
            pending, _ = select_resume_symbols(
                conn, table_name, raw_table_name, symbols, target, restrict_to_symbols=True,
            )
            if pending:
                raise ValueError("resume post-write validation failed: " + ",".join(pending[:10]))
    return qfq_rows, raw_rows


def failed_markets(results: list[FetchResult]) -> list[str]:
    """Reject wholly failed markets, while allowing valid incremental skips."""
    counts: dict[str, dict[str, int]] = {}
    for result in results:
        item = counts.setdefault(result.market, {"fail": 0, "valid": 0})
        if result.status == "fail":
            item["fail"] += 1
        elif result.status == "success" or (
            result.status == "skip"
            and result.reason in {"up-to-date", "no new rows after seed filtering"}
        ):
            item["valid"] += 1
    return sorted(
        market for market, item in counts.items()
        if item["fail"] > 0 and item["valid"] == 0
    )


def expected_market_counts(symbols: list[str]) -> dict[str, int]:
    counts = {market: 0 for market in P0_MARKETS}
    for symbol in symbols:
        market = infer_market(symbol)
        counts[market] = counts.get(market, 0) + 1
    return counts


def format_market_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{market}={counts.get(market, 0)}" for market in P0_MARKETS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect stock K-line with mootdx local qfq")
    parser.add_argument("--symbol", help="single stock symbol")
    parser.add_argument("--symbols-file", help="newline separated stock symbols for targeted repair")
    parser.add_argument("--limit", type=int, help="limit symbol count for testing")
    parser.add_argument("--start", default=DEFAULT_START_DATE, help="default start date")
    parser.add_argument("--end", default=date.today().isoformat(), help="end date")
    parser.add_argument("--workers", type=int, default=8, help="worker threads")
    parser.add_argument("--pool-size", type=int, default=8, help="mootdx adapter pool size")
    parser.add_argument("--timeout", type=int, default=10, help="adapter timeout seconds")
    parser.add_argument("--connect-retries", type=int, default=3, help="adapter startup retries")
    parser.add_argument(
        "--connect-retry-delay",
        type=float,
        default=2.0,
        help="seconds between adapter startup retries",
    )
    parser.add_argument(
        "--max-fail-rate",
        type=float,
        default=0.02,
        help="fail the command when symbol failure rate is above this value",
    )
    parser.add_argument(
        "--source-outage-threshold",
        type=int,
        default=8,
        help="stop after this many consecutive whole-source connectivity failures",
    )
    parser.add_argument("--batch-size", type=int, default=50, help="save batch size in symbols")
    parser.add_argument("--lookback-days", type=int, default=5, help="incremental lookback days")
    parser.add_argument("--max-bars", type=int, default=DEFAULT_MAX_BARS, help="recent bars fetched for incremental updates")
    parser.add_argument("--submit-window", type=int, default=256, help="max pending futures")
    parser.add_argument("--force-window", action="store_true", help="fetch recent window even when a symbol is already up to date")
    parser.add_argument("--resume-target-day", action="store_true",
                        help="skip verified raw/qfq pairs for the latest completed session")
    parser.add_argument("--market", choices=["ALL", "SH", "SZ", "BSE"], default="ALL", help="market filter for smoke/debug")
    parser.add_argument("--diagnostic-only", action="store_true", help="fetch and log source results without writing K-line rows")
    parser.add_argument("--result-json", help="optional JSON path for per-symbol fetch results")
    parser.add_argument("--table", default="stock_daily_prices", help="target table name")
    parser.add_argument(
        "--raw-table",
        default="stock_daily_prices_raw",
        help="compact unadjusted close table name",
    )
    parser.add_argument("--full-refresh", action="store_true", help="always recompute from start date")
    parser.add_argument("--run-id", default=datetime.now().strftime("kline_%Y%m%d_%H%M%S"))
    return parser


CURRENT_RUN_ID = datetime.now().strftime("kline_%Y%m%d_%H%M%S")


def log_source_summary(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    trade_date: str,
    results: list[FetchResult],
    duration: float,
) -> None:
    stats = {
        (SOURCE_NAME if market != MARKET_BSE else TDX_STANDARD_BJ_SOURCE_NAME, market): {
            "success": 0,
            "fail": 0,
            "skip": 0,
            "non_data_fail": 0,
            "errors": [],
        }
        for market in P0_MARKETS
    }
    for result in results:
        key = (result.source, result.market)
        market_stats = stats.setdefault(
            key, {"success": 0, "fail": 0, "skip": 0, "non_data_fail": 0, "errors": []},
        )
        if result.status == "success":
            market_stats["success"] += 1
        elif result.status == "fail":
            market_stats["fail"] += 1
            market_stats["non_data_fail"] += int(result.reason != "no raw data")
            if result.reason and len(market_stats["errors"]) < 10:
                market_stats["errors"].append(f"{result.symbol}:{result.reason}")
        else:
            market_stats["skip"] += 1

    for (source, market), item in stats.items():
        processed = item["success"] + item["fail"]
        fail_rate = item["fail"] / processed if processed else 0.0
        status = "PASS" if item["fail"] == 0 else ("WARNING" if fail_rate <= 0.02 else "FAIL")
        if item["fail"] and not item["non_data_fail"]:
            status = "WARNING"
        log_data_source_run(
            conn,
            run_id=run_id,
            trade_date=trade_date,
            source=source,
            api_name="stock_daily_bj_standard" if source == TDX_STANDARD_BJ_SOURCE_NAME else "stock_daily_qfq",
            market=market,
            status=status,
            success_count=int(item["success"]),
            fail_count=int(item["fail"]),
            error_message="; ".join(item["errors"]) if item["errors"] else None,
            end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            duration=duration,
            detail_json=json.dumps(item, ensure_ascii=False),
        )


@dataclass
class CollectionOutcome:
    results: list[FetchResult]
    saved_rows: int
    raw_saved_rows: int
    elapsed: float
    outage: bool
    recoveries: int


def collect_with_recovery(
    args: argparse.Namespace,
    conn: sqlite3.Connection,
    symbols: list[str],
    last_dates: dict[str, date],
    pool: AdapterPool | None,
    default_start: date,
    end_date: date,
    complete_symbols: list[str] | None = None,
) -> CollectionOutcome:
    """Drain workers and commit successes before one coordinator replaces the pool."""
    started = time.monotonic()
    results = {
        symbol: FetchResult(
            symbol, "skip", infer_market(symbol),
            source=TDX_STANDARD_BJ_SOURCE_NAME if infer_market(symbol) == MARKET_BSE else SOURCE_NAME,
            reason="up-to-date",
        )
        for symbol in (complete_symbols or [])
    }
    counts = Counter(result.status for result in results.values())
    saved_rows = raw_saved_rows = recoveries = 0
    remaining = list(symbols)
    outage = False
    pending_save: list[FetchResult] = []

    def flush() -> None:
        nonlocal saved_rows, raw_saved_rows, pending_save
        if pending_save and not args.diagnostic_only:
            saved, raw_saved = save_dual_batch(conn, args.table, args.raw_table, pending_save)
            saved_rows += saved
            raw_saved_rows += raw_saved
            pending_save = []

    try:
        with tqdm(total=len(symbols) + len(results), initial=len(results),
                  desc="collect", unit="symbol", mininterval=1.0) as progress:
            while remaining:
                if pool is None or pool.available == 0:
                    outage = True
                    break
                workers = max(1, min(max(1, args.workers), pool.available))
                submit_window = max(workers, min(8, int(args.submit_window or workers)))
                breaker = SourceOutageCircuitBreaker(args.source_outage_threshold)
                next_index = 0
                paused = False
                in_flight: dict[Future, str] = {}

                with ThreadPoolExecutor(max_workers=workers) as executor:
                    while next_index < len(remaining) or in_flight:
                        while (not paused and next_index < len(remaining)
                               and len(in_flight) < submit_window):
                            symbol = remaining[next_index]
                            future = executor.submit(
                                fetch_one_symbol, symbol, end_date, default_start,
                                last_dates.get(symbol), args.lookback_days, args.full_refresh,
                                pool, max(1, args.max_bars) if args.max_bars else None,
                                bse_pool=None, force_window=args.force_window,
                            )
                            in_flight[future] = symbol
                            next_index += 1
                        if not in_flight:
                            break
                        done, _ = wait(set(in_flight), return_when=FIRST_COMPLETED)
                        for future in done:
                            in_flight.pop(future)
                            result = future.result()
                            if result.status == "success" and getattr(args, "resume_target_day", False):
                                for attribute in ("data", "raw_data"):
                                    data = getattr(result, attribute)
                                    if data is None:
                                        raise ValueError("resume target data validation failed")
                                    selected = data.loc[pd.to_datetime(data["trade_date"]).dt.date == end_date].copy()
                                    if len(selected) != 1:
                                        raise ValueError("resume target data validation failed")
                                    setattr(result, attribute, selected)
                                result.start_date = result.end_date = end_date
                                result.write_policy = "resume_target"
                            previous = results.get(result.symbol)
                            if previous is not None:
                                counts[previous.status] -= 1
                            counts[result.status] += 1
                            # Keep only status metadata after each bounded save batch.
                            results[result.symbol] = FetchResult(
                                result.symbol, result.status, result.market,
                                source=result.source, reason=result.reason,
                            )
                            if result.status == "success" and not args.diagnostic_only:
                                pending_save.append(result)
                                if len(pending_save) >= max(1, args.batch_size):
                                    flush()
                            if breaker.observe(result):
                                paused = True
                                next_index = len(remaining)
                                for pending in list(in_flight):
                                    if pending.cancel():
                                        in_flight.pop(pending)
                            progress.update(int(previous is None))
                            elapsed = max(time.monotonic() - started, 1e-6)
                            progress.set_postfix(
                                success=counts["success"], fail=counts["fail"], skip=counts["skip"],
                                rows=saved_rows,
                                sym_s=f"{(counts['success'] + counts['fail']) / elapsed:.2f}",
                                refresh=False,
                            )
                flush()
                retry = [symbol for symbol in remaining if symbol not in results
                         or results[symbol].reason == "source unavailable"]
                outage = bool(retry)
                if not retry or recoveries >= 1:
                    break
                # No old lease is active here. Discovery runs once, not once per worker.
                pool.close()
                recoveries += 1
                print(f"source_recovery=begin round={recoveries} remaining={len(retry)} "
                      f"budget_seconds={AdapterPool.STARTUP_BUDGET_SECONDS}", flush=True)
                pool = AdapterPool(
                    size=max(1, args.pool_size), timeout=args.timeout,
                    max_retries=1, retry_delay=0,
                )
                if pool.available == 0:
                    print("source_recovery=failed remaining_deferred=" + str(len(retry)), flush=True)
                    break
                print(f"source_recovery=connected adapters={pool.available}", flush=True)
                remaining = retry
        return CollectionOutcome(
            list(results.values()), saved_rows, raw_saved_rows,
            time.monotonic() - started, outage, recoveries,
        )
    finally:
        if pool is not None:
            pool.close()


def validate_resume_request(start: date, end: date, full_refresh: bool,
                            now: datetime | None = None) -> None:
    current = now or datetime.now()
    completed = current.date()
    if (current.hour, current.minute) < (15, 30):
        completed -= timedelta(days=1)
    while not is_a_share_session(completed):
        completed -= timedelta(days=1)
    if full_refresh or start != end or end != completed:
        raise ValueError("resume-target-day requires the latest completed single session; "
                         "use the anchored gap repair path for older history")


def main():
    args = build_parser().parse_args()
    validate_target_tables(args.table, args.raw_table)
    global CURRENT_RUN_ID
    CURRENT_RUN_ID = args.run_id

    default_start = parse_date(args.start)
    end_date = parse_date(args.end)

    if default_start is None or end_date is None:
        raise ValueError("invalid start/end date")
    if default_start > end_date:
        raise ValueError("start date must be <= end date")
    if args.resume_target_day:
        validate_resume_request(default_start, end_date, args.full_refresh)
        if not args.force_window or not args.max_bars or not 2 <= args.max_bars <= DEFAULT_MAX_BARS:
            raise ValueError("resume-target-day requires --force-window and bounded seed bars")

    ensure_data_core_schema(settings.LOCAL_DB_PATH)
    read_conn = get_db_connection()
    init_target_table(read_conn, args.table)
    init_raw_target_table(read_conn, args.raw_table)

    symbols = load_symbols(read_conn, args.symbol, args.symbols_file, args.limit, args.market)
    if not symbols:
        print("No symbols found.")
        read_conn.close()
        return 1

    complete_symbols = []
    all_symbol_count = len(symbols)
    market_counts = expected_market_counts(symbols)
    if args.resume_target_day:
        symbols, complete_symbols = select_resume_symbols(
            read_conn, args.table, args.raw_table, symbols, end_date,
        )
        last_dates = {}
        # Read only one indexed seed date per candidate, not a whole-history GROUP BY.
        for symbol in symbols:
            row = read_conn.execute(
                f"SELECT trade_date FROM {args.table} WHERE symbol=? AND trade_date<? "
                "ORDER BY trade_date DESC LIMIT 1",
                (symbol, (end_date + timedelta(days=1)).isoformat()),
            ).fetchone()
            if row:
                last_dates[symbol] = parse_date(row[0])
        print(f"resume_target_day={end_date} total={all_symbol_count} "
              f"complete={len(complete_symbols)} pending={len(symbols)}", flush=True)
    else:
        last_dates = load_last_dates(read_conn, args.table)
    needs_standard_tdx = any(infer_market(symbol) in P0_MARKETS for symbol in symbols)
    requested_pool_size = max(1, args.pool_size)
    pool: AdapterPool | None = None
    if needs_standard_tdx:
        pool = AdapterPool(
            size=requested_pool_size,
            timeout=args.timeout,
            max_retries=max(1, args.connect_retries),
            retry_delay=max(0.0, args.connect_retry_delay),
        )
    if needs_standard_tdx and pool is not None and pool.available == 0:
        print(f"adapter failures: {pool.failure_summary()}")
        log_data_source_run(
            read_conn,
            run_id=args.run_id,
            trade_date=end_date.isoformat(),
            source=SOURCE_NAME,
            api_name="adapter_pool",
            market="ALL",
            status="FAIL",
            success_count=0,
            fail_count=len(symbols),
            error_message=f"no available TDX connections: {pool.failure_summary()}",
            end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        read_conn.commit()
        pool.close()
        read_conn.close()
        raise RuntimeError("no available TDX connections; HTTP fallback is disabled")
    if pool is not None and pool.available < requested_pool_size:
        print(f"warning: only {pool.available}/{requested_pool_size} adapters available")
        print(f"adapter failures: {pool.failure_summary()}")

    available_slots = 0
    if pool is not None:
        available_slots += pool.available
    if available_slots == 0:
        available_slots = max(1, args.workers)
    effective_workers = max(1, min(max(1, args.workers), available_slots))
    submit_window = max(effective_workers, min(8, int(args.submit_window or effective_workers)))

    print("=" * 72)
    print("mootdx qfq collector")
    print("=" * 72)
    print(f"symbols: {len(symbols)}")
    print(f"expected markets: {format_market_counts(market_counts)}")
    print(f"date range: {default_start} -> {end_date}")
    print(
        f"workers: requested={args.workers}, effective={effective_workers}, "
        f"pool={pool.available if pool is not None else 0}, submit_window={submit_window}, batch={args.batch_size}"
    )
    print(f"market filter: {args.market}, max_bars: {args.max_bars}, diagnostic_only: {args.diagnostic_only}")
    print(f"force_window: {args.force_window}")
    print(f"target table: {args.table}")
    print(f"raw target table: {args.raw_table}")
    print("=" * 72)

    try:
        outcome = collect_with_recovery(
            args, read_conn, symbols, last_dates, pool, default_start, end_date, complete_symbols,
        )
    except BaseException:
        read_conn.close()
        raise
    completed_results = outcome.results
    counts = Counter(result.status for result in completed_results)
    success, fail, skip = (counts[status] for status in ("success", "fail", "skip"))
    failed_symbols = [f"{result.symbol}:{result.reason}" for result in completed_results
                      if result.status == "fail"]
    saved_rows, raw_saved_rows = outcome.saved_rows, outcome.raw_saved_rows
    elapsed = outcome.elapsed
    source_outage_tripped = outcome.outage
    log_source_summary(
        read_conn,
        run_id=args.run_id,
        trade_date=end_date.isoformat(),
        results=completed_results,
        duration=elapsed,
    )
    read_conn.commit()
    print()
    print("=" * 72)
    print("done")
    print("=" * 72)
    print(f"success: {success}")
    print(f"fail: {fail}")
    print(f"skip: {skip}")
    print(f"saved rows: {saved_rows}")
    print(f"raw saved rows: {raw_saved_rows}")
    print(f"source recovery rounds: {outcome.recoveries}")
    print(f"elapsed: {elapsed:.2f}s")
    print(f"symbols/s (fetched): {(success + fail) / max(elapsed, 1e-6):.2f}")
    print(f"rows/s: {saved_rows / max(elapsed, 1e-6):.0f}")
    if source_outage_tripped:
        processed = success + fail + skip
        print(
            "source outage circuit breaker tripped: "
            f"threshold={args.source_outage_threshold}, unprocessed={all_symbol_count - processed}"
        )
    if failed_symbols:
        print("failed reason top:")
        for reason, count in Counter(item.split(":", 1)[1] if ":" in item else item for item in failed_symbols).most_common(10):
            print(f"  {reason}: {count}")
        print("failed symbols (first 30):")
        for item in failed_symbols[:30]:
            print(f"  {item}")
        if len(failed_symbols) > 30:
            print(f"  ... {len(failed_symbols) - 30} more")
    print("=" * 72)

    if args.result_json:
        result_path = Path(args.result_json)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "end_date": end_date.isoformat(),
                    "success": success,
                    "fail": fail,
                    "skip": skip,
                    "saved_rows": saved_rows,
                    "raw_saved_rows": raw_saved_rows,
                    "elapsed": elapsed,
                    "recovery_rounds": outcome.recoveries,
                    "unprocessed_symbols": sorted(set(symbols) - {r.symbol for r in completed_results}),
                    "results": [
                        {
                            "symbol": result.symbol,
                            "status": result.status,
                            "market": result.market,
                            "source": result.source,
                            "reason": result.reason,
                        }
                        for result in completed_results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    if pool is not None:
        pool.close()
    read_conn.close()
    processed = success + fail + skip
    fail_rate = fail / processed if processed else 1.0
    if source_outage_tripped:
        print("K-line collection failed: TDX source outage circuit breaker tripped.")
        return 1
    market_failures = failed_markets(completed_results)
    if market_failures:
        print(f"K-line collection failed: wholly failed markets={','.join(market_failures)}")
        return 1
    if fail_rate > args.max_fail_rate:
        print(f"K-line collection failed: fail_rate={fail_rate:.2%} > {args.max_fail_rate:.2%}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
