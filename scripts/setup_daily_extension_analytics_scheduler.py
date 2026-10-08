#!/usr/bin/env python3
"""Register the extension analytics email report for 6:00 PM daily."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "send_extension_analytics_report.py"
TASK_NAME = "ApolloDailyExtensionAnalyticsReport6PM"
DISPATCH_TIME = "18:00"


def register_scheduler(task_time: str = DISPATCH_TIME) -> bool:
    """Create or update the task in the Windows machine's local timezone."""
    tr_command = f'"{PYTHON_EXE}" "{SCRIPT_PATH}" --channels email'
    command = [
        "schtasks", "/create", "/tn", TASK_NAME, "/tr", tr_command,
        "/sc", "daily", "/st", task_time, "/f",
    ]
    print(f"Registering Windows Task '{TASK_NAME}' at {task_time} daily...")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[OK] Task '{TASK_NAME}' registered at {task_time} local server time.")
        print(f"     Action: {tr_command}")
        return True
    print(f"[ERROR] Could not register task: {result.stderr or result.stdout}")
    return False


if __name__ == "__main__":
    requested_time = sys.argv[1] if len(sys.argv) > 1 else DISPATCH_TIME
    raise SystemExit(0 if register_scheduler(requested_time) else 1)
