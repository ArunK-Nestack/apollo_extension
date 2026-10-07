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
    """Fetches the official saving summary, verification summary, and CRM sync ledger data."""
    try:
        from backend.dashboard_routes import (
            get_saving_summary,
            get_verification_summary,
            get_crm_sync_summary
        )
        saving = get_saving_summary(filter_cycle="october", active_only=True)
        verif = get_verification_summary(filter_cycle="october")
        crm = get_crm_sync_summary(filter_cycle="all")
        crm_oct = get_crm_sync_summary(filter_cycle="october")

        return {
            "status": "ok",
            "saving": saving,
            "verification": verif,
            "crm": crm,
            "crm_october": crm_oct
        }
    except Exception as e:
        print(f"[Dispatcher] Error loading report data: {e}", flush=True)
        return {
            "status": "error",
            "message": str(e),
            "saving": {},
            "verification": {},
            "crm": {},
            "crm_october": {}
        }


def format_whatsapp_message(report_data: Dict[str, Any], timestamp_str: str) -> str:
    """Formats a concise, emoji-rich WhatsApp summary covering all 3 stages."""
    saving = report_data.get("saving", {})
    verif = report_data.get("verification", {})
    crm = report_data.get("crm_october") or report_data.get("crm", {})

    total_leads = saving.get("total_saved_leads", 0)
    total_web = saving.get("total_saved_web", 0)
    total_enriched = saving.get("total_enriched", 0)
    total_emails = saving.get("total_emails_saved", 0)
    total_creds = saving.get("total_credits_used", 0)
    yield_pct = f"{(total_emails / max(1, total_leads)) * 100:.1f}%" if total_leads > 0 else "0.0%"

    v_checked = verif.get("total_checked", 0)
    v_good = verif.get("total_good", 0)
    v_bad = verif.get("total_bad", 0)
    v_risky = verif.get("total_risky", 0)
    v_rate = verif.get("overall_deliverability", 0.0)
    v_supp = verif.get("total_suppressed", 0)

    crm_batches = crm.get("total_batches_synced", 0)
    crm_created = crm.get("total_contacts_created", 0)
    crm_updated = crm.get("total_contacts_updated", 0)
    crm_accounts = crm.get("total_accounts_created", 0)

    lines = [
        "📊 *APOLLO, VERIFIER & CRM DAILY DIGEST*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 *Generated:* {timestamp_str} (IST)",
        "⚙️ *Cycle:* Active October 2026 Cycle (sep 03 - oct 03)",
        "",
        "🟣 *STAGE 1: APOLLO SAVING & ENRICHMENT*",
        f"• 🌐 Total Contacts:      *{total_leads:,d}*",
        f"• 📥 Saved from Web:       *{total_web:,d}*",
        f"• ⚡ Enriched with Credits: *{total_enriched:,d}*",
        f"• 💳 Apollo Credits Used:   *{total_creds:,d}*",
        f"• ✉️ Saved Emails:         *{total_emails:,d}* ({yield_pct} yield)",
        "",
        "🛡️ *STAGE 2: MILLIONVERIFIER TELEMETRY*",
        f"• 🔍 Total Checked:        *{v_checked:,d}*",
        f"• ✅ Good (Inbox-Ready):   *{v_good:,d}*",
        f"• 📈 Deliverability Rate:  *{v_rate}%*",
        f"• 🚫 Suppressed Leads:     *{v_supp:,d}* ({v_bad:,d} bad, {v_risky:,d} risky)",
        f"• 🛡️ Active Target:        *Rahul Chandran* (● Secured)",
        "",
        "💼 *STAGE 3: FRESHSALES CRM INGESTION*",
        "• 🏷️ Target Tag:           *RAHUL.CHANDRAN@NESTACK-TECH.COM(sep 03 - oct 03)*",
        f"• 📥 Ingested Breakdown:   *{crm_created:,d}* created | *{crm_updated:,d}* updated",
        f"• 🏢 Accounts Created (verified): *{crm_accounts:,d}* ({crm.get('account_reconciled_runs', 0)}/{crm_batches} runs reconciled)",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "⚡ _Automated report via Apollo Intelligence Engine_"
    ]
    return "\n".join(lines)


