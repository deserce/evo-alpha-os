#!/usr/bin/env python3
"""
Schedule the production daily data workflow.

This scheduler is intentionally thin: it only invokes
scripts/run_daily_data_workflow.py so the production entrypoint stays unique.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_daily_workflow() -> None:
    command = [sys.executable, "scripts/run_daily_data_workflow.py"]
    subprocess.run(command, cwd=BACKEND_DIR, check=False)


def main() -> int:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_daily_workflow,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        id="daily_data_workflow",
        name="每日生产数据流水线",
        misfire_grace_time=7200,
    )
    print("EvoAlpha OS production scheduler started: weekdays 16:00")
    scheduler.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
