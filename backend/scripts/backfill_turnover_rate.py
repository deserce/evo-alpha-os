"""
Backfill turnover_rate for existing rows in stock_daily_prices.

策略：
    1. 从 DB 读每只股票已有的 (trade_date, close, volume, amount)
    2. 从 mootdx 取 xdxr
    3. 调用 compute_turnover_rate 重新计算
    4. UPDATE 回库（只动 turnover_rate 字段，不改价格/成交量）

适合一次性补齐历史空值，也可用 --full-recompute 整体重算。
"""

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Optional

import pandas as pd
from tqdm import tqdm

backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from data_job.core.mootdx_adapter import MootdxAdapter
from data_job.core.mootdx_qfq import compute_turnover_rate


@dataclass
class BackfillResult:
    symbol: str
    status: str
    updates: list[tuple[float, str, str]] = None  # (turnover_rate, symbol, trade_date)
    reason: str = ""


class AdapterPool:
    def __init__(self, size: int, timeout: int):
        self._queue: Queue[MootdxAdapter] = Queue(maxsize=size)
        for _ in range(size):
            adapter = MootdxAdapter(timeout=timeout)
            if adapter.is_available():
                self._queue.put(adapter)

    @property
    def available(self) -> int:
        return self._queue.qsize()

    def acquire(self, timeout: int = 30) -> MootdxAdapter:
        return self._queue.get(timeout=timeout)

    def release(self, adapter: MootdxAdapter):
        self._queue.put(adapter)

    def close(self):
        while not self._queue.empty():
            adapter = self._queue.get_nowait()
            adapter.disconnect()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.LOCAL_DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def load_symbols(conn: sqlite3.Connection, only_null: bool, symbol: Optional[str], limit: Optional[int]) -> list[str]:
    if symbol:
        return [str(symbol).zfill(6)]

    if only_null:
        query = """
            SELECT DISTINCT symbol
            FROM stock_daily_prices
            WHERE turnover_rate IS NULL
            ORDER BY symbol
        """
    else:
        query = "SELECT DISTINCT symbol FROM stock_daily_prices ORDER BY symbol"

    df = pd.read_sql_query(query, conn)
    symbols = [str(s).zfill(6) for s in df["symbol"].tolist()]
    if limit:
        symbols = symbols[:limit]
    return symbols


def load_kline(conn: sqlite3.Connection, symbol: str, only_null: bool) -> pd.DataFrame:
    if only_null:
        query = """
            SELECT trade_date, open, close, high, low, volume, amount
            FROM stock_daily_prices
            WHERE symbol = ? AND turnover_rate IS NULL
            ORDER BY trade_date
        """
    else:
        query = """
            SELECT trade_date, open, close, high, low, volume, amount
            FROM stock_daily_prices
            WHERE symbol = ?
            ORDER BY trade_date
        """
    df = pd.read_sql_query(query, conn, params=(symbol,))
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def process_symbol(symbol: str, only_null: bool, pool: AdapterPool, read_conn_path: str) -> BackfillResult:
    # 每线程独立只读连接，避免 SQLite 跨线程问题
    read_conn = sqlite3.connect(read_conn_path, timeout=60)
    try:
        target_df = load_kline(read_conn, symbol, only_null=only_null)
        if target_df.empty:
            return BackfillResult(symbol=symbol, status="skip", reason="no rows to backfill")

        if only_null:
            # 为保证 baseline / 单位检测准确，拿全量 kline 做计算上下文
            full_df = load_kline(read_conn, symbol, only_null=False)
        else:
            full_df = target_df
    finally:
        read_conn.close()

    if full_df.empty or "volume" not in full_df.columns:
        return BackfillResult(symbol=symbol, status="fail", reason="no kline context")

    try:
        adapter = pool.acquire()
    except Exception as exc:
        return BackfillResult(symbol=symbol, status="fail", reason=f"acquire adapter: {exc}")

    try:
        xdxr_df = adapter.get_xdxr_info(symbol)
    except Exception as exc:
        return BackfillResult(symbol=symbol, status="fail", reason=f"xdxr: {exc}")
    finally:
        pool.release(adapter)

    try:
        rate = compute_turnover_rate(full_df, xdxr_df)
    except Exception as exc:
        return BackfillResult(symbol=symbol, status="fail", reason=f"compute: {exc}")

    full_df = full_df.reset_index(drop=True)
    full_df["turnover_rate"] = rate.values

    target_dates = set(target_df["trade_date"].tolist())
    updates = [
        (
            float(row.turnover_rate) if pd.notna(row.turnover_rate) else None,
            symbol,
            row.trade_date.isoformat(),
        )
        for row in full_df.itertuples(index=False)
        if row.trade_date in target_dates
    ]

    # 过滤 None（仍填不上的不写，保留 NULL 便于复盘）
    updates = [u for u in updates if u[0] is not None]

    if not updates:
        return BackfillResult(symbol=symbol, status="skip", reason="all still null")

    return BackfillResult(symbol=symbol, status="success", updates=updates)


