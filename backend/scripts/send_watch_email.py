#!/usr/bin/env python3
"""Send a Watch Engine test email without exposing SMTP credentials."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.notifications.email_notifier import send_test


def main() -> int:
    parser = argparse.ArgumentParser(description="EvoAlpha Watch Engine email helper")
    parser.add_argument("--test", action="store_true", help="send one SMTP test email")
    args = parser.parse_args()
    if not args.test:
        parser.error("--test is required")

    result = send_test()
    if result.success:
        print("watch_email_test=sent")
        return 0
    print(f"watch_email_test={'skipped' if result.skipped else 'failed'} error={result.error}")
    return 2 if result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
