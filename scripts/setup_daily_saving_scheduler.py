#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Register Apollo Saving Ledger Daily Report in Windows Task Scheduler
====================================================================
Schedules scripts/send_saving_report.py to run automatically every day at 5:00 PM (17:00).
"""

import sys
import subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = sys.executable
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "send_saving_report.py"
TASK_NAME = "ApolloDailySavingReport5PM"
DISPATCH_TIME = "17:00"


def register_scheduler(task_time: str = DISPATCH_TIME):
    tr_command = f'"{PYTHON_EXE}" "{SCRIPT_PATH}"'
    cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", tr_command,
        "/sc", "daily",
        "/st", task_time,
        "/f"
    ]

    print(f"\nRegistering Windows Task '{TASK_NAME}' at {task_time} daily...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"[✓] Task '{TASK_NAME}' successfully registered!")
        print(f"    • Frequency: Daily at {task_time} IST")
        print(f"    • Action   : {tr_command}")
        return True
    else:
        print(f"[!] Error registering task: {res.stderr or res.stdout}")
        return False


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else DISPATCH_TIME
    register_scheduler(t)