def apply_updates(conn: sqlite3.Connection, results: list[BackfillResult]) -> int:
    rows = []
    for result in results:
        if result.status == "success" and result.updates:
            rows.extend(result.updates)
    if not rows:
        return 0
    with conn:
        conn.executemany(
            "UPDATE stock_daily_prices SET turnover_rate = ? WHERE symbol = ? AND trade_date = ?",
            rows,
        )
    return len(rows)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backfill turnover_rate in stock_daily_prices")
    p.add_argument("--symbol", help="single symbol for testing")
    p.add_argument("--limit", type=int, help="limit symbol count")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--pool-size", type=int, default=4)
    p.add_argument("--timeout", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=50, help="symbols per DB write batch")
    p.add_argument("--full-recompute", action="store_true", help="recompute all rows, not just NULL")
    return p


def main():
    args = build_parser().parse_args()
    only_null = not args.full_recompute

    write_conn = get_db_connection()
    symbols = load_symbols(write_conn, only_null=only_null, symbol=args.symbol, limit=args.limit)

    if not symbols:
        print("nothing to backfill.")
        write_conn.close()
        return

    pool = AdapterPool(size=max(1, args.pool_size), timeout=args.timeout)
    if pool.available == 0:
        write_conn.close()
        raise RuntimeError("no available mootdx connections")

    print("=" * 72)
    print(f"turnover_rate backfill  (only_null={only_null})")
    print(f"symbols: {len(symbols)}, workers: {args.workers}, pool: {pool.available}")
    print("=" * 72)

    started = time.time()
    pending: list[BackfillResult] = []
    updated_rows = 0
    success = fail = skip = 0

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(process_symbol, sym, only_null, pool, settings.LOCAL_DB_PATH): sym
            for sym in symbols
        }

        with tqdm(total=len(futures), desc="backfill", unit="symbol") as progress:
            for future in as_completed(futures):
                result = future.result()
                if result.status == "success":
                    success += 1
                    pending.append(result)
                    if len(pending) >= args.batch_size:
                        updated_rows += apply_updates(write_conn, pending)
                        pending = []
                elif result.status == "skip":
                    skip += 1
                else:
                    fail += 1

                elapsed = max(time.time() - started, 1e-6)
                progress.update(1)
                progress.set_postfix(
                    ok=success,
                    skip=skip,
                    fail=fail,
                    rows=updated_rows,
                    sym_s=f"{(success + fail + skip) / elapsed:.2f}",
                )

    if pending:
        updated_rows += apply_updates(write_conn, pending)

    elapsed = time.time() - started
    print()
    print("=" * 72)
    print(f"done  success={success}  skip={skip}  fail={fail}  updated_rows={updated_rows}  elapsed={elapsed:.1f}s")
    print("=" * 72)

    pool.close()
    write_conn.close()


if __name__ == "__main__":
    main()
