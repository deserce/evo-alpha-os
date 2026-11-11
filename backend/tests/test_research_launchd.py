"""Isolated scheduler tests: no real launchctl mutation or production writes."""
from pathlib import Path
import plistlib
import subprocess
import sys
from unittest.mock import Mock

import pytest

from scripts import install_research_launchd as installer
from scripts import run_research_scheduled as runner


def test_plist_calendar_resources_and_safe_paths(tmp_path):
    payload = installer.build_plist(tmp_path, tmp_path / "Documents" / "quoted ' repo")
    assert payload['Label'] == 'com.evoalpha.research.daily'
    assert payload['RunAtLoad'] is True and payload['KeepAlive'] is False
    assert payload['StartCalendarInterval'] == {'Hour': 9, 'Minute': 0}
    assert 'WorkingDirectory' not in payload
    assert payload['ProgramArguments'][:2] == ['/bin/zsh', '-lc']
    assert 'exec /opt/anaconda3/bin/python3' in payload['ProgramArguments'][2]
    assert 'caffeinate' not in str(payload)
    assert Path(payload['StandardOutPath']).parent == tmp_path / 'Library/Logs/EvoAlpha'
    assert payload['EnvironmentVariables']['EVOALPHA_DISABLE_LLM'] == '1'
    assert payload['EnvironmentVariables']['OPENBLAS_NUM_THREADS'] == '1'
    assert payload['ExitTimeOut'] == 15


def test_atomic_write_and_invalid_paths(tmp_path):
    path = tmp_path / 'LaunchAgents' / 'test.plist'
    payload = installer.build_plist(tmp_path)
    installer.write_plist(path, payload)
    assert plistlib.loads(path.read_bytes()) == payload
    assert len(list(path.parent.iterdir())) == 1
    payload['WorkingDirectory'] = str(tmp_path)
    with pytest.raises(ValueError):
        installer.write_plist(path, payload)
    payload.pop('WorkingDirectory')
    payload['StandardOutPath'] = str(tmp_path / 'Documents/unsafe.log')
    with pytest.raises(ValueError):
        installer.write_plist(path, payload)


@pytest.fixture
def install_environment(monkeypatch):
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer, 'timezone_is_shanghai', lambda: True)
    sleep = Mock()
    monkeypatch.setattr(installer.time, 'sleep', sleep)
    return sleep


def test_install_retries_registration_race_only_for_own_label(tmp_path, monkeypatch,
                                                            install_environment):
    calls = []
    bootstraps = 0
    prints = 0

    def launchctl(*args):
        nonlocal bootstraps, prints
        calls.append(args)
        if args[0] == 'print':
            prints += 1
            loaded = prints == 1 or bootstraps >= 2
            return subprocess.CompletedProcess(args, 0 if loaded else 113, 'state = waiting')
        if args[0] == 'bootstrap':
            bootstraps += 1
            return subprocess.CompletedProcess(args, 5 if bootstraps == 1 else 0, '')
        return subprocess.CompletedProcess(args, 0, '')

    monkeypatch.setattr(installer, 'launchctl', launchctl)
    assert installer.install(tmp_path)['status'] == 'installed'
    assert bootstraps == 2
    assert install_environment.call_args_list
    assert all(installer.LABEL in ' '.join(call) for call in calls)
    assert (tmp_path / 'Library/LaunchAgents' / f'{installer.LABEL}.plist').exists()


def test_active_job_saved_without_interruption(tmp_path, monkeypatch, install_environment):
    launch = Mock(return_value=subprocess.CompletedProcess([], 0, 'pid = 123\nstate = running'))
    monkeypatch.setattr(installer, 'launchctl', launch)
    assert installer.install(tmp_path)['status'] == 'deferred_active_run'
    assert [call.args[0] for call in launch.call_args_list] == ['print']
    assert (tmp_path / 'Library/LaunchAgents' / f'{installer.LABEL}.plist').exists()


def test_bootstrap_failure_has_bounded_retries(tmp_path, monkeypatch, install_environment):
    launch = Mock(return_value=subprocess.CompletedProcess([], 5, 'private diagnostic'))
    monkeypatch.setattr(installer, 'launchctl', launch)
    with pytest.raises(RuntimeError, match='3 attempts'):
        installer.install(tmp_path)
    assert sum(call.args[0] == 'bootstrap' for call in launch.call_args_list) == 3
    assert install_environment.call_count == 2


def test_timezone_rejected_before_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, 'timezone_is_shanghai', lambda: False)
    with pytest.raises(RuntimeError, match='timezone'):
        installer.install(tmp_path)
    assert not list(tmp_path.iterdir())


def test_system_permission_denial_is_not_retried(tmp_path, monkeypatch, install_environment):
    launch = Mock(return_value=subprocess.CompletedProcess(
        [], 5, '', 'Operation not permitted'))
    monkeypatch.setattr(installer, 'launchctl', launch)
    with pytest.raises(RuntimeError, match='macOS denied'):
        installer.install(tmp_path)
    assert sum(call.args[0] == 'bootstrap' for call in launch.call_args_list) == 1
    assert install_environment.call_count == 0


def test_status_is_sanitized_and_reports_health(tmp_path, monkeypatch):
    path = tmp_path / 'Library/LaunchAgents' / f'{installer.LABEL}.plist'
    installer.write_plist(path, installer.build_plist(tmp_path))
    monkeypatch.setattr(installer, 'timezone_is_shanghai', lambda: True)
    monkeypatch.setattr(installer, 'launchctl', Mock(return_value=subprocess.CompletedProcess(
        [], 0, 'state = waiting\nlast exit code = 78\nOPENAI_API_KEY = secret')))
    result = installer.status(tmp_path)
    assert result['config_healthy'] and result['timezone_healthy']
    assert result['last_exit_code'] == '78'
    assert 'secret' not in str(result)


def test_log_rotation_is_bounded_and_preserves_active_inode(tmp_path):
    path = tmp_path / 'research.log'
    path.write_bytes(b'0123456789' * 3)
    inode = path.stat().st_ino
    runner.rotate_log(path, limit=10)
    assert path.stat().st_ino == inode
    assert path.read_bytes() == b''
    assert path.with_suffix('.log.1').read_bytes() == b'0123456789'
    path.write_bytes(b'keep')
    runner.rotate_log(path, limit=10)
    assert path.read_bytes() == b'keep'
    symlink = tmp_path / 'alias.log'
    symlink.symlink_to(path)
    with pytest.raises(ValueError, match='symbolic'):
        runner.rotate_log(symlink, limit=1)


def test_environment_strips_model_credentials(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-leak')
    monkeypatch.setenv('OTHER_API_TOKEN', 'must-not-leak')
    monkeypatch.setenv('OMP_NUM_THREADS', '32')
    env = runner.worker_environment()
    assert 'OPENAI_API_KEY' not in env and 'OTHER_API_TOKEN' not in env
    assert env['OMP_NUM_THREADS'] == '1'
    assert env['APP_DEBUG'] == 'false'


def test_real_spawn_exits_and_timeout_reaps(tmp_path):
    # Only execute a stdlib helper, never import the updater or access any database.
    marker = tmp_path / 'finished'
    command = [sys.executable, '-c',
               'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("ok")',
               str(marker)]
    assert runner.run_worker(command, timeout=5) == 0
    assert marker.read_text() == 'ok'
    assert runner.run_worker([sys.executable, '-c', 'import time; time.sleep(20)'],
                             timeout=0.1) == 124
