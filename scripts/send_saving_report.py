#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apollo Saving Ledger Multi-Channel Report Dispatcher
===================================================
1. Queries the active October cycle ledger and all connected accounts.
2. Generates:
   - Responsive HTML Executive Email Report + CSV Attachment
   - Mobile-optimized WhatsApp Text Digest (CallMeBot)
   - Microsoft Teams Adaptive / Connector Card (Incoming Webhook)
3. Dispatches across Email, WhatsApp, and Teams manually or on a 5:00 PM daily schedule.
"""

from __future__ import annotations

import os
import sys
import csv
import io
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))


def build_report_data() -> Dict[str, Any]:
    """Fetches the official saving summary data from backend.dashboard_routes."""
    try:
        from backend.dashboard_routes import get_saving_summary
        data = get_saving_summary(filter_cycle="october", active_only=True)
        return data
    except Exception as e:
        print(f"[Dispatcher] Error loading saving summary: {e}", flush=True)
        return {"status": "error", "message": str(e), "records": []}


def format_whatsapp_message(report_data: Dict[str, Any], timestamp_str: str) -> str:
    """Formats a concise, emoji-rich WhatsApp summary."""
    records = report_data.get("records", [])
    total_leads = report_data.get("total_saved_leads", 0)
    total_web = report_data.get("total_saved_web", 0)
    total_enriched = report_data.get("total_enriched", 0)
    total_emails = report_data.get("total_emails_saved", 0)
    total_creds = report_data.get("total_credits_used", 0)

    yield_pct = f"{(total_emails / max(1, total_leads)) * 100:.1f}%" if total_leads > 0 else "0.0%"

    lines = [
        "📊 *APOLLO DAILY SAVING & CREDIT DIGEST*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 *Report Generated:* {timestamp_str}",
        "⚙️ *Cycle Filter:* Active October 2026 Cycle",
        "",
        "📈 *ORGANIZATION EXECUTIVE TOTALS:*",
        f"• 🌐 Total Contacts:      *{total_leads:,d}*",
        f"• 📥 Saved from Web:       *{total_web:,d}*",
        f"• ⚡ Enriched with Credits: *{total_enriched:,d}*",
        f"• 💳 Apollo Credits Used:   *{total_creds:,d}*",
        f"• ✉️ Saved Emails:         *{total_emails:,d}* ({yield_pct} yield)",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📋 *ACTIVE ACCOUNT BREAKDOWN:*"
    ]

    active_records = [r for r in records if (r.get("total_leads", 0) > 0 or r.get("enriched_here", 0) > 0)]
    if active_records:
        for idx, r in enumerate(active_records, 1):
            nm = r.get("name", "Account")
            em = r.get("email", "")
            time_left = r.get("time_left", "")
            web = r.get("saved_from_web", 0)
            enr = r.get("enriched_here", 0)
            creds = r.get("credits_used", 0)
            enr_creds = r.get("enrichment_credits", 0)
            emails = r.get("saved_emails", 0)
            tot = r.get("total_leads", 0)

            lines.append(f"*{idx}. {nm}* ({em})")
            if time_left:
                lines.append(f"   • ⏱ Expiry Timer: *{time_left}*")
            lines.append(f"   • 📥 Web: *{web:,d}* | ⚡ Enriched: *{enr:,d}*")
            creds_str = f"*{creds:,d}*" + (f" ({enr_creds:,d} enriched)" if enr_creds and enr_creds != creds else "")
            lines.append(f"   • 💳 Credits: {creds_str} | ✉️ Emails: *{emails:,d}*")
            lines.append(f"   • 📦 Total Contacts: *{tot:,d}*")
            lines.append("")
    else:
        lines.append("ℹ️ _No active saves or enrichments in this cycle yet._")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚡ _Automated report via Apollo Operations Engine_")
    return "\n".join(lines)


def format_teams_card(report_data: Dict[str, Any], timestamp_str: str) -> Dict[str, Any]:
    """Builds an Office 365 / Microsoft Teams Connector Card payload."""
    total_leads = report_data.get("total_saved_leads", 0)
    total_web = report_data.get("total_saved_web", 0)
    total_enriched = report_data.get("total_enriched", 0)
    total_emails = report_data.get("total_emails_saved", 0)
    total_creds = report_data.get("total_credits_used", 0)
    yield_pct = f"{(total_emails / max(1, total_leads)) * 100:.1f}%" if total_leads > 0 else "0.0%"

    facts = [
        {"name": "Total Contacts Saved", "value": f"{total_leads:,d}"},
        {"name": "Saved from Web", "value": f"{total_web:,d}"},
        {"name": "Enriched Here (Credits)", "value": f"{total_enriched:,d}"},
        {"name": "Apollo Credits Spent", "value": f"{total_creds:,d}"},
        {"name": "Verified Emails Saved", "value": f"{total_emails:,d} ({yield_pct})"},
        {"name": "Report Timestamp", "value": timestamp_str},
    ]

    active_records = [r for r in report_data.get("records", []) if r.get("total_leads", 0) > 0]
    account_facts = []
    for r in active_records:
        account_facts.append({
            "name": f"{r.get('name')} ({r.get('email')})",
            "value": f"Web: {r.get('saved_from_web', 0):,d} | Enriched: {r.get('enriched_here', 0):,d} | Credits: {r.get('credits_used', 0):,d} | Emails: {r.get('saved_emails', 0):,d} | Total: {r.get('total_leads', 0):,d}"
        })

    sections = [
        {
            "activityTitle": "Executive Metrics Summary",
            "facts": facts,
            "markdown": True
        }
    ]

    if account_facts:
        sections.append({
            "activityTitle": "Active Logins Breakdown",
            "facts": account_facts,
            "markdown": True
        })

    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0EA5E9",
        "summary": f"Apollo Saving Ledger Report - {total_leads:,d} Contacts",
        "title": "🟣 Apollo Saving Ledger & Credit Report",
        "sections": sections,
        "potentialAction": [
            {
                "@type": "OpenUri",
                "name": "Open Apollo Dashboard",
                "targets": [{"os": "default", "uri": "http://localhost:8000"}]
            }
        ]
    }
    return card


def format_email_html(report_data: Dict[str, Any], timestamp_str: str) -> str:
    """Renders a responsive, executive HTML email digest."""
    total_leads = report_data.get("total_saved_leads", 0)
    total_web = report_data.get("total_saved_web", 0)
    total_enriched = report_data.get("total_enriched", 0)
    total_emails = report_data.get("total_emails_saved", 0)
    total_creds = report_data.get("total_credits_used", 0)
    yield_pct = f"{(total_emails / max(1, total_leads)) * 100:.1f}%" if total_leads > 0 else "0.0%"

    active_records = [r for r in report_data.get("records", []) if r.get("total_leads", 0) > 0]

    rows_html = ""
    for idx, r in enumerate(active_records, 1):
        num = f"#{idx:02d}"
        rows_html += f"""
        <tr style="border-bottom: 1px solid #2d3748;">
            <td style="padding: 10px 12px; font-family: monospace; color: #94a3b8; font-weight: bold;">{num}</td>
            <td style="padding: 10px 12px;">
                <div style="font-weight: 600; color: #f8fafc;">{r.get('name')}</div>
                <div style="font-size: 11px; font-family: monospace; color: #94a3b8;">{r.get('email')}</div>
                <div style="font-size: 10px; color: #38bdf8; margin-top: 2px;">⏱ {r.get('time_left', '—')} ({r.get('cycle', '')})</div>
            </td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #c084fc; font-weight: 600;">{r.get('saved_from_web', 0):,d}</td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #4ade80; font-weight: 600;">{r.get('enriched_here', 0):,d}</td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #fbbf24; font-weight: bold;">
                <div>{r.get('credits_used', 0):,d}</div>
                {f"<div style='font-size:10px; color:#94a3b8; font-weight:normal;'>({r.get('enrichment_credits', 0):,d} enriched)</div>" if r.get('enrichment_credits') and r.get('enrichment_credits') != r.get('credits_used') else ""}
            </td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #34d399; font-weight: bold;">{r.get('saved_emails', 0):,d}</td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #38bdf8; font-weight: 800; font-size: 13px;">{r.get('total_leads', 0):,d}</td>
        </tr>
        """

    if not rows_html:
        rows_html = '<tr><td colspan="7" style="padding: 24px; text-align: center; color: #94a3b8;">No active records in this cycle.</td></tr>'

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
  .container {{ max-width: 820px; margin: 0 auto; background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; }}
  .header {{ background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%); padding: 24px 32px; }}
  .title {{ font-size: 20px; font-weight: 800; color: #ffffff; margin: 0; }}
  .subtitle {{ font-size: 13px; color: rgba(255,255,255,0.85); margin-top: 4px; }}
  .kpi-row {{ display: table; width: 100%; border-bottom: 1px solid #334155; background: #182234; }}
  .kpi-cell {{ display: table-cell; padding: 18px 16px; text-align: center; border-right: 1px solid #334155; }}
  .kpi-cell:last-child {{ border-right: none; }}
  .kpi-val {{ font-size: 22px; font-weight: 800; font-family: monospace; }}
  .kpi-lbl {{ font-size: 11px; text-transform: uppercase; color: #94a3b8; font-weight: 600; margin-top: 4px; letter-spacing: 0.5px; }}
  .table-wrapper {{ padding: 24px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th {{ background: #0f172a; color: #94a3b8; text-transform: uppercase; font-size: 10.5px; padding: 10px 12px; letter-spacing: 0.5px; text-align: left; }}
  .footer {{ padding: 16px 24px; background: #0f172a; border-top: 1px solid #334155; font-size: 11px; color: #64748b; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="title">📊 Apollo Daily Saving Ledger & Credit Digest</div>
    <div class="subtitle">Generated on {timestamp_str} • Active October 2026 Billing Cycle</div>
  </div>

  <div class="kpi-row">
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#38bdf8;">{total_leads:,d}</div>
      <div class="kpi-lbl">Total Contacts</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#c084fc;">{total_web:,d}</div>
      <div class="kpi-lbl">Saved from Web</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#4ade80;">{total_enriched:,d}</div>
      <div class="kpi-lbl">Enriched Here</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#fbbf24;">{total_creds:,d}</div>
      <div class="kpi-lbl">Credits Used</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#34d399;">{total_emails:,d}</div>
      <div class="kpi-lbl">Emails ({yield_pct})</div>
    </div>
  </div>

  <div class="table-wrapper">
    <div style="font-size: 13px; font-weight: 700; color: #f8fafc; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between;">
      <span>Connected Logins Activity Breakdown</span>
      <span style="font-size: 11px; color: #94a3b8; font-weight: normal;">{len(active_records)} Active Account(s)</span>
    </div>
    <table>
      <thead>
        <tr>
          <th style="width: 40px;">#</th>
          <th>Account & Cycle</th>
          <th style="text-align: right;">Saved from Web</th>
          <th style="text-align: right;">Enriched</th>
          <th style="text-align: right;">Credits</th>
          <th style="text-align: right;">Emails</th>
          <th style="text-align: right;">Total Contacts</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>

  <div class="footer">
    Sent automatically by Apollo Intelligence Engine • Access live dashboard at <a href="http://localhost:8000" style="color: #38bdf8; text-decoration: none;">http://localhost:8000</a>
  </div>
</div>
</body>
</html>
"""
    return html


