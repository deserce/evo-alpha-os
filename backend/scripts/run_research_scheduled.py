#!/usr/bin/env python3
"""Bounded, one-shot research updater process; never starts model services."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def worker_environment() -> dict[str, str]:
    """Remove inherited model credentials and constrain native worker threads."""
    env = {key: value for key, value in os.environ.items()
           if not any(token in key.upper() for token in
                      ('API_KEY', 'API_TOKEN', 'OPENAI', 'ANTHROPIC', 'GEMINI', 'DEEPSEEK'))}
    env.update(APP_DEBUG='false', EVOALPHA_DISABLE_LLM='1', EVOALPHA_LOCAL_ONLY='1')
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS'):
        env[key] = '1'
    return env


def rotate_log(path: Path, limit: int = 20 * 1024 * 1024) -> None:
    """Keep one bounded tail archive and truncate the active launchd inode."""
    if path.is_symlink():
        raise ValueError('log must not be a symbolic link')
    if not path.exists() or path.stat().st_size < limit:
        return
    archive = path.with_suffix(path.suffix + '.1')
    if archive.is_symlink():
        raise ValueError('log archive must not be a symbolic link')
    with path.open('rb') as source, archive.open('wb') as target:
        source.seek(-limit, os.SEEK_END)
        while chunk := source.read(65536):
            target.write(chunk)
    with path.open('r+b') as active:
        active.truncate(0)


def run_worker(command: list[str], timeout: float = 600) -> int:
    """Reap the worker process group on timeout or launchd shutdown."""
    process = subprocess.Popen(command, cwd=BACKEND_DIR, env=worker_environment(),
                               start_new_session=True)

    def stop(signum: int, _frame: object) -> None:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop(signal.SIGTERM, None)
            print('Research update exceeded runtime budget', file=sys.stderr)
            return 124
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    log_dir = Path.home() / 'Library' / 'Logs' / 'EvoAlpha'
    log_dir.mkdir(parents=True, exist_ok=True)
    for name, descriptor in (('research-daily.out.log', 1), ('research-daily.err.log', 2)):
        path = log_dir / name
        rotate_log(path)
        with path.open('ab', buffering=0) as log:
            os.dup2(log.fileno(), descriptor)
    return run_worker([sys.executable, '-m', 'research.update_reports', '--scheduled'])


if __name__ == '__main__':
    raise SystemExit(main())
