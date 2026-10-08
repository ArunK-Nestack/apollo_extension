#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apollo Account Credit & Expiry Report
======================================
Probes all 19 Apollo accounts live via the REST API and prints a rich CLI
table showing:

  #  | Account Name       | Email                          | Expiry (IST)        | Time Left     | Credits Left
  ---+--------------------|--------------------------------|---------------------|---------------|--------------
  01 | Abel Abraham       | abel.abraham@nestack...        | 25 Sep 2026 08:30   | 4d 18h 22m    | 2,724

Run standalone:
    python scripts/apollo_account_report.py

Or import and call:
    from scripts.apollo_account_report import run_account_report
    run_account_report()
"""

from __future__ import annotations

import json
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

# ── Bootstrap ───────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

CONFIG_PATH = PROJECT_ROOT / "config" / "apollo_accounts.json"
CACHE_PATH = PROJECT_ROOT / "config" / "apollo_live_account_report.json"
load_dotenv(PROJECT_ROOT / ".env")
IST = timezone(timedelta(hours=5, minutes=30))


# ── Apollo Live Probe ────────────────────────────────────────────────────────

def _probe_account(acc: Dict[str, Any]) -> Dict[str, Any]:
    """Hit Apollo REST API to get billing cycle end date and credit balance."""
    api_key = acc.get("api_key", "")
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key,
    }
    base = {
        "id":    acc.get("id"),
        "name":  acc.get("name", ""),
        "email": acc.get("email", ""),
        "status": "error",
        "billing_end": None,
        "credits_remaining": 0,
        "credits_avail": 0,
    }
    try:
        r = requests.get(
            "https://api.apollo.io/api/v1/users/search",
            headers=headers, timeout=12
        )
        if r.status_code != 200:
            base["status"] = f"HTTP {r.status_code}"
            return base

        users = r.json().get("users", [])
        if not users:
            base["status"] = "No user"
            return base

        team_id = users[0].get("team_id")
        r2 = requests.get(
            f"https://api.apollo.io/api/v1/teams/{team_id}",
            headers=headers, timeout=12
        )
        if r2.status_code != 200:
            base["status"] = f"Team HTTP {r2.status_code}"
            return base

        t = r2.json().get("team", {})
        avail = t.get("effective_num_lead_credits", 0) or 0
        if t.get("using_unified_credits", False):
            used = t.get("total_unified_credits_used", 0) or 0
        else:
            used = t.get("num_lead_credits_used", 0) or 0
        return {
            **base,
            "status":            "active",
            "billing_end":       t.get("current_monthly_billing_cycle_end_date"),
            "credits_avail":     avail,
            "credits_remaining": max(0, avail - used),
            "team_id":           team_id,
        }
    except Exception as ex:
        base["status"] = f"Error: {ex}"
        return base


def _load_cached_report() -> Dict[str, Any]:
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def merge_probe_results_with_cache(
    live_results: List[Dict[str, Any]],
    cached_report: Optional[Dict[str, Any]] = None,
    successful_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Keep the last known good expiry and credits when a free probe fails."""
    cached_report = cached_report or {}
    cached_generated_at = str(cached_report.get("generated_at") or "")
    cached_map = {
        str(row.get("email") or "").strip().lower(): row
        for row in cached_report.get("accounts", [])
        if isinstance(row, dict) and row.get("email")
    }
    success_time = successful_at or datetime.now(timezone.utc).astimezone(IST).strftime(
        "%d %b %Y %I:%M:%S %p IST"
    )
    merged: List[Dict[str, Any]] = []
    for live in live_results:
        email_key = str(live.get("email") or "").strip().lower()
        if live.get("status") == "active" and live.get("billing_end"):
            merged.append({
                **live,
                "source": "live",
                "is_stale": False,
                "last_success_at": success_time,
            })
            continue

        cached = cached_map.get(email_key, {})
        cached_expiry = cached.get("expiry_utc") or cached.get("billing_end")
        if cached_expiry:
            merged.append({
                **cached,
                "id": live.get("id", cached.get("id")),
                "name": live.get("name") or cached.get("name", ""),
                "email": live.get("email") or cached.get("email", ""),
                "status": "active",
                "billing_end": cached_expiry,
                "source": "last_good_cache",
                "is_stale": True,
                "probe_status": live.get("status", "error"),
                "last_success_at": cached.get("last_success_at") or cached_generated_at,
            })
        else:
            merged.append({
                **live,
                "source": "unavailable",
                "is_stale": True,
                "probe_status": live.get("status", "error"),
            })
    return merged


