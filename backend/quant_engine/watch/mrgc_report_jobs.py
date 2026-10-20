"""One bounded archive worker; HTTP reads never calculate strategies or call AI."""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import uuid
from threading import Thread, Timer

from quant_engine.watch.mrgc_daily_report import build_report, render_email
from quant_engine.watch.mrgc_report_history import calculate_history
from quant_engine.watch.mrgc_report_archive import (
    directory, read_archive, read_partial, source_signature, write_archive,
)
from quant_engine.watch.structure_monitor import _day


def _write_job(db_path: Path | str, job: dict) -> None:
    folder = directory(db_path)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.job-', dir=folder)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(job, stream, ensure_ascii=False, allow_nan=False)
        os.replace(name, folder / 'job.json')
    finally:
        Path(name).unlink(missing_ok=True)


def _load_job(db_path: Path | str) -> dict:
    try:
        path = directory(db_path) / 'job.json'
        if path.stat().st_size > 16384:
            return {}
        job = json.loads(path.read_text())
        return job if isinstance(job, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_running(db_path: Path | str) -> bool:
    path = directory(db_path) / '.worker.lock'
    if not path.exists():
        return False
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def get_report(db_path: Path | str, target: str) -> dict:
    """Return the saved report and compact task state, with no CPU replay."""
    target = _day(target)
    active = _load_job(db_path)
    running = active.get('status') == 'RUNNING' and _is_running(db_path)
    archived = read_archive(db_path, target, validate=not running)
    partial = None if archived else read_partial(db_path, target, validate=not running)
    saved = archived or partial
    report = ({key: value for key, value in saved['report'].items() if key != '_history_results'}
              if saved else None)
    job = dict(date=target, status='NOT_GENERATED', stage='NOT_GENERATED',
               completed=0, total=0, reason='尚未生成该日期日报')
    if archived and archived.get('valid'):
        job.update(status='COMPLETED', stage='COMPLETED', reason='读取完整日报存档')
    elif saved and not saved.get('valid'):
        job.update(status='STALE', stage='STALE', reason='规则或源数据已更新，旧版存档可查看')
    elif partial:
        job.update(status='PARTIAL', stage='PARTIAL', reason='已保存部分结果，可继续生成')
    if running:
        if active.get('date') == target:
            job = active
        else:
            job.update(status='BUSY', stage='BUSY', reason='另一日期正在生成，请稍后重试')
    elif active.get('date') == target and active.get('status') == 'RUNNING':
        job.update(status='FAILED', stage='FAILED', reason='上次生成中断，可继续生成')
    elif active.get('date') == target and active.get('status') in {'FAILED', 'PARTIAL'}:
        if not archived or not archived.get('valid'):
            job = active
    if saved and not saved.get('valid') and not running:
        job.update(status='STALE', stage='STALE',
                   reason='源数据或规则需要重新校验，当前展示旧版存档，可更新日报')
    return dict(report=report, email_html=render_email(report) if report else '', job=job,
                archive=None if not saved else dict(
                    valid=saved.get('valid', False), archive_id=saved['archive_id'],
                    generated_at=saved['generated_at'], complete=bool(archived)))


def _watch_process(process: subprocess.Popen, db_path: Path | str, target: str, job_id: str) -> None:
    """Reap our child and enforce a hard deadline without periodic wakeups."""
    def terminate() -> None:
        if process.poll() is None:
            process.kill()
    deadline = Timer(200, terminate)
    deadline.daemon = True
    deadline.start()
    try:
        code = process.wait()
    finally:
        deadline.cancel()
    if code:
        active = _load_job(db_path)
        if active.get('job_id') == job_id and active.get('status') == 'RUNNING':
            active.update(status='FAILED', stage='FAILED', reason='生成进程超时或退出，可重试')
            _write_job(db_path, active)


def _log_path() -> Path:
    return Path.home() / 'Library' / 'Logs' / 'EvoAlpha' / 'mrgc_report_worker.log'


def _open_log():
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size >= 20 * 1024 * 1024:
        path.write_text('')
    return path.open('a')


def start_generation(db_path: Path | str, target: str, regenerate: bool = False) -> dict:
    """Start at most one subprocess; inherit the held lock to eliminate races."""
    target = _day(target)
    existing = get_report(db_path, target)
    if existing['job']['status'] == 'COMPLETED' and not regenerate:
        return existing
    folder = directory(db_path)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / '.worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return get_report(db_path, target)
        # Another worker may have finished while this request was validating its archive.
        existing = get_report(db_path, target)
        if existing['job']['status'] == 'COMPLETED' and not regenerate:
            return existing
        job = dict(date=target, job_id=uuid.uuid4().hex, status='RUNNING', stage='PREPARING', completed=0, total=0,
                   reason='正在确定历史票池', updated_at=datetime.now().isoformat(timespec='seconds'))
        _write_job(db_path, job)
        env = {name: os.environ[name] for name in
               ('HOME', 'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'PYTHONPATH', 'PYTHONUNBUFFERED')
               if name in os.environ}
        for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                     'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
            env[name] = '1'
        env['APP_DEBUG'] = 'false'
        command = [sys.executable, '-m', 'quant_engine.watch.mrgc_report_jobs',
                   '--db', str(Path(db_path).resolve()), '--date', target,
                   '--lock-fd', str(lock.fileno())]
        if regenerate:
            command.append('--regenerate')
        try:
            with _open_log() as log:
                process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2],
                                           env=env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log, pass_fds=(lock.fileno(),),
                                           start_new_session=True)
        except OSError:
            job.update(status='FAILED', stage='FAILED', reason='无法启动日报任务，可重试')
            _write_job(db_path, job)
            raise
        Thread(target=_watch_process, args=(process, db_path, target, job['job_id']), daemon=True,
               name='mrgc-report-reaper').start()
        existing['job'] = job
        return existing


