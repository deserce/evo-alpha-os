"""On-demand, durable F10 downloads; no polling or resident executor."""
from __future__ import annotations

import argparse
import fcntl
from datetime import date
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import tempfile
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
        if 'formula_priority' not in columns:
            conn.execute(text(
                'ALTER TABLE stock_f10_download_queue '
                'ADD COLUMN formula_priority INTEGER NOT NULL DEFAULT 0'
            ))
        for column in ('module_status', 'last_error'):
            if column not in columns:
                conn.execute(text(
                    f'ALTER TABLE stock_f10_download_queue ADD COLUMN {column} TEXT'
                ))


def enqueue_symbols(symbols, *, engine=None, start=True, strict=False, formula_priorities=None) -> int:
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
                    INSERT INTO stock_f10_download_queue(symbol, formula_priority)
                    VALUES (:symbol, :priority)
                    ON CONFLICT(symbol) DO UPDATE SET status=CASE
                        WHEN status='failed' THEN status ELSE 'pending' END,
                        next_attempt_at=CURRENT_TIMESTAMP,
                        formula_priority=excluded.formula_priority
                    WHERE next_attempt_at <= CURRENT_TIMESTAMP
                       OR (status='done' AND bundle_version < 3)
                """), {'symbol': code, 'priority': (formula_priorities or {}).get(code, 0)})
                count += result.rowcount
        if start:
            start_worker(engine)
        return count
    except Exception:
        if strict:
            raise
        logger.exception('Automatic F10 enqueue failed')
        return 0


def pending_symbols(engine) -> list[str]:
    """Fetch a small batch without holding a transaction during networking."""
    with engine.connect() as conn:
        return list(conn.execute(text("""
            SELECT symbol FROM stock_f10_download_queue
            WHERE status IN ('pending', 'failed') AND next_attempt_at <= CURRENT_TIMESTAMP
            ORDER BY CASE WHEN bundle_version=0 THEN 0 ELSE 1 END,
                CASE WHEN bundle_version=0 AND status='pending' AND formula_priority=2
                     THEN 0 ELSE 1 END,
                updated_at, symbol LIMIT 25
        """)).scalars())


def _formula_symbols(conn, tables, start: str | None, end: str | None) -> set[str]:
    if not {'quant_formula_results', 'strategy_run_log'} <= tables:
        return set()
    if start is None:
        if 'stock_daily_prices' not in tables:
            return set()
        end = conn.execute(text("SELECT MAX(trade_date) FROM stock_daily_prices")).scalar()
        if not end:
            return set()
        start = end = str(end)[:10]
    return set(conn.execute(text("""
        SELECT DISTINCT r.symbol FROM quant_formula_results r
        WHERE r.trade_date BETWEEN :start AND :end
          AND (
            SELECT a.status FROM strategy_run_log a
            WHERE a.strategy_name=r.strategy_name AND a.trade_date=r.trade_date
            ORDER BY a.finished_at DESC, a.rowid DESC LIMIT 1
          )='success'
    """), {'start': start, 'end': end}).scalars())


def enqueue_formula_range(engine, start: str, end: str, *, kick: bool = True) -> tuple[int, int]:
    """Register the union of confirmed formula hits for at most 31 calendar days."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end or not 0 <= (last - first).days <= 30:
        raise ValueError('formula range must use YYYY-MM-DD and span at most 31 calendar days')
    with engine.connect() as conn:
        tables = set(conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars())
        symbols = _formula_symbols(conn, tables, start, end)
        latest = _formula_symbols(conn, tables, end, end)
    priorities = {symbol: 2 if symbol in latest else 1 for symbol in symbols}
    return len(symbols), enqueue_symbols(
        symbols, engine=engine, start=kick, strict=True, formula_priorities=priorities,
    )


def enqueue_current_universe(engine) -> int:
    """Catch existing members/results even when strategy dates are skipped."""
    try:
        with engine.connect() as conn:
            tables = set(conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )).scalars())
            formulas = _formula_symbols(conn, tables, None, None)
            symbols = set()
            if 'stock_pool_members' in tables:
                symbols.update(conn.execute(text(
                    'SELECT DISTINCT symbol FROM stock_pool_members'
                )).scalars())
            if 'quant_preselect_results' in tables:
                symbols.update(conn.execute(text("""
                    SELECT DISTINCT symbol FROM quant_preselect_results r
                    WHERE trade_date=(
                        SELECT MAX(trade_date) FROM quant_preselect_results latest
                        WHERE latest.strategy_name=r.strategy_name
                    )
                """)).scalars())
        priorities = dict.fromkeys(formulas - symbols, 2)
        return enqueue_symbols(symbols | formulas, engine=engine, formula_priorities=priorities)
    except Exception:
        logger.exception('Automatic material universe discovery failed')
        return 0


