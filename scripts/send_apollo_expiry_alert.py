#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apollo Daily Expiry & Credit WhatsApp Alert Engine (100% Free)
=============================================================
1. Scans all 19 Apollo accounts live via Apollo REST API.
2. Identifies exact monthly billing cycle expiry dates & renewal times.
3. Formats an executive digest with:
   - ⚠️ URGENT: Accounts expiring within 24-48 hours and credits at risk
   - 📅 UPCOMING: Accounts expiring in the next 7 days
   - 💰 TOTAL: Active credits available across the organization
4. Dispatches daily alert directly to WhatsApp via CallMeBot (100% free, 0 API fees).
5. Provides one-click Windows Task Scheduler installation to run automatically every morning.
"""

from __future__ import annotations

import os
import sys
import json
import csv
import io
import html
import urllib.parse
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure Windows terminal outputs UTF-8 emojis cleanly
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

CONFIG_PATH = PROJECT_ROOT / "config" / "apollo_accounts.json"
IST = timezone(timedelta(hours=5, minutes=30))

WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE_NUMBER", "").strip()
WHATSAPP_APIKEY = os.getenv("WHATSAPP_CALLMEBOT_APIKEY", "").strip()


# =====================================================================
# 1. LIVE APOLLO ACCOUNT & BILLING CYCLE PROBING
# =====================================================================

def probe_single_account(acc: Dict[str, Any]) -> Dict[str, Any]:
    """Query Apollo API for account team_id and exact monthly billing cycle end date."""
    acc_id = acc.get("id")
    email = acc.get("email", "")
    name = acc.get("name", "")
    api_key = acc.get("api_key", "")

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }

    try:
        r_user = requests.get("https://api.apollo.io/api/v1/users/search", headers=headers, timeout=10)
        if r_user.status_code != 200:
            return {"id": acc_id, "email": email, "name": name, "status": f"HTTP {r_user.status_code}"}

        users_data = r_user.json().get("users", [])
        if not users_data:
            return {"id": acc_id, "email": email, "name": name, "status": "No user found"}

        u = users_data[0]
        team_id = u.get("team_id")
        user_created = u.get("created_at")

        r_team = requests.get(f"https://api.apollo.io/api/v1/teams/{team_id}", headers=headers, timeout=10)
        if r_team.status_code != 200:
            return {"id": acc_id, "email": email, "name": name, "status": f"Team HTTP {r_team.status_code}"}

        t = r_team.json().get("team", {})
        billing_end = t.get("current_monthly_billing_cycle_end_date")
        credits_avail = t.get("effective_num_lead_credits", 0) or 0
        credits_used = t.get("num_lead_credits_used", 0) or 0
        bonus_expiry = t.get("additional_email_credits_expires_date")

        return {
            "id": acc_id,
            "email": email,
            "name": name,
            "status": "Active",
            "created_at": user_created,
            "billing_end": billing_end,
            "credits_avail": credits_avail,
            "credits_used": credits_used,
            "credits_remaining": max(0, credits_avail - credits_used),
            "bonus_expiry": bonus_expiry
        }
    except Exception as ex:
        return {"id": acc_id, "email": email, "name": name, "status": f"Error: {ex}"}


def fetch_all_account_expiries(accounts: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Scan all accounts concurrently to collect exact renewal timestamps."""
    if accounts is None:
        if not CONFIG_PATH.exists():
            return []
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            accounts = json.load(f)

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(probe_single_account, acc): acc for acc in accounts}
        for future in as_completed(futures):
            res = future.result()
            if res.get("billing_end"):
                results.append(res)

    # Sort in ascending order of expiry (earliest expiry first)
    results.sort(key=lambda x: x["billing_end"])
    return results


# =====================================================================
# 2. WHATSAPP MESSAGE FORMATTING
# =====================================================================