def format_teams_card(report_data: Dict[str, Any], timestamp_str: str) -> Dict[str, Any]:
    """Builds an Office 365 / Microsoft Teams Connector Card payload covering all 3 operations."""
    saving = report_data.get("saving", {})
    verif = report_data.get("verification", {})
    crm = report_data.get("crm_october") or report_data.get("crm", {})

    total_leads = saving.get("total_saved_leads", 0)
    total_web = saving.get("total_saved_web", 0)
    total_enriched = saving.get("total_enriched", 0)
    total_emails = saving.get("total_emails_saved", 0)
    total_creds = saving.get("total_credits_used", 0)

    v_checked = verif.get("total_checked", 0)
    v_good = verif.get("total_good", 0)
    v_rate = verif.get("overall_deliverability", 0.0)
    v_supp = verif.get("total_suppressed", 0)

    crm_batches = crm.get("total_batches_synced", 0)
    crm_created = crm.get("total_contacts_created", 0)
    crm_updated = crm.get("total_contacts_updated", 0)
    crm_accounts = crm.get("total_accounts_created", 0)

    facts = [
        {"name": "Report Timestamp", "value": timestamp_str},
        {"name": "Active Cycle", "value": "October 2026 (sep 03 - oct 03)"},
        {"name": "Apollo Contacts Saved", "value": f"{total_leads:,d} ({total_web:,d} web / {total_enriched:,d} enriched)"},
        {"name": "Apollo Credits Spent", "value": f"{total_creds:,d}"},
        {"name": "Verified Emails Saved", "value": f"{total_emails:,d}"},
        {"name": "MillionVerifier Checked", "value": f"{v_checked:,d}"},
        {"name": "Inbox-Ready Good Leads", "value": f"{v_good:,d} ({v_rate}% Deliverable)"},
        {"name": "Suppressed (Bad/Risky)", "value": f"{v_supp:,d} Leads"},
        {"name": "Freshsales Contacts", "value": f"{crm_created:,d} created | {crm_updated:,d} updated"},
        {"name": "Verified Freshsales Accounts Created", "value": f"{crm_accounts:,d} ({crm.get('account_reconciled_runs', 0)}/{crm_batches} runs reconciled)"},
    ]

    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0EA5E9",
        "summary": f"Apollo, Verifier & CRM Report - {total_leads:,d} Contacts",
        "title": "🟣 Apollo Saving, MillionVerifier & Freshsales Executive Digest",
        "sections": [
            {
                "activityTitle": "Multi-Stage Operations Summary",
                "facts": facts,
                "markdown": True
            }
        ],
        "potentialAction": [
            {
                "@type": "OpenUri",
                "name": "Open Executive Dashboard",
                "targets": [{"os": "default", "uri": "http://localhost:8000"}]
            }
        ]
    }
    return card