def finish_symbol(engine, symbol: str, success: bool, *, error: str | None = None) -> None:
    """Refresh each Shanghai day; failed requests cool down one hour."""
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE stock_f10_download_queue SET status=:status,
                bundle_version=CASE WHEN :status='done' THEN 3 ELSE bundle_version END,
                next_attempt_at=CASE WHEN :status='done' THEN
                    DATETIME('now','+8 hours','start of day','+1 day','-8 hours')
                    ELSE DATETIME('now','+1 hour') END,
                last_error=CASE WHEN :status='done' THEN NULL
                    ELSE COALESCE(:error,last_error,'download_failed') END,
                updated_at=CURRENT_TIMESTAMP
            WHERE symbol=:symbol
        """), {'symbol': symbol, 'status': 'done' if success else 'failed',
               'error': error})


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
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        env[key] = '1'
    # Keep diagnostics bounded in memory; each child has a hard deadline.
    with tempfile.TemporaryFile(mode='w+b') as output:
        result = subprocess.run(
            [sys.executable, '-m', 'data_job.auto_f10', '--download', symbol],
            cwd=BACKEND, env=env, timeout=timeout, check=False,
            stdin=subprocess.DEVNULL, stdout=output, stderr=output,
        )
        size = output.tell()
        output.seek(max(0, size - 16384))
        for line in output.read().decode('utf-8', errors='replace').splitlines():
            if line.startswith('MATERIAL_RESULT='):
                try:
                    modules = json.loads(line.removeprefix('MATERIAL_RESULT='))
                except ValueError:
                    logger.warning('Invalid material result for %s', symbol)
                    continue
                _persist_module_status(engine, symbol, modules)
    return result.returncode == 0


def _persist_module_status(engine, symbol: str, modules: dict) -> None:
    errors = '; '.join(
        f"{name}: {item.get('error', item['status'])}"
        for name, item in modules.items() if item['status'] != 'ok'
    )[:1000]
    with engine.begin() as conn:
        conn.execute(text('''UPDATE stock_f10_download_queue
            SET module_status=:modules,last_error=:error WHERE symbol=:symbol
        '''), {'modules': json.dumps(modules, ensure_ascii=False),
              'error': errors or None, 'symbol': symbol})


def run_worker(fd: int) -> None:
    """Drain small batches for at most 30 minutes/500 stocks; never poll idle."""
    from app.core.database import get_engine
    engine = get_engine()
    database, _, _ = _paths(engine)
    deadline = time.monotonic() + 1800
    processed = 0
    try:
        with os.fdopen(fd, 'a') as worker:
            # Never hold a read transaction across networking.
            while processed < 500:
                symbols = pending_symbols(engine)
                if not symbols:
                    break
                for symbol in symbols:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or processed >= 500:
                        return
                    with (database.parent / '.daily_data_workflow.lock').open('a') as core:
                        try:
                            fcntl.flock(core, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            return
                        error = None
                        try:
                            reusable = _reusable_modules(engine, symbol, formula_only=True)
                            if len(reusable) == 4:
                                _persist_module_status(engine, symbol, reusable)
                                success = True
                            else:
                                success = download_symbol(engine, symbol, timeout=min(120, remaining))
                        except subprocess.TimeoutExpired:
                            error = 'download_timeout'
                            success = False
                        except Exception as exc:
                            logger.exception('Automatic F10 download failed for %s', symbol)
                            error = type(exc).__name__
                            success = False
                        finish_symbol(engine, symbol, success, error=error)
                        processed += 1
            fcntl.flock(worker, fcntl.LOCK_UN)
    finally:
        engine.dispose()


def _reusable_modules(engine, symbol: str, *, formula_only: bool = False) -> dict:
    """Check source and freshness before reusing a formula cache or a partial attempt."""
    latest_period = (
        "(SELECT MAX(report_date) FROM stock_finance_summary WHERE code=:symbol)"
    )
    checks = {
        'finance_summary': (
            "stock_finance_summary", "code=:symbol AND source='eastmoney_f10' "
            "AND total_revenue IS NOT NULL AND net_profit IS NOT NULL "
            f"AND report_date={latest_period}",
        ),
        'financial_reports': (
            "stock_financial_reports", "symbol=:symbol AND source='finance_summary' "
            f"AND report_date={latest_period} AND LENGTH(TRIM(payload_json)) > 2",
        ),
        'finance_snapshot': (
            "stock_finance_snapshot_tdx", "symbol=:symbol AND source='finance_summary_fallback' "
            f"AND report_date={latest_period} AND income IS NOT NULL AND profit IS NOT NULL",
        ),
        'f10': (
            "stock_f10_sections", "symbol=:symbol AND source IN ('mootdx','eastmoney_direct') "
            "AND LENGTH(TRIM(content)) > 0",
        ),
    }
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT formula_priority,
                   CASE WHEN status='failed'
                             AND DATE(updated_at,'+8 hours')=DATE('now','+8 hours')
                        THEN module_status END
            FROM stock_f10_download_queue WHERE symbol=:symbol
        """), {'symbol': symbol}).first()
        if row is None:
            return {}
        formula_priority, prior = row
        if formula_only and not formula_priority:
            return {}
        try:
            statuses = json.loads(prior) if prior else {}
        except ValueError:
            statuses = {}
        if not isinstance(statuses, dict):
            statuses = {}
        reusable = {}
        tables = set(conn.execute(text("""
            SELECT name FROM sqlite_master WHERE type='table' AND name IN
                ('stock_finance_summary','stock_financial_reports',
                 'stock_finance_snapshot_tdx','stock_f10_sections')
        """)).scalars())
        for module, (table, where) in checks.items():
            if table not in tables:
                continue
            if module in {'financial_reports', 'finance_snapshot'} and 'finance_summary' not in reusable:
                continue
            item = statuses.get(module)
            if not formula_priority and (not isinstance(item, dict) or item.get('status') != 'ok'):
                continue
            cutoff = "DATE('now','+8 hours')"
            if formula_priority:
                # Annual/Q1 (January–April), interim (July–August), and Q3 (October).
                cutoff = "DATETIME('now','+8 hours','-7 days')"
                if module != 'f10':
                    cutoff = (
                        "DATETIME('now','+8 hours', CASE WHEN STRFTIME('%m','now','+8 hours') "
                        "IN ('01','02','03','04','07','08','10') THEN '-1 day' ELSE '-7 days' END)"
                    )
            rows = conn.execute(text(f"""
                SELECT COUNT(*) FROM {table} WHERE {where}
                AND fetched_at BETWEEN {cutoff} AND DATETIME('now','+8 hours')
            """), {'symbol': symbol}).scalar()
            if rows:
                reusable[module] = (
                    {'status': 'ok', 'rows': rows, 'cached': True} if formula_priority else item
                )
    return reusable


