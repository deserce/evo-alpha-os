#!/usr/bin/env python3
"""Manage only the independent daily 09:00 EvoAlpha research LaunchAgent."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess
import sys
import tempfile
import time

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
LABEL = 'com.evoalpha.research.daily'


def timezone_is_shanghai() -> bool:
    """launchd calendar triggers use the host timezone, not process TZ."""
    return str(Path('/etc/localtime').resolve()).endswith('/Asia/Shanghai')


def build_plist(home: Path | None = None, backend: Path = BACKEND_DIR) -> dict:
    """Construct a one-shot launch job without a Documents WorkingDirectory."""
    home = home or Path.home()
    logs = home / 'Library' / 'Logs' / 'EvoAlpha'
    python = '/opt/anaconda3/bin/python3'
    wrapper = backend / 'scripts' / 'run_research_scheduled.py'
    command = f'cd {shlex.quote(str(backend))} && exec {python} {shlex.quote(str(wrapper))}'
    env = {'HOME': str(home), 'PATH': '/opt/anaconda3/bin:/usr/bin:/bin:/usr/sbin:/sbin',
           'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8', 'PYTHONPATH': str(backend),
           'PYTHONUNBUFFERED': '1', 'APP_DEBUG': 'false', 'EVOALPHA_DISABLE_LLM': '1',
           'EVOALPHA_LOCAL_ONLY': '1', 'TZ': 'Asia/Shanghai'}
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS'):
        env[key] = '1'
    return {'Label': LABEL, 'ProgramArguments': ['/bin/zsh', '-lc', command],
            'EnvironmentVariables': env,
            'StartCalendarInterval': {'Hour': 9, 'Minute': 0},
            'RunAtLoad': True, 'KeepAlive': False, 'ProcessType': 'Background',
            'ExitTimeOut': 15, 'Nice': 10,
            'StandardOutPath': str(logs / 'research-daily.out.log'),
            'StandardErrorPath': str(logs / 'research-daily.err.log')}


def launchctl(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(['/bin/launchctl', *arguments], capture_output=True, text=True,
                          timeout=15, check=False)


def inspect_service() -> dict:
    """Only report allowlisted metadata, never raw launchctl environment dumps."""
    result = launchctl('print', f'gui/{os.getuid()}/{LABEL}')
    fields = {'loaded': result.returncode == 0}
    if result.returncode == 0:
        for name, pattern in (('state', r'^\s*state = (.+)$'),
                              ('pid', r'^\s*pid = (\d+)$'),
                              ('last_exit_code', r'^\s*last exit code = (.+)$')):
            match = re.search(pattern, result.stdout, re.MULTILINE)
            fields[name] = match.group(1) if match else None
    return fields


def write_plist(path: Path, payload: dict) -> None:
    """Write and validate one exact label atomically before changing registration."""
    if payload.get('Label') != LABEL or 'WorkingDirectory' in payload:
        raise ValueError('invalid research LaunchAgent configuration')
    for key in ('StandardOutPath', 'StandardErrorPath'):
        if Path(payload[key]).parent != Path(payload['EnvironmentVariables']['HOME']) / 'Library' / 'Logs' / 'EvoAlpha':
            raise ValueError('research logs must use Library/Logs/EvoAlpha')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            plistlib.dump(payload, temp)
            temp.flush()
            os.fsync(temp.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def install(home: Path | None = None) -> dict:
    """Refresh only our label, with bounded registration-race retries."""
    if sys.platform != 'darwin' or not timezone_is_shanghai():
        raise RuntimeError('requires macOS host timezone Asia/Shanghai')
    home = home or Path.home()
    payload = build_plist(home)
    path = home / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
    (home / 'Library' / 'Logs' / 'EvoAlpha').mkdir(parents=True, exist_ok=True)
    write_plist(path, payload)
    current = inspect_service()
    if current.get('pid'):
        return {'status': 'deferred_active_run', 'message': '配置已原子保存；当前更新结束后再安装以应用调度'}
    target = f'gui/{os.getuid()}'
    if current['loaded']:
        result = launchctl('bootout', f'{target}/{LABEL}')
        if result.returncode != 0:
            raise RuntimeError('research service bootout failed; existing plist preserved')
        for attempt in range(5):
            if not inspect_service()['loaded']:
                break
            time.sleep(0.5 * (attempt + 1))
        else:
            raise RuntimeError('research service still registered; no bootstrap attempted')
    for attempt in range(3):
        result = launchctl('bootstrap', target, str(path))
        if result.returncode == 0 or inspect_service()['loaded']:
            return {'status': 'installed', **inspect_service()}
        if 'operation not permitted' in (result.stderr or '').lower():
            raise RuntimeError('macOS denied process launch; inspect session permissions')
        if attempt < 2:
            time.sleep(attempt + 1)
    raise RuntimeError('research bootstrap failed after 3 attempts; inspect macOS spawn logs')


def status(home: Path | None = None) -> dict:
    home = home or Path.home()
    path = home / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
    healthy = False
    try:
        with path.open('rb') as handle:
            healthy = plistlib.load(handle) == build_plist(home)
    except (OSError, plistlib.InvalidFileException, ValueError):
        pass
    return {'label': LABEL, 'config_healthy': healthy,
            'timezone_healthy': timezone_is_shanghai(),
            'schedule': 'Daily 09:00 Asia/Shanghai; login catchup; runtime <=600s +5s cleanup',
            **inspect_service()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'status', 'print-plist'))
    args = parser.parse_args()
    if args.action == 'print-plist':
        sys.stdout.buffer.write(plistlib.dumps(build_plist()))
        return
    try:
        print(json.dumps(install() if args.action == 'install' else status(), ensure_ascii=False))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f'Research scheduler: {type(error).__name__}: {error}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
