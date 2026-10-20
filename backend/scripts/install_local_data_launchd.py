#!/usr/bin/env python3
"""Install user-level launchd jobs for EvoAlpha local Data Core automation."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from data_job.common.network_utils import PROXY_ENV_KEYS

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = Path.home() / "Library" / "Logs" / "EvoAlpha"
LAUNCHD_LOG_MAX_BYTES = 20 * 1024 * 1024
RUNNER = BACKEND_DIR / "scripts" / "run_local_data_skill.py"
GUI_TARGET = f"gui/{os.getuid()}"
PREFERRED_PYTHON = Path("/opt/anaconda3/bin/python3")


@dataclass(frozen=True)
class LaunchJob:
    label: str
    mode: str
    hour: int
    minute: int
    weekdays: tuple[int, ...] = (1, 2, 3, 4, 5)
    module: str | None = None
    extra_arguments: tuple[str, ...] = ()
    run_at_load: bool = False


LAUNCH_JOBS: tuple[LaunchJob, ...] = (
    LaunchJob(
        "com.evoalpha.datacore.morning",
        "morning",
        9,
        0,
        extra_arguments=("--pending-days", "7", "--time-budget-minutes", "25"),
    ),
    LaunchJob("com.evoalpha.datacore.health", "health", 9, 30),
    LaunchJob(
        "com.evoalpha.watch.intraday",
        "intraday",
        9,
        35,
        module="quant_engine.watch.intraday",
        extra_arguments=("--run-until-close",),
        run_at_load=True,
    ),
    LaunchJob("com.evoalpha.datacore.daily", "daily", 15, 30),
    LaunchJob("com.evoalpha.datacore.retry", "retry", 16, 30),
    LaunchJob(
        "com.evoalpha.watch.evening",
        "evening",
        20,
        0,
        module="quant_engine.watch.evening",
    ),
    LaunchJob(
        "com.evoalpha.watch.retry",
        "watch-retry",
        22,
        30,
        module="quant_engine.watch.evening",
        extra_arguments=("--mode", "retry"),
    ),
    LaunchJob(
        "com.evoalpha.datacore.deep-health",
        "health",
        9,
        15,
        weekdays=(6,),
        extra_arguments=("--deep-health",),
    ),
)

OBSOLETE_LABELS = ("com.evoalpha.datacore.early",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage EvoAlpha Data Core LaunchAgents")
    parser.add_argument(
        "command",
        choices=["install", "uninstall", "status", "print-plist"],
        help="action to perform",
    )
    parser.add_argument("--dry-run", action="store_true", help="print actions without changing launchd")
    return parser


def weekday_schedule(hour: int, minute: int, weekdays: tuple[int, ...]) -> list[dict[str, int]]:
    return [{"Weekday": weekday, "Hour": hour, "Minute": minute} for weekday in weekdays]


def _bounded_log_command(path: Path) -> str:
    """Bound launchd log growth without adding a resident rotation process."""
    quoted_path = shlex.quote(str(path))
    return (
        f"if [ -f {quoted_path} ] && "
        f"[ \"$(/usr/bin/stat -f %z {quoted_path} 2>/dev/null || echo 0)\" "
        f"-gt {LAUNCHD_LOG_MAX_BYTES} ]; then : > {quoted_path}; fi"
    )


def build_plist(job: LaunchJob) -> dict[str, object]:
    stdout = LOG_DIR / f"{job.label}.out.log"
    stderr = LOG_DIR / f"{job.label}.err.log"
    python_executable = str(PREFERRED_PYTHON if PREFERRED_PYTHON.exists() else Path(sys.executable))
    if job.module:
        runtime_arguments = [
            "/usr/bin/caffeinate",
            "-i",
            python_executable,
            "-m",
            job.module,
            *job.extra_arguments,
        ]
    else:
        runtime_arguments = [
            "/usr/bin/caffeinate",
            "-i",
            python_executable,
            str(RUNNER),
            "--mode",
            job.mode,
            *job.extra_arguments,
        ]
    direct_arguments = ["/usr/bin/env"]
    for key in PROXY_ENV_KEYS:
        direct_arguments.extend(["-u", key])
    direct_arguments.extend(["NO_PROXY=*", "no_proxy=*", *runtime_arguments])
    shell_command = "; ".join(
        (
            _bounded_log_command(stdout),
            _bounded_log_command(stderr),
            f"cd {shlex.quote(str(BACKEND_DIR))} && exec {shlex.join(direct_arguments)}",
        )
    )
    return {
        "Label": job.label,
        "ProgramArguments": ["/bin/zsh", "-lc", shell_command],
        "EnvironmentVariables": {
            "EVOALPHA_LOCAL_ONLY": "1",
            "EVOALPHA_DISABLE_LLM": "1",
            "APP_DEBUG": "false",
            "HOME": str(Path.home()),
            "PATH": "/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "PYTHONPATH": str(BACKEND_DIR),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
        },
        "StartCalendarInterval": weekday_schedule(job.hour, job.minute, job.weekdays),
        "StandardOutPath": str(stdout),
        "StandardErrorPath": str(stderr),
        "RunAtLoad": job.run_at_load,
        "ProcessType": "Background",
    }


def plist_issues(plist: dict[str, object]) -> list[str]:
    """Return configuration issues that can make macOS reject the job before spawn."""
    issues: list[str] = []
    if plist.get("WorkingDirectory"):
        issues.append("WorkingDirectory 不得指向项目目录")
    protected_roots = (BACKEND_DIR, Path.home() / "Documents")
    for key in ("StandardOutPath", "StandardErrorPath"):
        raw_path = plist.get(key)
        if not isinstance(raw_path, str) or not raw_path:
            issues.append(f"{key} 缺失")
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            issues.append(f"{key} 必须是绝对路径")
            continue
        if any(path.is_relative_to(root) for root in protected_roots):
            issues.append(f"{key} 位于受保护目录: {path}")
        if not path.is_relative_to(LOG_DIR):
            issues.append(f"{key} 不在统一日志目录 {LOG_DIR}")
    return issues


def validate_plist(plist: dict[str, object]) -> None:
    issues = plist_issues(plist)
    if issues:
        raise ValueError("LaunchAgent 配置不安全: " + "; ".join(issues))


def plist_path(job: LaunchJob) -> Path:
    return LAUNCH_AGENTS_DIR / f"{job.label}.plist"


def dump_plist(plist: dict[str, object]) -> bytes:
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=False)


def lint_plist_bytes(data: bytes) -> None:
    if shutil.which("plutil") is None:
        plistlib.loads(data)
        return

    with tempfile.NamedTemporaryFile(suffix=".plist") as temp:
        temp.write(data)
        temp.flush()
        completed = subprocess.run(
            ["plutil", "-lint", temp.name],
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())


def write_plist_atomically(path: Path, data: bytes) -> None:
    """Replace a plist atomically so login-time loading never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_launchctl(args: Iterable[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def load_installed_plist(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    with path.open("rb") as handle:
        data = plistlib.load(handle)
    return data if isinstance(data, dict) else None


def program_arguments(plist: dict[str, object] | None) -> list[str]:
    args = (plist or {}).get("ProgramArguments")
    if not isinstance(args, list):
        return []
    return [str(item) for item in args]


def python_from_program_arguments(args: list[str]) -> str | None:
    if not args:
        return None
    if args[:2] == ["/bin/zsh", "-lc"] and len(args) >= 3:
        try:
            shell_tokens = shlex.split(args[2])
        except ValueError:
            return None
        for token in shell_tokens:
            if token.endswith("python3") or "Python.framework" in token or "Python.app" in token:
                return token
        return None
    if args[0] == "/usr/bin/caffeinate" and len(args) >= 3:
        return args[2]
    return args[0]


def active_pid(label: str, loaded_output: str) -> str | None:
    for line in loaded_output.splitlines():
        text = line.strip()
        if not text.startswith("pid = "):
            continue
        value = text.split("=", 1)[1].strip()
        return value if value and value != "0" else None
    return None


def last_exit_status(loaded_output: str) -> str:
    for line in loaded_output.splitlines():
        text = line.strip()
        if text.startswith("last exit code = "):
            return text.split("=", 1)[1].strip() or "-"
    return "-"


def process_command(pid: str | None) -> str | None:
    if not pid:
        return None
    completed = subprocess.run(
        ["ps", "-p", pid, "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    command = completed.stdout.strip()
    return command or None


def python_from_process_command(command: str | None) -> str | None:
    if not command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if token.endswith("python3") or "Python.framework" in token or "Python.app" in token:
            return token
    return tokens[0] if tokens else None


def install(dry_run: bool = False) -> int:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    for label in OBSOLETE_LABELS:
        path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        print(f"remove obsolete {label} <- {path}")
        if not dry_run:
            run_launchctl(["bootout", GUI_TARGET, str(path)], check=False)
            path.unlink(missing_ok=True)

    for job in LAUNCH_JOBS:
        path = plist_path(job)
        plist = build_plist(job)
        validate_plist(plist)
        data = dump_plist(plist)
        lint_plist_bytes(data)
        print(f"install {job.label} -> {path}")
        if dry_run:
            continue

        # Update the on-disk configuration first. An active process keeps its
        # loaded launchd definition, while the corrected plist is ready for
        # the next login even when stopping it would interrupt market hours.
        write_plist_atomically(path, data)
        loaded_state = run_launchctl(["print", f"{GUI_TARGET}/{job.label}"], check=False)
        pid = active_pid(job.label, loaded_state.stdout if loaded_state.returncode == 0 else "")
        if pid:
            print(f"preserve active {job.label} pid={pid}; on-disk plist updated")
            continue

        run_launchctl(["bootout", GUI_TARGET, str(path)], check=False)
        completed = run_launchctl(["bootstrap", GUI_TARGET, str(path)], check=False)
        if completed.returncode != 0:
            print(completed.stderr.strip() or completed.stdout.strip())
            return completed.returncode
        verified = run_launchctl(["print", f"{GUI_TARGET}/{job.label}"], check=False)
        if verified.returncode != 0:
            print(f"verify failed: {job.label} was not loaded")
            return verified.returncode or 1
    return 0


def uninstall(dry_run: bool = False) -> int:
    for job in LAUNCH_JOBS:
        path = plist_path(job)
        print(f"uninstall {job.label} <- {path}")
        if dry_run:
            continue
        run_launchctl(["bootout", GUI_TARGET, str(path)], check=False)
        path.unlink(missing_ok=True)
    for label in OBSOLETE_LABELS:
        path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        print(f"uninstall obsolete {label} <- {path}")
        if dry_run:
            continue
        run_launchctl(["bootout", GUI_TARGET, str(path)], check=False)
        path.unlink(missing_ok=True)
    return 0


def status() -> int:
    unhealthy = False
    for job in LAUNCH_JOBS:
        path = plist_path(job)
        completed = run_launchctl(["print", f"{GUI_TARGET}/{job.label}"], check=False)
        loaded = completed.returncode == 0
        loaded_output = completed.stdout if loaded else ""
        installed = load_installed_plist(path)
        installed_python = python_from_program_arguments(program_arguments(installed))
        issues = plist_issues(installed or {}) if installed else ["plist 缺失"]
        exit_status = last_exit_status(loaded_output)
        if issues or exit_status.startswith("78"):
            unhealthy = True
        pid = active_pid(job.label, loaded_output)
        command = process_command(pid)
        active_python = python_from_process_command(command)
        print(
            f"{job.label}: plist={'yes' if path.exists() else 'no'} "
            f"loaded={'yes' if loaded else 'no'} "
            f"schedule={job.hour:02d}:{job.minute:02d} weekdays={','.join(map(str, job.weekdays))} "
            f"python={installed_python or '-'} active_pid={pid or '-'} "
            f"active_python={active_python or '-'} "
            f"config={'ok' if not issues else f'invalid({len(issues)})'} "
            f"last_exit={exit_status}"
        )
    return 1 if unhealthy else 0


def print_plists() -> int:
    for job in LAUNCH_JOBS:
        print(f"--- {plist_path(job)}")
        print(dump_plist(build_plist(job)).decode("utf-8"))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "install":
        return install(dry_run=args.dry_run)
    if args.command == "uninstall":
        return uninstall(dry_run=args.dry_run)
    if args.command == "status":
        return status()
    if args.command == "print-plist":
        return print_plists()
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
