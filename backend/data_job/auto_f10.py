"""On-demand, durable F10 downloads; no polling or resident executor."""
from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from sqlalchemy import text

logger = logging.getLogger(__name__)
BACKEND = Path(__file__).resolve().parents[1]


def ensure_queue(engine) -> None:
    """One bounded row per stock; retry state survives process restarts."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS stock_f10_download_queue (
                symbol TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
                next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                bundle_version INTEGER NOT NULL DEFAULT 0
            )
        """))
        columns = {row[1] for row in conn.execute(text(
            'PRAGMA table_info(stock_f10_download_queue)'
        ))}
        if 'bundle_version' not in columns:
            conn.execute(text(
                'ALTER TABLE stock_f10_download_queue '
                'ADD COLUMN bundle_version INTEGER NOT NULL DEFAULT 0'
            ))


def enqueue_symbols(symbols, *, engine=None, start=True) -> int:
    """Queue valid codes and kick a single worker; failures never affect callers."""
    try:
        if engine is None:
            from app.core.database import get_engine
            engine = get_engine()
        ensure_queue(engine)
        codes = sorted({str(value).strip() for value in symbols
                        if re.fullmatch(r'\d{6}', str(value).strip())})
        count = 0
        with engine.begin() as conn:
            for code in codes:
                result = conn.execute(text("""
                    INSERT INTO stock_f10_download_queue(symbol) VALUES (:symbol)
                    ON CONFLICT(symbol) DO UPDATE SET status='pending',
                        next_attempt_at=CURRENT_TIMESTAMP
                    WHERE next_attempt_at <= CURRENT_TIMESTAMP
                       OR (status='done' AND bundle_version < 2)
                """), {'symbol': code})
                count += result.rowcount
        if start:
            start_worker(engine)
        return count
    except Exception:
        logger.exception('Automatic F10 enqueue failed')
        return 0


def pending_symbols(engine) -> list[str]:
    """Fetch a small batch without holding a transaction during networking."""
    with engine.connect() as conn:
        return list(conn.execute(text("""
            SELECT symbol FROM stock_f10_download_queue
            WHERE status IN ('pending', 'failed') AND next_attempt_at <= CURRENT_TIMESTAMP
            ORDER BY updated_at, symbol LIMIT 25
        """)).scalars())


def enqueue_current_universe(engine) -> int:
    """Catch existing members/results even when strategy dates are skipped."""
    try:
        with engine.connect() as conn:
            tables = set(conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )).scalars())
            symbols = set()
            if 'stock_pool_members' in tables:
                symbols.update(conn.execute(text(
                    'SELECT DISTINCT symbol FROM stock_pool_members'
                )).scalars())
            if 'quant_preselect_results' in tables:
                symbols.update(conn.execute(text("""
                    SELECT DISTINCT symbol FROM quant_preselect_results
                    WHERE strategy_name='mrgc_v1' AND trade_date=(
                        SELECT MAX(trade_date) FROM quant_preselect_results
                        WHERE strategy_name='mrgc_v1'
                    )
                """)).scalars())
        return enqueue_symbols(symbols, engine=engine)
    except Exception:
        logger.exception('Automatic material universe discovery failed')
        return 0


