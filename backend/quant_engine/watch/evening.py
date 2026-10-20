"""20:00 低吸行动箱晚间摘要。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import os
from contextlib import closing
from pathlib import Path
from typing import Any

from quant_engine.watch.dispatcher import WatchNotificationDispatcher
from quant_engine.watch.engine import DB_PATH


def _latest_snapshot_date(db_path: Path) -> str | None:
    with closing(sqlite3.connect(db_path, timeout=5)) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='watch_daily_snapshot'"
        ).fetchone()
        if table is None:
            return None
        row = conn.execute(
            "SELECT MAX(substr(trade_date, 1, 10)) FROM watch_daily_snapshot"
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def run_evening(
    db_path: str | Path | None = None,
    *,
    dispatcher: WatchNotificationDispatcher | None = None,
) -> dict[str, Any]:
    target_path = Path(db_path) if db_path else DB_PATH
    trade_date = _latest_snapshot_date(target_path)
    if not trade_date:
        return {
            "success": True,
            "status": "NO_SNAPSHOT",
            "trade_date": None,
        }

    notifier = dispatcher or WatchNotificationDispatcher(target_path)
    immediate = notifier.dispatch_immediate(trade_date)
    digest = notifier.dispatch_digest(trade_date, require_trusted=True)
    failed = int(immediate.get("failed") or 0) + int(digest.get("failed") or 0)
    return {
        "success": failed == 0,
        "status": "COMPLETED" if failed == 0 else "DELIVERY_FAILED",
        "trade_date": trade_date,
        "immediate": immediate,
        "digest": digest,
    }


def run_report_followup(db_path: str | Path, *, mode: str) -> dict[str, Any]:
    """Run a bounded rule-only report worker separately from legacy reminders."""
    env = dict(os.environ, EVOALPHA_DISABLE_LLM="1", EVOALPHA_LOCAL_ONLY="1")
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env[key] = "1"
    phases = ("prepare", "deliver") if mode == "evening" else ("retry",)
    base = [sys.executable, "-m", "quant_engine.watch.three_group_daily",
            "--db-path", str(db_path)]
    for phase in phases:
        command = [*base, "--mode", phase]
        if phase != "prepare":
            command.append("--send")
        try:
            result = subprocess.run(command, env=env, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {"success": False, "status": "FAILED", "stage": phase}
        if result.returncode != 0:
            return {"success": False, "status": "FAILED", "stage": phase}
    return {"success": True, "status": "COMPLETED"}


def main() -> int:
    parser = argparse.ArgumentParser(description="EvoAlpha 晚间摘要与三分组日报兜底")
    parser.add_argument("--db-path")
    parser.add_argument("--mode", choices=("evening", "retry"), default="evening")
    args = parser.parse_args()
    target_path = Path(args.db_path) if args.db_path else DB_PATH
    if args.mode == "retry":
        legacy = WatchNotificationDispatcher(target_path).retry_failed()
        legacy_success = not legacy.get("failed")
    else:
        legacy = run_evening(target_path)
        legacy_success = bool(legacy.get("success"))
    report = run_report_followup(target_path, mode=args.mode)
    result = {"success": legacy_success and report["success"],
              "legacy": legacy, "three_group_report": report}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