def format_whatsapp_expiry_message(
    accounts_data: List[Dict[str, Any]],
    now_utc: Optional[datetime] = None
) -> str:
    """Format a clean, readable WhatsApp markdown message with bolding and alerts."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)

    total_credits = sum(acc.get("credits_remaining", 0) for acc in accounts_data)
    date_str = now_ist.strftime("%A, %d %b %Y")

    lines = [
        "🚨 *APOLLO DAILY EXPIRY & CREDIT DIGEST* 🚨",
        f"📅 *{date_str}* | 💰 Total Active Credits: *{total_credits:,d}*",
        ""
    ]

    urgent_items = []
    upcoming_items = []
    later_items = []

    for acc in accounts_data:
        dt_utc = datetime.fromisoformat(acc["billing_end"])
        diff = dt_utc - now_utc
        dt_ist = dt_utc.astimezone(IST)

        acc["dt_ist"] = dt_ist
        acc["diff"] = diff

        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60

        if diff.total_seconds() < 0:
            acc["time_left_str"] = "EXPIRED"
        elif days == 0:
            acc["time_left_str"] = f"{hours}h {minutes}m left (TODAY)"
        elif days == 1:
            acc["time_left_str"] = f"1d {hours}h left (Tomorrow)"
        else:
            acc["time_left_str"] = f"{days}d {hours}h left"

        # Categorize
        if diff.total_seconds() <= 48 * 3600:
            urgent_items.append(acc)
        elif diff.total_seconds() <= 7 * 24 * 3600:
            upcoming_items.append(acc)
        else:
            later_items.append(acc)

    # 1. Urgent Section (Next 48 Hours)
    if urgent_items:
        lines.append("⚠️ *EXPIRING IN NEXT 24–48 HOURS:*")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        for idx, item in enumerate(urgent_items, 1):
            ist_time = item["dt_ist"].strftime("%d %b, %I:%M %p IST")
            clean_email = item["email"].strip()
            rem = item["credits_remaining"]
            avail = item["credits_avail"]
            lines.append(f"🔥 *{idx}. {clean_email}*")
            lines.append(f"   • Expiry: *{ist_time}* ({item['time_left_str']})")
            lines.append(f"   • Credits: *{rem:,d}* / {avail:,d} remaining!")
            lines.append("")
    else:
        lines.append("✅ *NO ACCOUNTS EXPIRING IN THE NEXT 48 HOURS!*")
        lines.append("")

    # 2. Upcoming Section (Next 7 Days)
    if upcoming_items:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📊 *UPCOMING EXPIRIES (NEXT 7 DAYS):*")
        for item in upcoming_items:
            ist_time = item["dt_ist"].strftime("%d %b, %I:%M %p")
            clean_email = item["email"].strip()
            rem = item["credits_remaining"]
            lines.append(f"• *{ist_time}*: {clean_email} (*{rem:,d}* creds)")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚡ _Automated daily alert via Apollo Extension Engine_")

    return "\n".join(lines)


def _expiry_display_fields(
    account: Dict[str, Any],
    now_utc: datetime,
) -> Dict[str, Any]:
    """Return presentation fields shared by the HTML and CSV reports."""
    row = dict(account)
    billing_end = account.get("billing_end")
    row["expiry_ist"] = "No data"
    row["time_left"] = "-"
    row["urgency"] = "unknown"

    if billing_end:
        try:
            dt_utc = datetime.fromisoformat(str(billing_end).replace("Z", "+00:00"))
            dt_ist = dt_utc.astimezone(IST)
            seconds = int((dt_utc - now_utc).total_seconds())
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60

            if seconds <= 0:
                row["time_left"] = "EXPIRED"
                row["urgency"] = "expired"
            elif seconds <= 48 * 3600:
                row["time_left"] = f"{max(days, 0)}d {hours}h {minutes}m"
                row["urgency"] = "urgent"
            elif seconds <= 7 * 86400:
                row["time_left"] = f"{days}d {hours}h"
                row["urgency"] = "upcoming"
            else:
                row["time_left"] = f"{days}d {hours}h"
                row["urgency"] = "healthy"

            row["expiry_ist"] = dt_ist.strftime("%d %b %Y, %I:%M %p IST")
        except (TypeError, ValueError):
            row["expiry_ist"] = "Parse error"

    available = int(account.get("credits_avail", 0) or 0)
    remaining = int(account.get("credits_remaining", 0) or 0)
    row["credits_used"] = max(0, available - remaining)
    return row


def format_email_expiry_report(
    accounts_data: List[Dict[str, Any]],
    now_utc: Optional[datetime] = None,
) -> str:
    """Build the Apollo 19 live credit and expiry report as an HTML email."""
    now_utc = now_utc or datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)
    rows = [_expiry_display_fields(account, now_utc) for account in accounts_data]
    total_credits = sum(int(row.get("credits_remaining", 0) or 0) for row in rows)
    expired = sum(row["urgency"] == "expired" for row in rows)
    urgent = sum(row["urgency"] == "urgent" for row in rows)
    upcoming = sum(row["urgency"] == "upcoming" for row in rows)

    color_by_urgency = {
        "expired": "#ef4444",
        "urgent": "#f59e0b",
        "upcoming": "#fb923c",
        "healthy": "#22c55e",
        "unknown": "#94a3b8",
    }
    account_rows = []
    for index, row in enumerate(rows, 1):
        urgency = row.get("urgency", "unknown")
        color = color_by_urgency.get(urgency, "#94a3b8")
        status = row.get("status", "unknown")
        account_rows.append(
            "<tr>"
            f"<td>{index:02d}</td>"
            f"<td><strong>{html.escape(str(row.get('name', '')))}</strong></td>"
            f"<td>{html.escape(str(row.get('email', '')))}</td>"
            f"<td>{html.escape(str(row.get('expiry_ist', 'No data')))}</td>"
            f"<td style=\"color:{color};font-weight:700\">{html.escape(str(row.get('time_left', '-')))}</td>"
            f"<td style=\"text-align:right;font-weight:700\">{int(row.get('credits_remaining', 0) or 0):,}</td>"
            f"<td style=\"color:{color}\">{html.escape(str(status))}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html><body style="margin:0;background:#0f172a;color:#e2e8f0;font-family:Arial,sans-serif;">
<div style="max-width:1050px;margin:0 auto;padding:24px;">
  <div style="background:#111827;border:1px solid #334155;border-radius:14px;overflow:hidden;">
    <div style="padding:24px;background:linear-gradient(135deg,#172554,#111827);">
      <h1 style="margin:0 0 8px;font-size:24px;color:#f8fafc;">Apollo Accounts Expiring Within the Next 7 Days</h1>
      <div style="color:#94a3b8;font-size:13px;">Live probe generated {now_ist.strftime('%d %b %Y, %I:%M %p IST')}</div>
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;padding:18px 24px;background:#0b1220;">
      <div style="padding:12px 16px;border:1px solid #334155;border-radius:10px;"><strong style="font-size:20px;color:#4ade80;">{total_credits:,}</strong><br><span style="font-size:11px;color:#94a3b8;">Credits in 7-Day Window</span></div>
      <div style="padding:12px 16px;border:1px solid #334155;border-radius:10px;"><strong style="font-size:20px;color:#ef4444;">{expired}</strong><br><span style="font-size:11px;color:#94a3b8;">Expired</span></div>
      <div style="padding:12px 16px;border:1px solid #334155;border-radius:10px;"><strong style="font-size:20px;color:#f59e0b;">{urgent}</strong><br><span style="font-size:11px;color:#94a3b8;">Expiring &lt;48h</span></div>
      <div style="padding:12px 16px;border:1px solid #334155;border-radius:10px;"><strong style="font-size:20px;color:#fb923c;">{upcoming}</strong><br><span style="font-size:11px;color:#94a3b8;">Expiring &lt;7 days</span></div>
    </div>
    <div style="padding:0 24px 24px;overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="background:#1e293b;color:#cbd5e1;">
          <th style="padding:10px;text-align:left;">#</th><th style="padding:10px;text-align:left;">Account</th>
          <th style="padding:10px;text-align:left;">Email / Login</th><th style="padding:10px;text-align:left;">Expiry (IST)</th>
          <th style="padding:10px;text-align:left;">Time Left</th><th style="padding:10px;text-align:right;">Credits</th>
          <th style="padding:10px;text-align:left;">Status</th>
        </tr></thead>
        <tbody>{''.join(account_rows)}</tbody>
      </table>
    </div>
  </div>
</div>
</body></html>"""