def run_generation(db_path: Path | str, target: str, regenerate: bool = False) -> None:
    """Build facts once, checkpoint them, then enrich and atomically publish."""
    target = _day(target)
    job = dict(date=target, job_id=_load_job(db_path).get('job_id', uuid.uuid4().hex),
               status='RUNNING', stage='PREPARING', completed=0, total=0)
    def update(stage: str, reason: str, completed: int = 0, total: int = 0) -> None:
        job.update(stage=stage, reason=reason, completed=completed, total=total,
                   updated_at=datetime.now().isoformat(timespec='seconds'))
        _write_job(db_path, job)
    try:
        saved = read_partial(db_path, target) or read_archive(db_path, target)
        frozen = saved['report'].get('groups') if saved else None
        if saved and saved.get('valid') and saved['report'].get('status') == 'READY' and not regenerate:
            report = saved['report']
            signature = saved['signature']
        else:
            update('PREPARING', '正在确定所选日期票池与行情版本')
            seed = build_report(db_path, target, frozen_groups=frozen, compute_chan=False)
            signature = source_signature(db_path, seed)
            symbols = [row['symbol'] for row in seed['stocks']]
            update('CALCULATING', '正在计算历史 C 类结构', total=len(symbols))
            previous = saved['report'].get('_history_results') if saved and saved.get('valid') and not regenerate else None
            snapshots, events = previous if previous else ({}, {})
            pending = [symbol for symbol in symbols if snapshots.get(symbol, {}).get('data_status') != 'OK']
            new_snapshots, new_events = calculate_history(db_path, target, pending,
                progress=lambda done, total: update('CALCULATING', '正在计算历史 C 类结构',
                                                     len(symbols) - len(pending) + done, len(symbols)))
            snapshots.update(new_snapshots)
            events.update(new_events)
            facts = (snapshots, events)
            update('CHAN', '正在计算截至当日的缠论候选', len(symbols), len(symbols))
            report = build_report(db_path, target, frozen_groups=seed['groups'], history_results=facts)
            if source_signature(db_path, report) != signature:
                raise ValueError('source changed during calculation')
            report['source']['historical_calculation'] = True
            report['_history_results'] = facts
            # Preserve completed per-stock replay for bounded retries.
            write_archive(db_path, report, signature)
        # User requested strategy archives first; no provider or model is invoked.
        report['ai'] = dict(status='DEFERRED', model='', summary=[], sector_notes={},
                            stock_notes={}, reason='已保存策略日报，暂未生成 AI 解读')
        if source_signature(db_path, report) != signature:
            raise ValueError('source changed before archiving')
        update('SAVING', '正在保存日报存档', len(report['stocks']), len(report['stocks']))
        write_archive(db_path, report, signature)
        completed = report['status'] == 'READY' and report.get('ai', {}).get('status') in {'READY', 'DEFERRED'}
        job['status'] = 'COMPLETED' if completed else 'PARTIAL'
        update(job['status'], '日报已生成并存档' if completed else
               report.get('ai', {}).get('reason') or '部分结果已存档，需补齐数据后继续生成',
               len(report['stocks']), len(report['stocks']))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as exc:
        job['error_code'] = type(exc).__name__
        logging.getLogger(__name__).exception('Historical report generation failed for %s', target)
        job['status'] = 'FAILED'
        update('FAILED', '生成失败或源数据正在更新，已保留已有存档，可重试')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--lock-fd', type=int, required=True)
    parser.add_argument('--regenerate', action='store_true')
    args = parser.parse_args()
    deadline = Timer(200, lambda: os._exit(124))
    deadline.daemon = True
    deadline.start()
    try:
        with os.fdopen(args.lock_fd, 'a'):
            run_generation(args.db, args.date, args.regenerate)
    finally:
        deadline.cancel()


if __name__ == '__main__':
    main()