def fetch_raw_accounts_probe() -> List[Dict[str, Any]]:
    """Probe accounts in a standalone worker pool."""
    if not CONFIG_PATH.exists():
        return []
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        accounts: List[Dict[str, Any]] = json.load(f)
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_probe_account, acc): acc for acc in accounts}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda x: x.get("id") or 999)
    return results


def fetch_all_accounts_live() -> List[Dict[str, Any]]:
    """Probe all accounts, preserving last-good values for failed probes."""
    results = fetch_raw_accounts_probe()

    # If all probes failed due to socket restrictions (e.g. WinError 10013 in background process)
    if not any(r.get("status") == "active" for r in results):
        try:
            import subprocess
            cmd = [
                sys.executable,
                "-c",
                "import json; from scripts.apollo_account_report import fetch_raw_accounts_probe; "
                "print('__PROBE_JSON_START__' + json.dumps(fetch_raw_accounts_probe()) + '__PROBE_JSON_END__')",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if proc.returncode == 0 and "__PROBE_JSON_START__" in proc.stdout:
                raw_part = proc.stdout.split("__PROBE_JSON_START__")[1].split("__PROBE_JSON_END__")[0]
                sub_results = json.loads(raw_part)
                if any(r.get("status") == "active" for r in sub_results):
                    results = sub_results
        except Exception:
            pass

    return merge_probe_results_with_cache(results, _load_cached_report())


# ── Time Helpers ─────────────────────────────────────────────────────────────

def _time_left_str(diff: timedelta) -> str:
    if diff.total_seconds() <= 0:
        return "EXPIRED"
    total_s = int(diff.total_seconds())
    days    = total_s // 86400
    hours   = (total_s % 86400) // 3600
    minutes = (total_s % 3600) // 60
    if days == 0 and hours == 0:
        return f"{minutes}m  [!] TODAY"
    if days == 0:
        return f"{hours}h {minutes}m  [!] TODAY"
    if days == 1:
        return f"1d {hours}h {minutes}m  [>] TOMORROW"
    if days <= 3:
        return f"{days}d {hours}h {minutes}m  [~]"
    return f"{days}d {hours}h {minutes}m"


RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"


def _urgency_color(diff: timedelta) -> str:
    s = diff.total_seconds()
    if s <= 0:           return RED
    if s <= 48 * 3600:   return YELLOW
    if s <= 7 * 86400:   return CYAN
    return ""


# ── CLI Report Renderer ───────────────────────────────────────────────────────

def run_account_report(accounts: Optional[List[Dict[str, Any]]] = None) -> None:
    """Fetch live data and print the full 19-account credit & expiry table."""
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)

    if accounts is None:
        accounts = fetch_all_accounts_live()

    if not accounts:
        print("[!] No account data retrieved.")
        return

    # ── Pre-compute display fields ───────────────────────────────────────────
    rows: List[Dict[str, Any]] = []
    total_credits = 0
    for acc in accounts:
        row: Dict[str, Any] = dict(acc)
        be = acc.get("billing_end")
        if be:
            try:
                dt_utc = datetime.fromisoformat(be.replace("Z", "+00:00"))
                dt_ist = dt_utc.astimezone(IST)
                diff   = dt_utc - now_utc
                row["expiry_ist"]  = dt_ist.strftime("%d %b %Y  %I:%M %p")
                row["expiry_utc"]  = dt_utc.isoformat()
                row["time_left"]   = _time_left_str(diff)
                row["diff"]        = diff
                row["color"]       = _urgency_color(diff)
            except Exception:
                row["expiry_ist"] = "Parse error"
                row["time_left"]  = "-"
                row["diff"]       = timedelta(days=999)
                row["color"]      = DIM
        else:
            row["expiry_ist"] = "No data"
            row["time_left"]  = "-"
            row["diff"]       = timedelta(days=999)
            row["color"]      = DIM

        total_credits += acc.get("credits_remaining", 0)
        rows.append(row)

    # Persist live probe results to cache
    try:
        clean_rows = []
        for r in rows:
            clean_r = {k: v for k, v in r.items() if k not in ("color", "diff")}
            clean_rows.append(clean_r)

        payload = {
            "status": "ok",
            "generated_at": now_ist.strftime("%d %b %Y %I:%M:%S %p IST"),
            "total_credits": total_credits,
            "total_accounts": len(clean_rows),
            "fresh_accounts": sum(1 for a in clean_rows if not a.get("is_stale")),
            "stale_accounts": sum(1 for a in clean_rows if a.get("is_stale")),
            "accounts": clean_rows,
        }
        tmp_cache = CACHE_PATH.with_suffix(".json.tmp")
        with open(tmp_cache, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        tmp_cache.replace(CACHE_PATH)
    except Exception:
        pass

    # ── Header ───────────────────────────────────────────────────────────────
    W = 114
    print()
    print("=" * W)
    print(f"{BOLD}{'APOLLO ACCOUNT CREDIT & EXPIRY REPORT':^{W}}{RESET}")
    print("=" * W)
    print(f"  {DIM}Generated : {now_ist.strftime('%A, %d %b %Y  %I:%M:%S %p IST')}{RESET}")
    print(f"  {GREEN}{BOLD}Total Credits Available Across All Accounts: {total_credits:,d}{RESET}")
    print("=" * W)

    # Column widths
    C_IDX    = 3
    C_NAME   = 20
    C_EMAIL  = 42
    C_EXPIRY = 22
    C_LEFT   = 24
    C_CREDS  = 12

    header = (
        f"  {'#':>{C_IDX}}  "
        f"{'Account Name':<{C_NAME}}  "
        f"{'Email / Login':<{C_EMAIL}}  "
        f"{'Expiry Date (IST)':<{C_EXPIRY}}  "
        f"{'Time Left':<{C_LEFT}}  "
        f"{'Credits':>{C_CREDS}}"
    )
    sep = "  " + "-" * (W - 2)

    print(f"{BOLD}{WHITE}{header}{RESET}")
    print(sep)

    # ── Rows ─────────────────────────────────────────────────────────────────
    expired_count  = 0
    urgent_count   = 0
    upcoming_count = 0

    for row in rows:
        idx    = row.get("id", "?")
        name   = (row.get("name") or "")[:C_NAME]
        email  = (row.get("email") or "")[:C_EMAIL]
        expiry = row.get("expiry_ist", "No data")[:C_EXPIRY]
        left   = row.get("time_left", "-")[:C_LEFT]
        creds  = row.get("credits_remaining", 0)
        color  = row.get("color", "")
        status = row.get("status", "")

        # Count urgency
        diff_val: timedelta = row.get("diff", timedelta(days=999))
        s = diff_val.total_seconds()
        if   s <= 0:               expired_count  += 1
        elif s <= 48 * 3600:       urgent_count   += 1
        elif s <= 7 * 24 * 3600:   upcoming_count += 1

        # Non-active rows: show raw status
        if status not in ("active",) and expiry == "No data":
            expiry = str(status)[:C_EXPIRY]
            left   = "-"
            color  = DIM

        creds_str = f"{creds:,d}" if creds else "0"

        print(
            f"  {color}"
            f"{idx:>{C_IDX}}  "
            f"{name:<{C_NAME}}  "
            f"{email:<{C_EMAIL}}  "
            f"{expiry:<{C_EXPIRY}}  "
            f"{left:<{C_LEFT}}  "
            f"{creds_str:>{C_CREDS}}"
            f"{RESET}"
        )

    # ── Summary Bar ──────────────────────────────────────────────────────────
    print(sep)

    summary_parts = []
    if expired_count:
        summary_parts.append(f"{RED}[X] {expired_count} EXPIRED{RESET}")
    if urgent_count:
        summary_parts.append(f"{YELLOW}[!] {urgent_count} expiring within 48h{RESET}")
    if upcoming_count:
        summary_parts.append(f"{CYAN}[~] {upcoming_count} expiring within 7 days{RESET}")

    if summary_parts:
        print(f"  {' | '.join(summary_parts)}")
    else:
        print(f"  {GREEN}[OK] All accounts healthy - no imminent expiries.{RESET}")

    print(f"  {BOLD}{GREEN}Total Credits: {total_credits:,d}{RESET}")
    print("=" * W)
    print()


# ── Standalone Entry Point ────────────────────────────────────────────────────

if __name__ == "__main__":
    run_account_report()
