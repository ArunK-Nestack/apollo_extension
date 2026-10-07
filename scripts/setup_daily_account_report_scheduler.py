#!/usr/bin/env python3
"""Register the Apollo 19-account credit/expiry email for 10:30 AM daily."""

import subprocess
import sys
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = sys.executable
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "send_apollo_expiry_alert.py"
TASK_NAME = "ApolloDailyAccountCreditExpiryReport1030AM"
DISPATCH_TIME = "10:30"


def register_scheduler(task_time: str = DISPATCH_TIME) -> bool:
    """Create or update the Windows task using the machine's local timezone."""
    uv_exe = shutil.which("uv")
    if uv_exe:
        tr_command = (
            f'"{uv_exe}" run --no-project --with requests --with python-dotenv '
            f'python "{SCRIPT_PATH}" --channels email'
        )
    else:
        tr_command = f'"{PYTHON_EXE}" "{SCRIPT_PATH}" --channels email'
    cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", tr_command,
        "/sc", "daily",
        "/st", task_time,
        "/f",
    ]

    print(f"Registering Windows Task '{TASK_NAME}' at {task_time} daily...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[OK] Task '{TASK_NAME}' registered at {task_time} local server time.")
        print(f"     Action: {tr_command}")
        return True

    print(f"[ERROR] Could not register task: {result.stderr or result.stdout}")
    return False


if __name__ == "__main__":
    requested_time = sys.argv[1] if len(sys.argv) > 1 else DISPATCH_TIME
    raise SystemExit(0 if register_scheduler(requested_time) else 1)