def build_csv_attachment(report_data: Dict[str, Any]) -> str:
    """Generates CSV string of the active accounts."""
    records = report_data.get("records", [])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Account ID", "Name", "Email", "Billing Cycle", "Expiry IST", "Time Left",
        "Saved from Web", "Enriched Here", "Credits Used", "Saved Emails", "Total Contacts", "Last Saved"
    ])
    for r in records:
        writer.writerow([
            r.get("id"),
            r.get("name"),
            r.get("email"),
            r.get("cycle"),
            r.get("expiry_ist"),
            r.get("time_left"),
            r.get("saved_from_web", 0),
            r.get("enriched_here", 0),
            r.get("credits_used", 0),
            r.get("saved_emails", 0),
            r.get("total_leads", 0),
            r.get("last_saved_str", "")
        ])
    return output.getvalue()


# =====================================================================
# DISPATCH ENGINES
# =====================================================================

def dispatch_email(
    subject: str,
    html_content: str,
    csv_content: Optional[str] = None,
    to_emails: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Sends email via SMTP to configured recipient(s)."""
    raw_to = os.getenv("REPORT_EMAIL_TO", "")
    recipients = to_emails or [e.strip() for e in raw_to.replace(";", ",").split(",") if e.strip()]

    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    pwd = os.getenv("SMTP_PASSWORD", "").strip()
    from_name = os.getenv("SMTP_FROM_NAME", "Apollo Intelligence Reports").strip('\"\'')

    if not recipients:
        return {"status": "skipped", "message": "No recipient email configured in REPORT_EMAIL_TO"}

    # 1. Prefer Google Apps Script Web App (0 passwords, 0 2FA required)
    apps_script_url = os.getenv("APPS_SCRIPT_EMAIL_WEBHOOK_URL", "").strip()
    if apps_script_url:
        try:
            payload = {
                "to": ", ".join(recipients),
                "subject": subject,
                "htmlBody": html_content
            }
            res = requests.post(apps_script_url, json=payload, timeout=30)
            if res.status_code == 200:
                print(f"[Dispatcher ✓] Email successfully delivered via Google Apps Script to: {', '.join(recipients)}")
                return {
                    "status": "success",
                    "provider": "Google Apps Script (Native Workspace)",
                    "recipients": recipients
                }
            else:
                print(f"[Dispatcher !] Google Apps Script returned {res.status_code}: {res.text[:200]}")
        except Exception as e:
            print(f"[Dispatcher !] Google Apps Script dispatch error: {e}")

    # 2. Fallback to standard SMTP if configured
    if not user or not pwd:
        return {"status": "error", "message": "Neither APPS_SCRIPT_EMAIL_WEBHOOK_URL nor valid SMTP credentials configured in .env"}

    try:
        msg = MIMEMultipart()
        msg["From"] = f"{from_name} <{user}>"
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        # Attach HTML body
        msg.attach(MIMEText(html_content, "html"))

        # Attach CSV
        if csv_content:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(csv_content.encode("utf-8"))
            encoders.encode_base64(part)
            filename = f"apollo_saving_ledger_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            part.add_header("Content-Disposition", f"attachment; filename=\"{filename}\"")
            msg.attach(part)

        server = smtplib.SMTP(host, port, timeout=20)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(user, pwd)
        server.sendmail(user, recipients, msg.as_string())
        server.quit()

        print(f"[Dispatcher ✓] Email successfully delivered to: {', '.join(recipients)}")
        return {"status": "success", "recipients": recipients}
    except Exception as e:
        print(f"[Dispatcher !] Email delivery failed: {e}")
        return {"status": "error", "error": str(e), "recipients": recipients}


def dispatch_whatsapp(message: str) -> Dict[str, Any]:
    """Sends message to WhatsApp via CallMeBot."""
    phone = os.getenv("WHATSAPP_PHONE_NUMBER", "").strip()
    apikey = os.getenv("WHATSAPP_CALLMEBOT_APIKEY", "").strip()

    if not phone or not apikey:
        return {"status": "skipped", "message": "WHATSAPP_PHONE_NUMBER or WHATSAPP_CALLMEBOT_APIKEY not configured"}

    url = "https://api.callmebot.com/whatsapp.php"
    params = {"phone": phone, "text": message, "apikey": apikey}

    try:
        res = requests.get(url, params=params, timeout=20)
        if res.status_code == 200:
            print(f"[Dispatcher ✓] WhatsApp alert delivered to {phone}!")
            return {"status": "success", "phone": phone}
        else:
            return {"status": "error", "http_code": res.status_code, "response": res.text[:200]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def dispatch_teams(card_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Sends Adaptive Connector Card to Microsoft Teams Webhook."""
    webhook_url = os.getenv("TEAMS_WEBHOOK_URL", "").strip()

    if not webhook_url:
        return {"status": "skipped", "message": "TEAMS_WEBHOOK_URL not configured"}

    try:
        res = requests.post(webhook_url, json=card_payload, timeout=20)
        if res.status_code in (200, 201, 202):
            print("[Dispatcher ✓] Microsoft Teams Card delivered!")
            return {"status": "success"}
        else:
            return {"status": "error", "http_code": res.status_code, "response": res.text[:200]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# =====================================================================
# MAIN ORCHESTRATION PIPELINE
# =====================================================================

def run_saving_report_pipeline(channels: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Executes the full report generation and multi-channel dispatch.
    channels: list containing any of ['email', 'whatsapp', 'teams']. Defaults to all.
    """
    if channels is None:
        channels = ["email", "whatsapp", "teams"]

    now_ist = datetime.now(timezone.utc).astimezone(IST)
    timestamp_str = now_ist.strftime("%d %b %Y, %I:%M %p IST")

    # 1. Fetch live report data
    report_data = build_report_data()
    total_leads = report_data.get("total_saved_leads", 0)
    total_creds = report_data.get("total_credits_used", 0)

    results = {
        "timestamp": timestamp_str,
        "total_contacts": total_leads,
        "credits_used": total_creds,
        "channels": {}
    }

    # 2. Email
    if "email" in channels:
        subject = f"[Apollo Daily Report] {now_ist.strftime('%d %b %Y')} • {total_leads:,d} Contacts | {total_creds:,d} Credits Used"
        html = format_email_html(report_data, timestamp_str)
        csv_data = build_csv_attachment(report_data)
        results["channels"]["email"] = dispatch_email(subject, html, csv_data)

    # 3. WhatsApp
    if "whatsapp" in channels:
        wa_msg = format_whatsapp_message(report_data, timestamp_str)
        results["channels"]["whatsapp"] = dispatch_whatsapp(wa_msg)

    # 4. Microsoft Teams
    if "teams" in channels:
        card = format_teams_card(report_data, timestamp_str)
        results["channels"]["teams"] = dispatch_teams(card)

    return results


if __name__ == "__main__":
    print("=" * 70)
    print(">>> Apollo Saving Ledger Multi-Channel Report Dispatcher")
    print("=" * 70)
    res = run_saving_report_pipeline()
    print("\nDispatch Results:")
    print(json.dumps(res, indent=2))