def format_email_html(report_data: Dict[str, Any], timestamp_str: str) -> str:
    """Renders a responsive, executive HTML email digest covering all 3 stages."""
    saving = report_data.get("saving", {})
    verif = report_data.get("verification", {})
    crm = report_data.get("crm_october") or report_data.get("crm", {})

    # Section 1 Data
    total_leads = saving.get("total_saved_leads", 0)
    total_web = saving.get("total_saved_web", 0)
    total_enriched = saving.get("total_enriched", 0)
    total_emails = saving.get("total_emails_saved", 0)
    total_creds = saving.get("total_credits_used", 0)
    yield_pct = f"{(total_emails / max(1, total_leads)) * 100:.1f}%" if total_leads > 0 else "0.0%"

    active_saving_records = [r for r in saving.get("records", []) if r.get("total_leads", 0) > 0]
    saving_rows_html = ""
    for idx, r in enumerate(active_saving_records, 1):
        num = f"#{idx:02d}"
        saving_rows_html += f"""
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
    if not saving_rows_html:
        saving_rows_html = '<tr><td colspan="7" style="padding: 16px; text-align: center; color: #94a3b8;">No active saving records.</td></tr>'

    # Section 2 Data (MillionVerifier)
    v_checked = verif.get("total_checked", 0)
    v_good = verif.get("total_good", 0)
    v_bad = verif.get("total_bad", 0)
    v_risky = verif.get("total_risky", 0)
    v_rate = verif.get("overall_deliverability", 0.0)
    v_supp = verif.get("total_suppressed", 0)

    verif_records = [r for r in verif.get("records", []) if r.get("total_checked", 0) > 0]
    verif_rows_html = ""
    for r in verif_records:
        num = f"#{r.get('id', 0):02d}"
        rate = r.get("deliverability_rate", 0.0)
        verif_rows_html += f"""
        <tr style="border-bottom: 1px solid #2d3748;">
            <td style="padding: 10px 12px; font-family: monospace; color: #94a3b8; font-weight: bold;">{num}</td>
            <td style="padding: 10px 12px;">
                <div style="font-weight: 600; color: #f8fafc;">{r.get('name')}</div>
                <div style="font-size: 11px; font-family: monospace; color: #94a3b8;">{r.get('email')}</div>
            </td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; font-weight: 600;">{r.get('total_checked', 0):,d}</td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #4ade80; font-weight: 700;">{r.get('good', 0):,d}</td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #f87171; font-weight: 700;">{r.get('bad', 0):,d}</td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #fbbf24; font-weight: 700;">{r.get('risky', 0):,d}</td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #38bdf8; font-weight: bold;">{rate}%</td>
            <td style="padding: 10px 12px; text-align: right; font-family: monospace; color: #f87171; font-weight: 700;">{r.get('suppressed_leads', 0):,d}</td>
            <td style="padding: 10px 12px; text-align: center;">
                <span style="background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); padding: 3px 8px; border-radius: 9999px; font-size: 10px; font-weight: 700;">● Secured</span>
            </td>
        </tr>
        """
    if not verif_rows_html:
        verif_rows_html = '<tr><td colspan="9" style="padding: 16px; text-align: center; color: #94a3b8;">No verification records in this cycle.</td></tr>'

    # Section 3 Data (Freshsales CRM)
    crm_batches = crm.get("total_batches_synced", 0)
    crm_created = crm.get("total_contacts_created", 0)
    crm_updated = crm.get("total_contacts_updated", 0)
    crm_accounts = crm.get("total_accounts_created", 0)
    crm_blocked = crm.get("total_cleaned_out", 0)

    crm_records = crm.get("records", [])
    crm_rows_html = ""
    for idx, r in enumerate(crm_records, 1):
        num = f"#{idx:02d}"
        crm_rows_html += f"""
        <tr style="border-bottom: 1px solid #2d3748;">
            <td style="padding: 9px 11px; font-family: monospace; color: #94a3b8; font-weight: bold;">{num}</td>
            <td style="padding: 9px 11px;">
                <div style="font-weight: 600; color: #f8fafc; font-size: 12px;">{r.get('login_owner')}</div>
                <div style="font-size: 10px; color: #94a3b8;">{r.get('tag')}</div>
            </td>
            <td style="padding: 9px 11px; text-align: right; font-family: monospace;">{r.get('initial_leads', 0):,d}</td>
            <td style="padding: 9px 11px; text-align: right; font-family: monospace; color: #fbbf24;">-{r.get('cleaned_out', 0):,d}</td>
            <td style="padding: 9px 11px; text-align: right; font-family: monospace; color: #4ade80; font-weight: 700;">+{r.get('contacts_created', 0):,d}</td>
            <td style="padding: 9px 11px; text-align: right; font-family: monospace; color: #38bdf8; font-weight: 700;">↺ {r.get('contacts_updated', 0):,d}</td>
            <td style="padding: 9px 11px; text-align: right; font-family: monospace; color: #a78bfa; font-weight: 700;">{'Unreconciled' if r.get('accounts_created') is None else format(r['accounts_created'], ',d')}</td>
            <td style="padding: 9px 11px; font-family: monospace; font-size: 10px; color: #94a3b8;">{r.get('sync_timestamp', '')}</td>
        </tr>
        """
    if not crm_rows_html:
        crm_rows_html = '<tr><td colspan="8" style="padding: 16px; text-align: center; color: #94a3b8;">No CRM batches recorded.</td></tr>'

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
  .container {{ max-width: 860px; margin: 0 auto; background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; }}
  .header {{ background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%); padding: 24px 32px; }}
  .title {{ font-size: 20px; font-weight: 800; color: #ffffff; margin: 0; }}
  .subtitle {{ font-size: 13px; color: rgba(255,255,255,0.9); margin-top: 4px; }}
  .sec-head {{ background: #0f172a; padding: 14px 20px; font-size: 13px; font-weight: 700; color: #f8fafc; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; }}
  .kpi-row {{ display: table; width: 100%; border-bottom: 1px solid #334155; background: #182234; }}
  .kpi-cell {{ display: table-cell; padding: 14px 12px; text-align: center; border-right: 1px solid #334155; }}
  .kpi-cell:last-child {{ border-right: none; }}
  .kpi-val {{ font-size: 19px; font-weight: 800; font-family: monospace; }}
  .kpi-lbl {{ font-size: 10px; text-transform: uppercase; color: #94a3b8; font-weight: 600; margin-top: 3px; letter-spacing: 0.5px; }}
  .table-wrapper {{ padding: 18px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ background: #0f172a; color: #94a3b8; text-transform: uppercase; font-size: 10px; padding: 9px 11px; letter-spacing: 0.5px; text-align: left; }}
  .banner {{ background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 8px; padding: 12px 16px; margin: 0 18px 14px 18px; font-size: 11.5px; color: #e2e8f0; }}
  .footer {{ padding: 16px 24px; background: #0f172a; border-top: 1px solid #334155; font-size: 11px; color: #64748b; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="title">📊 Apollo, MillionVerifier & Freshsales Executive Digest</div>
    <div class="subtitle">Generated on {timestamp_str} (IST) • Active October 2026 Billing Cycle (sep 03 - oct 03)</div>
  </div>

  <!-- SECTION 1: APOLLO SAVING -->
  <div class="sec-head">
    <span>🟣 Section 1: Apollo Saving Ledger &amp; Credit Usage</span>
    <span style="font-size:11px; color:#38bdf8; font-weight:normal;">Active October Cycle</span>
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
    <table>
      <thead>
        <tr>
          <th style="width: 35px;">#</th>
          <th>Account &amp; Expiry</th>
          <th style="text-align: right;">Web</th>
          <th style="text-align: right;">Enriched</th>
          <th style="text-align: right;">Credits</th>
          <th style="text-align: right;">Emails</th>
          <th style="text-align: right;">Total Leads</th>
        </tr>
      </thead>
      <tbody>
        {saving_rows_html}
      </tbody>
    </table>
  </div>

  <!-- SECTION 2: MILLIONVERIFIER -->
  <div class="sec-head" style="border-top:1px solid #334155;">
    <span>🛡️ Section 2: MillionVerifier Email Deliverability &amp; Negative Suppression</span>
    <span style="font-size:11px; color:#4ade80; font-weight:normal;">{v_good:,d} Inbox-Ready Leads</span>
  </div>
  <div class="kpi-row">
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#38bdf8;">{v_checked:,d}</div>
      <div class="kpi-lbl">Total Checked</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#4ade80;">{v_good:,d}</div>
      <div class="kpi-lbl">Good (Inbox)</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#f87171;">{v_bad:,d}</div>
      <div class="kpi-lbl">Bad (Bounces)</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#fbbf24;">{v_risky:,d}</div>
      <div class="kpi-lbl">Risky (Catch-all)</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#4ade80;">{v_rate}%</div>
      <div class="kpi-lbl">Deliverability</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#f87171;">{v_supp:,d}</div>
      <div class="kpi-lbl">Suppressed Leads</div>
    </div>
  </div>
  <div class="table-wrapper">
    <table>
      <thead>
        <tr>
          <th style="width: 35px;">#</th>
          <th>Outreach Account</th>
          <th style="text-align: right;">Checked</th>
          <th style="text-align: right;">Good</th>
          <th style="text-align: right;">Bad</th>
          <th style="text-align: right;">Risky</th>
          <th style="text-align: right;">Health</th>
          <th style="text-align: right;">Suppressed</th>
          <th style="text-align: center;">Status</th>
        </tr>
      </thead>
      <tbody>
        {verif_rows_html}
      </tbody>
    </table>
  </div>

  <!-- SECTION 3: FRESHSALES CRM -->
  <div class="sec-head" style="border-top:1px solid #334155;">
    <span>💼 Section 3: Freshsales CRM Bridge &amp; Master Ingestion Ledger</span>
    <span style="font-size:11px; color:#a78bfa; font-weight:normal;">{crm.get('account_reconciled_runs', 0)}/{crm_batches} Runs Reconciled</span>
  </div>
  <div class="kpi-row">
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#38bdf8;">{crm_batches}</div>
      <div class="kpi-lbl">Batches Synced</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#4ade80;">+{crm_created:,d}</div>
      <div class="kpi-lbl">Contacts Created</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#38bdf8;">↺ {crm_updated:,d}</div>
      <div class="kpi-lbl">Contacts Updated</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#a78bfa;">+{crm_accounts:,d}</div>
      <div class="kpi-lbl">Verified Accounts Created</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-val" style="color:#fbbf24;">-{crm_blocked:,d}</div>
      <div class="kpi-lbl">GDPR / TLD Blocked</div>
    </div>
  </div>
  <div style="padding-top:14px;">
    <div class="banner">
      <strong>Active Ingestion Target (October Cycle):</strong><br>
      • <strong>Account:</strong> Rahul Chandran (RAHUL.CHANDRAN@NESTACK-TECH.COM)<br>
      • <strong>Freshsales Tag:</strong> <code style="color:#38bdf8;">RAHUL.CHANDRAN@NESTACK-TECH.COM(sep 03 - oct 03)</code><br>
      • Account creation counts are shown only for runs reconciled to Freshsales account IDs.
    </div>
  </div>
  <div class="table-wrapper" style="padding-top:0;">
    <table>
      <thead>
        <tr>
          <th style="width: 35px;">#</th>
          <th>Login Owner &amp; Run Tag</th>
          <th style="text-align: right;">Initial</th>
          <th style="text-align: right;">Cleaned</th>
          <th style="text-align: right;">Created</th>
          <th style="text-align: right;">Updated</th>
          <th style="text-align: right;">Accounts Created</th>
          <th>Sync Timestamp</th>
        </tr>
      </thead>
      <tbody>
        {crm_rows_html}
      </tbody>
    </table>
  </div>

  <div class="footer">
    Sent automatically by Apollo Intelligence Engine • Access live operations station at <a href="http://localhost:8000" style="color: #38bdf8; text-decoration: none;">http://localhost:8000</a>
  </div>
</div>
</body>
</html>
"""
    return html


