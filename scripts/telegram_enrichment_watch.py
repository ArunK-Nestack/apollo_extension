"""Minute-by-minute Telegram watcher for an active enrichment run."""

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from backend.api import get_connection


POLL_SECONDS = 60
STALL_SECONDS = 120


def telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15,
        )
        return response.ok
    except Exception:
        return False


def snapshot_runs():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT run_id, login_name, batch_tag, attempted_leads, number_enriched,
                          number_not_enriched, credits_used, credits_remaining_before,
                          run_status, started_at, updated_at
                   FROM enrichment_run_audit
                   WHERE run_status = 'running'
                   ORDER BY started_at DESC"""
            )
            runs = cur.fetchall()
            result = []
            for run in runs:
                login_email = run[1]
                cur.execute(
                    """SELECT COUNT(*) FROM batch_enrichment_ledger
                       WHERE LOWER(login_email) = LOWER(%s)
                         AND attempted_at >= CURDATE() - INTERVAL 2 DAY
                         AND attempted_at < CURDATE() + INTERVAL 1 DAY
                         AND outcome = 'email_found'""",
                    (login_email,),
                )
                result.append((run, int(cur.fetchone()[0] or 0)))
            return result


def format_alert(run, recent_enriched: int, prefix: str) -> str:
    run_id, login_email, batch, attempted, enriched, no_email, spent, before, status, started, updated = run
    remaining = max(0.0, float(before or 0) - float(spent or 0))
    return (
        f"{prefix}\n"
        f"Login: {login_email}\n"
        f"Progress: {attempted:,} attempted | {enriched:,} enriched | {no_email:,} no email\n"
        f"Enriched past 3 days: {recent_enriched:,}\n"
        f"Credits remaining: {remaining:,.2f}\n"
        f"Status: {status}"
    )


def main() -> None:
    state = {}

    while True:
        try:
            data = snapshot_runs()
        except Exception:
            data = []

        active_ids = set()
        for run, recent_enriched in data:
            run_id = run[0]
            active_ids.add(run_id)
            is_new = run_id not in state
            current = state.setdefault(
                run_id,
                {"updated": run[10], "last_progress": time.monotonic(), "rate_alerted": False},
            )
            if is_new:
                telegram(format_alert(run, recent_enriched, "Apollo enrichment session started"))
            elif current["updated"] != run[10]:
                current["updated"] = run[10]
                current["last_progress"] = time.monotonic()
                current["rate_alerted"] = False
                telegram(format_alert(run, recent_enriched, "Apollo enrichment progress update"))
            elif time.monotonic() - current["last_progress"] >= STALL_SECONDS and not current["rate_alerted"]:
                current["rate_alerted"] = True
                telegram(format_alert(run, recent_enriched, "Apollo rate limit suspected: no progress for 2+ minutes"))

        for run_id in list(state):
            if run_id not in active_ids:
                del state[run_id]

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