def finish_symbol(engine, symbol: str, success: bool) -> None:
    """Successful downloads expire in seven days; failures cool down one hour."""
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE stock_f10_download_queue SET status=:status,
                bundle_version=CASE WHEN :status='done' THEN 2 ELSE bundle_version END,
                next_attempt_at=DATETIME('now', :cooldown), updated_at=CURRENT_TIMESTAMP
            WHERE symbol=:symbol
        """), {'symbol': symbol, 'status': 'done' if success else 'failed',
               'cooldown': '+7 days' if success else '+1 hour'})


def _paths(engine):
    database = Path(engine.url.database).resolve()
    return database, database.with_suffix('.f10.schedule.lock'), database.with_suffix('.f10.worker.lock')


def start_worker(engine) -> None:
    """Serialize start/stop decisions; pass the worker lock to the child."""
    database, schedule_path, worker_path = _paths(engine)
    with (database.parent / '.daily_data_workflow.lock').open('a') as core:
        try:
            fcntl.flock(core, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
    with schedule_path.open('a') as schedule, worker_path.open('a') as worker:
        fcntl.flock(schedule, fcntl.LOCK_EX)
        try:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if not pending_symbols(engine):
            return
        env = dict(
            os.environ, LOCAL_DB_PATH=str(database),
            LOCAL_DATABASE_URL=f'sqlite:///{database}', APP_DEBUG='false'
        )
        for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                    'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
            env[key] = '1'
        process = subprocess.Popen(
            [sys.executable, '-m', 'data_job.auto_f10', '--worker', str(worker.fileno())],
            cwd=BACKEND, env=env, pass_fds=(worker.fileno(),), start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=process.wait, name='f10-worker-reaper', daemon=True).start()


def download_symbol(engine, symbol: str, *, timeout: float = 120) -> bool:
    """Refresh the material bundle with a hard per-stock deadline."""
    database, _, _ = _paths(engine)
    env = dict(
        os.environ, LOCAL_DB_PATH=str(database),
        LOCAL_DATABASE_URL=f'sqlite:///{database}', APP_DEBUG='false'
    )
    result = subprocess.run(
        [sys.executable, '-m', 'data_job.auto_f10', '--download', symbol],
        cwd=BACKEND, env=env, timeout=timeout, check=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def run_worker(fd: int) -> None:
    """One batch of at most 25 stocks, 10 minutes, sharing the Data Core lock."""
    from app.core.database import get_engine
    engine = get_engine()
    database, _, _ = _paths(engine)
    deadline = time.monotonic() + 600
    try:
        with os.fdopen(fd, 'a') as worker:
            # Never hold a read transaction across networking.
            symbols = pending_symbols(engine)
            for symbol in symbols:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                with (database.parent / '.daily_data_workflow.lock').open('a') as core:
                    try:
                        fcntl.flock(core, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        break
                    try:
                        success = download_symbol(engine, symbol, timeout=min(120, remaining))
                    except Exception:
                        logger.exception('Automatic F10 download failed for %s', symbol)
                        success = False
                    finish_symbol(engine, symbol, success)
            fcntl.flock(worker, fcntl.LOCK_UN)
    finally:
        engine.dispose()


def refresh_bundle(collector, symbol: str) -> bool:
    """Download recent real earnings before building display caches and F10."""
    success = True
    summary_ok = False
    for module, refresh in (
        ('finance_summary', lambda: collector._refresh_finance_summary(symbol, years=6)),
        ('financial_reports', lambda: collector._refresh_financial_reports(symbol, local_only=True)),
        ('finance_snapshot', lambda: collector._refresh_finance_snapshot(symbol)),
        ('f10', lambda: collector._refresh_f10(symbol, remote_first=True)),
    ):
        if module in ('financial_reports', 'finance_snapshot') and not summary_ok:
            continue
        try:
            rows = refresh()
            if module == 'finance_summary':
                summary_ok = bool(rows)
            if not rows:
                success = False
        except Exception:
            logger.exception('Automatic material module failed: %s', module)
            success = False
    return success


def main() -> int:
    if sys.argv[1] == '--kick':
        from app.core.database import get_engine
        engine = get_engine()
        try:
            start_worker(engine)
        finally:
            engine.dispose()
        return 0
    if sys.argv[1] == '--worker':
        run_worker(int(sys.argv[2]))
        return 0
    from data_job.collectors.stock_enrichment_collector import StockEnrichmentCollector
    collector = StockEnrichmentCollector()
    try:
        collector.ensure_tables()
        return 0 if refresh_bundle(collector, sys.argv[2]) else 1
    finally:
        collector.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