def build_csv_attachment(report_data: Dict[str, Any]) -> str:
    """Generates comprehensive multi-section CSV string."""
    saving = report_data.get("saving", {})
    verif = report_data.get("verification", {})
    crm = report_data.get("crm_october") or report_data.get("crm", {})

    output = io.StringIO()
    writer = csv.writer(output)

    # 1. Apollo Saving Records
    writer.writerow(["=== SECTION 1: APOLLO SAVING LEDGER ==="])
    writer.writerow([
        "Account ID", "Name", "Email", "Billing Cycle", "Expiry IST", "Time Left",
        "Saved from Web", "Enriched Here", "Credits Used", "Saved Emails", "Total Contacts", "Last Saved"
    ])
    for r in saving.get("records", []):
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

    writer.writerow([])
    # 2. MillionVerifier Records
    writer.writerow(["=== SECTION 2: MILLIONVERIFIER DELIVERABILITY LEDGER ==="])
    writer.writerow([
        "Account ID", "Account Name", "Email", "Total Checked", "Good Inbox",
        "Bad Bounces", "Risky Catchall", "Deliverability Rate", "Suppressed Leads", "Shield Status"
    ])
    for r in verif.get("records", []):
        if r.get("total_checked", 0) > 0:
            writer.writerow([
                r.get("id"),
                r.get("name"),
                r.get("email"),
                r.get("total_checked"),
                r.get("good"),
                r.get("bad"),
                r.get("risky"),
                f"{r.get('deliverability_rate')}%",
                r.get("suppressed_leads"),
                r.get("status")
            ])

    writer.writerow([])
    # 3. Freshsales CRM Records
    writer.writerow(["=== SECTION 3: FRESHSALES CRM SYNC LEDGER ==="])
    writer.writerow([
        "Batch Key", "Login Owner", "Tag", "Import Label", "Initial Leads",
        "Cleaned Out TLD", "Contacts Created", "Contacts Updated", "Account Reconciliation Status", "Exact Accounts Created", "Sync Timestamp"
    ])
    for r in crm.get("records", []):
        writer.writerow([
            r.get("batch_key"),
            r.get("login_owner"),
            r.get("tag"),
            r.get("import_label"),
            r.get("initial_leads"),
            r.get("cleaned_out"),
            r.get("contacts_created"),
            r.get("contacts_updated"),
            r.get("account_reconciliation_status"),
            r.get("accounts_created"),
            r.get("sync_timestamp")
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
                resp_json = {}
                try:
                    resp_json = res.json()
                except Exception:
                    pass
                if resp_json.get("status") == "error":
                    print(f"[Dispatcher !] Google Apps Script error: {resp_json.get('message')}")
                else:
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
    saving = report_data.get("saving", {})
    verif = report_data.get("verification", {})
    crm = report_data.get("crm", {})

    total_leads = saving.get("total_saved_leads", 0)
    total_creds = saving.get("total_credits_used", 0)
    total_emails = saving.get("total_emails_saved", 0)
    v_checked = verif.get("total_checked", 0)
    v_good = verif.get("total_good", 0)
    v_rate = verif.get("overall_deliverability", 0.0)
    v_supp = verif.get("total_suppressed", 0)
    crm_created = crm.get("total_contacts_created", 0)
    crm_updated = crm.get("total_contacts_updated", 0)
    crm_accounts = crm.get("total_accounts_created", 0)

    results = {
        "timestamp": timestamp_str,
        "saving": {
            "total_contacts": total_leads,
            "credits_used": total_creds,
            "emails_saved": total_emails
        },
        "verification": {
            "total_checked": v_checked,
            "good_inbox": v_good,
            "deliverability_rate": v_rate,
            "suppressed_leads": v_supp
        },
        "crm": {
            "contacts_created": crm_created,
            "contacts_updated": crm_updated,
            "accounts_created": crm_accounts,
            "batches_synced": crm.get("total_batches_synced", 0)
        },
        "channels": {}
    }

    # 2. Email
    if "email" in channels:
        subject = f"[Apollo Executive Report] {now_ist.strftime('%d %b %Y')} • {total_leads:,d} Leads | {v_good:,d} Verified ({v_rate}%) | {crm_created:,d} CRM Synced"
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
    import argparse
    parser = argparse.ArgumentParser(description="Multi-Channel Report Dispatcher")
    parser.add_argument("--channels", type=str, default="email,whatsapp,teams", help="Comma-separated channels to dispatch")
    args = parser.parse_args()

    selected_channels = [c.strip().lower() for c in args.channels.split(",") if c.strip()]
    print("=" * 70)
    print(f">>> Apollo Saving, Verifier & CRM Report Dispatcher: {selected_channels}")
    print("=" * 70)
    res = run_saving_report_pipeline(channels=selected_channels)
    print("\nDispatch Results:")
    print(json.dumps(res, indent=2))