def refresh_bundle(
    collector, symbol: str, *, statuses: dict | None = None, reusable: dict | None = None,
) -> bool:
    """Download recent real earnings before building display caches and F10."""
    success = True
    summary_ok = False
    if statuses is None:
        statuses = {}
    reusable = reusable or {}
    for module, refresh in (
        ('finance_summary', lambda: collector._refresh_finance_summary(symbol, years=6)),
        ('financial_reports', lambda: collector._refresh_financial_reports(symbol, local_only=True)),
        ('finance_snapshot', lambda: collector._refresh_finance_snapshot(symbol)),
        ('f10', lambda: collector._refresh_f10(symbol, remote_first=True)),
    ):
        if module in reusable:
            statuses[module] = reusable[module]
            if module == 'finance_summary':
                summary_ok = True
            continue
        if module in ('financial_reports', 'finance_snapshot') and not summary_ok:
            statuses[module] = {'status': 'skipped', 'rows': 0,
                                'error': 'finance_summary_unavailable'}
            continue
        try:
            rows = refresh()
            if module == 'finance_summary':
                summary_ok = bool(rows)
            if not rows:
                success = False
            statuses[module] = {'status': 'ok' if rows else 'empty', 'rows': int(rows or 0)}
        except Exception as exc:
            statuses[module] = {'status': 'failed', 'rows': 0, 'error': str(exc)[:300]}
            logger.exception('Automatic material module failed: %s', module)
            success = False
    return success


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {'--kick', '--formula-range'}:
        from app.core.database import get_engine
        parser = argparse.ArgumentParser(description='Queue stock materials')
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--kick', action='store_true')
        mode.add_argument('--formula-range', nargs=2, metavar=('START', 'END'))
        parser.add_argument('--enqueue-only', action='store_true')
        args = parser.parse_args()
        engine = get_engine()
        try:
            if args.kick:
                if args.enqueue_only:
                    parser.error('--enqueue-only requires --formula-range')
                enqueue_current_universe(engine)
            else:
                try:
                    matched, queued = enqueue_formula_range(
                        engine, *args.formula_range, kick=not args.enqueue_only,
                    )
                except ValueError as exc:
                    parser.error(str(exc))
                print(f'formula_symbols={matched} queued={queued}', flush=True)
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
        statuses = {}
        symbol = sys.argv[2]
        reusable = _reusable_modules(collector.engine, symbol)
        success = refresh_bundle(collector, symbol, statuses=statuses, reusable=reusable)
        print('MATERIAL_RESULT=' + json.dumps(statuses, ensure_ascii=False), flush=True)
        return 0 if success else 1
    finally:
        collector.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