def build_expiry_csv(
    accounts_data: List[Dict[str, Any]],
    now_utc: Optional[datetime] = None,
) -> str:
    """Build a downloadable audit attachment for the emailed live report."""
    now_utc = now_utc or datetime.now(timezone.utc)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Account ID", "Account Name", "Email", "Expiry IST", "Time Left",
        "Credits Available", "Credits Used", "Credits Remaining", "Status",
    ])
    for account in accounts_data:
        row = _expiry_display_fields(account, now_utc)
        writer.writerow([
            row.get("id"), row.get("name"), row.get("email"), row.get("expiry_ist"),
            row.get("time_left"), row.get("credits_avail", 0), row.get("credits_used", 0),
            row.get("credits_remaining", 0), row.get("status", "unknown"),
        ])
    return output.getvalue()


def filter_accounts_expiring_within(
    accounts_data: List[Dict[str, Any]],
    now_utc: datetime,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """Keep accounts whose billing expiry is in the next rolling N-day window."""
    cutoff_seconds = days * 24 * 3600
    filtered: List[Dict[str, Any]] = []
    for account in accounts_data:
        billing_end = account.get("billing_end")
        if not billing_end:
            continue
        try:
            expiry_utc = datetime.fromisoformat(str(billing_end).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        seconds_left = (expiry_utc - now_utc).total_seconds()
        if 0 < seconds_left <= cutoff_seconds:
            filtered.append(account)
    return filtered


def run_expiry_report_pipeline(
    channels: Optional[List[str]] = None,
    accounts_data: Optional[List[Dict[str, Any]]] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Probe Apollo once and dispatch the live account report to selected channels."""
    selected = [channel.lower() for channel in (channels or ["email"])]
    if accounts_data is None:
        from scripts.apollo_account_report import fetch_all_accounts_live
        accounts_data = fetch_all_accounts_live()

    if not accounts_data:
        return {"status": "error", "message": "No Apollo account data retrieved", "channels": {}}

    now_utc = now_utc or datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)
    expiring_accounts = filter_accounts_expiring_within(accounts_data, now_utc, days=7)
    total_credits = sum(int(account.get("credits_remaining", 0) or 0) for account in expiring_accounts)
    results: Dict[str, Any] = {
        "status": "ok",
        "timestamp": now_ist.strftime("%d %b %Y, %I:%M %p IST"),
        "total_accounts_probed": len(accounts_data),
        "total_accounts": len(expiring_accounts),
        "window_days": 7,
        "total_credits": total_credits,
        "channels": {},
    }

    if "email" in selected:
        from scripts.send_saving_report import dispatch_email
        subject = (
            f"[Apollo Credit & Expiry Report] {now_ist.strftime('%d %b %Y')} "
            f"• {len(expiring_accounts)} Expiring Within 7 Days • {total_credits:,} Credits Remaining"
        )
        results["channels"]["email"] = dispatch_email(
            subject,
            format_email_expiry_report(expiring_accounts, now_utc),
            build_expiry_csv(expiring_accounts, now_utc),
            attachment_filename=f"apollo_credit_expiry_{now_ist.strftime('%Y%m%d_%H%M%S')}.csv",
        )

    if "whatsapp" in selected:
        accounts_with_expiry = [account for account in accounts_data if account.get("billing_end")]
        message = format_whatsapp_expiry_message(accounts_with_expiry, now_utc=now_utc)
        results["channels"]["whatsapp"] = send_whatsapp_alert(message)

    return results


# =====================================================================
# 3. DISPATCH ENGINE (CALLMEBOT API)
# =====================================================================

def send_whatsapp_alert(
    message: str,
    phone: Optional[str] = None,
    apikey: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Send WhatsApp message using CallMeBot Free API.
    URL: https://api.callmebot.com/whatsapp.php?phone=[phone]&text=[message]&apikey=[apikey]
    """
    target_phone = phone or WHATSAPP_PHONE
    target_apikey = apikey or WHATSAPP_APIKEY

    if dry_run:
        print("\n[DRY RUN MODE] - WhatsApp Message Preview:")
        print("=" * 70)
        print(message)
        print("=" * 70)
        return {"status": "dry_run", "message": "Preview printed successfully"}

    if not target_phone or not target_apikey:
        print("\n[!] WhatsApp phone number or CallMeBot API key is not configured.")
        print("    Add them to .env:")
        print("    WHATSAPP_PHONE_NUMBER=+919876543210")
        print("    WHATSAPP_CALLMEBOT_APIKEY=123456")
        print("\n--- Message that would be sent ---")
        print(message)
        return {"status": "unconfigured", "error": "Missing phone number or API key"}

    # CallMeBot requires standard URL encoded query parameters
    url = "https://api.callmebot.com/whatsapp.php"
    params = {
        "phone": target_phone,
        "text": message,
        "apikey": target_apikey
    }

    try:
        res = requests.get(url, params=params, timeout=20)
        if res.status_code == 200:
            print(f"\n[✓] WhatsApp alert successfully delivered to {target_phone}!")
            return {"status": "success", "response": res.text[:200]}
        else:
            print(f"\n[!] CallMeBot returned status HTTP {res.status_code}: {res.text[:200]}")
            return {"status": "error", "http_code": res.status_code, "response": res.text[:200]}
    except Exception as ex:
        print(f"\n[!] Failed to deliver WhatsApp alert: {ex}")
        return {"status": "error", "error": str(ex)}


# =====================================================================
# 4. WINDOWS TASK SCHEDULER REGISTRATION
# =====================================================================

def setup_windows_scheduler(hour: int = 8, minute: int = 30) -> None:
    """Register daily task in Windows Task Scheduler using schtasks."""
    import subprocess
    task_name = "ApolloDailyWhatsAppAlert"
    python_exe = sys.executable
    script_path = str(PROJECT_ROOT / "scripts" / "send_apollo_expiry_alert.py")

    time_str = f"{hour:02d}:{minute:02d}"

    # Command: schtasks /create /tn ApolloDailyWhatsAppAlert /tr "python .../send_apollo_expiry_alert.py" /sc daily /st 08:30 /f
    tr_command = f'"{python_exe}" "{script_path}"'
    cmd = [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", tr_command,
        "/sc", "daily",
        "/st", time_str,
        "/f"
    ]

    print(f"\nRegistering Windows Scheduled Task: '{task_name}' at {time_str} every morning...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"[✓] Task successfully created in Windows Task Scheduler!")
        print(f"    • Task Name: {task_name}")
        print(f"    • Trigger  : Daily at {time_str} IST")
        print(f"    • Command  : {tr_command}")
    else:
        print(f"[!] Could not register task: {res.stderr or res.stdout}")


# =====================================================================
# 5. CLI CONTROLLER
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Apollo Daily Expiry & Credit WhatsApp Alert Engine")
    parser.add_argument("--dry-run", action="store_true", help="Print WhatsApp message preview without sending")
    parser.add_argument("--phone", type=str, help="Recipient phone number with country code (e.g. +91...)")
    parser.add_argument("--apikey", type=str, help="CallMeBot API key")
    parser.add_argument("--schedule", action="store_true", help="Register Windows Task Scheduler to run daily at 8:30 AM")
    parser.add_argument("--time", type=str, default="08:30", help="Scheduled time in HH:MM (default: 08:30)")
    parser.add_argument(
        "--channels",
        type=str,
        default="whatsapp",
        help="Comma-separated dispatch channels: email,whatsapp (default: whatsapp)",
    )
    args = parser.parse_args()

    if args.schedule:
        try:
            h, m = map(int, args.time.split(":"))
            setup_windows_scheduler(hour=h, minute=m)
        except Exception as e:
            print(f"[!] Invalid time format: {e}. Use HH:MM, e.g. 08:30")
        return

    selected_channels = [item.strip().lower() for item in args.channels.split(",") if item.strip()]
    if args.dry_run and selected_channels == ["whatsapp"]:
        print("\nScanning all 19 Apollo accounts live for billing cycles & credits...")
        accounts_data = fetch_all_account_expiries()
        if not accounts_data:
            print("[!] No account expiry data retrieved. Check config/apollo_accounts.json and connection.")
            return
        send_whatsapp_alert(
            message=format_whatsapp_expiry_message(accounts_data),
            phone=args.phone,
            apikey=args.apikey,
            dry_run=True,
        )
        return

    print("\nScanning all 19 Apollo accounts live for billing cycles & credits...")
    result = run_expiry_report_pipeline(channels=selected_channels)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
