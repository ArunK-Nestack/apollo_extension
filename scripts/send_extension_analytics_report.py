#!/usr/bin/env python3
"""Build and email the daily extension activity report without calling Apollo."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

IST = timezone(timedelta(hours=5, minutes=30))
ACTIVITY_COLUMNS = (
    "id", "event_type", "account_email", "cycle_tag", "batch", "search_word",
    "search_bar_word", "page_number", "total_on_page", "required_on_page",
    "not_required_on_page", "existing_on_page", "guardrail_rejected_on_page",
    "collected_total", "created_at",
)


def _as_utc(value: Optional[datetime]) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def report_window(
    report_date: Optional[date] = None,
    now_utc: Optional[datetime] = None,
) -> Tuple[date, datetime, datetime]:
    """Return the IST report date and a UTC [start, end) window.

    Today's report stops at generation time. A historical report covers the full
    IST calendar day.
    """
    current_utc = _as_utc(now_utc)
    current_ist = current_utc.astimezone(IST)
    target_date = report_date or current_ist.date()
    start_ist = datetime.combine(target_date, time.min, tzinfo=IST)
    next_day_ist = start_ist + timedelta(days=1)
    end_ist = min(current_ist, next_day_ist) if target_date == current_ist.date() else next_day_ist
    return target_date, start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def _load_account_names() -> Dict[str, str]:
    path = PROJECT_ROOT / "config" / "apollo_accounts.json"
    try:
        accounts = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return {
        str(account.get("email") or "").strip().lower(): str(account.get("name") or "").strip()
        for account in accounts
        if account.get("email")
    }


def _ist_timestamp(value: Any) -> str:
    """Format database UTC TIMESTAMP values as unambiguous IST text."""
    if not value:
        return ""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def aggregate_daily_report(
    rows: Iterable[Dict[str, Any]],
    saved_counts: Optional[Dict[str, int]] = None,
    account_names: Optional[Dict[str, str]] = None,
    report_date: Optional[date] = None,
    generated_at_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Create a report model from already-filtered daily activity rows."""
    from backend.dashboard_routes import _aggregate_extension_analytics

    saved_counts = {str(k).strip().lower(): int(v or 0) for k, v in (saved_counts or {}).items()}
    account_names = {str(k).strip().lower(): str(v or "") for k, v in (account_names or {}).items()}
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    display_emails: Dict[str, str] = {}
    for row in rows:
        email = str(row.get("account_email") or "").strip()
        if not email:
            continue
        key = email.lower()
        display_emails.setdefault(key, email)
        grouped[key].append(row)

    logins: List[Dict[str, Any]] = []
    for email_key, login_rows in grouped.items():
        analytics = _aggregate_extension_analytics(login_rows, saved_leads=saved_counts.get(email_key, 0))
        summary = analytics["summary"]
        ordered_times = [row.get("created_at") for row in login_rows if row.get("created_at")]
        cycles = sorted({str(row.get("cycle_tag") or "").strip() for row in login_rows if row.get("cycle_tag")})
        batches = sorted({str(row.get("batch") or "").strip() for row in login_rows if row.get("batch")})
        events = Counter(str(row.get("event_type") or "UNKNOWN").strip().upper() for row in login_rows)
        logins.append({
            "email": display_emails[email_key],
            "name": account_names.get(email_key) or display_emails[email_key].split("@")[0].replace(".", " ").title(),
            "events": len(login_rows),
            "event_counts": dict(sorted(events.items())),
            "first_activity": _ist_timestamp(min(ordered_times)) if ordered_times else "",
            "last_activity": _ist_timestamp(max(ordered_times)) if ordered_times else "",
            "cycles": cycles,
            "batches": batches,
            "summary": summary,
            "searches": [
                {
                    **search,
                    "first_seen": _ist_timestamp(search.get("first_seen")),
                    "last_seen": _ist_timestamp(search.get("last_seen")),
                }
                for search in analytics["searches"][:5]
            ],
            "data_quality": analytics["data_quality"],
        })

    logins.sort(key=lambda item: (item["summary"]["evaluated"], item["events"]), reverse=True)
    totals = {
        "active_logins": len(logins),
        "events": sum(item["events"] for item in logins),
        "pages": sum(item["summary"]["pages"] for item in logins),
        "evaluated": sum(item["summary"]["evaluated"] for item in logins),
        "required": sum(item["summary"]["required"] for item in logins),
        "existing": sum(item["summary"]["existing"] for item in logins),
        "rejected": sum(item["summary"]["rejected"] for item in logins),
        "saved": sum(item["summary"]["saved"] for item in logins),
    }
    totals["required_rate"] = round(totals["required"] * 100 / max(1, totals["evaluated"]), 1)
    generated = _as_utc(generated_at_utc).astimezone(IST)
    return {
        "status": "ok",
        "report_date": (report_date or generated.date()).isoformat(),
        "generated_at": generated.isoformat(),
        "timezone": "Asia/Kolkata",
        "totals": totals,
        "logins": logins,
    }


def build_report_data(
    report_date: Optional[date] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Read today's extension ledger and saved leads; never calls Apollo."""
    from backend.api import get_connection

    target_date, start_utc, end_utc = report_window(report_date, now_utc)
    rows: List[Dict[str, Any]] = []
    saved_counts: Dict[str, int] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            # TIMESTAMP conversion/comparison is now deterministic even when the
            # MySQL host and Windows scheduler use different local timezones.
            cur.execute("SET time_zone = '+00:00'")
            cur.execute(
                """
                SELECT id, event_type, account_email, cycle_tag, batch, search_word,
                       search_bar_word, page_number, total_on_page, required_on_page,
                       not_required_on_page, existing_on_page, guardrail_rejected_on_page,
                       collected_total, created_at
                FROM extension_activity_log
                WHERE created_at >= %s AND created_at < %s
                  AND TRIM(account_email) <> ''
                ORDER BY created_at ASC, id ASC
                """,
                (start_utc.replace(tzinfo=None), end_utc.replace(tzinfo=None)),
            )
            rows = [dict(zip(ACTIVITY_COLUMNS, row)) for row in cur.fetchall()]
            try:
                cur.execute(
                    """
                    SELECT LOWER(TRIM(account_used)), COUNT(*)
                    FROM apollo_saved_leads
                    WHERE created_at >= %s AND created_at < %s
                      AND TRIM(account_used) <> ''
                    GROUP BY LOWER(TRIM(account_used))
                    """,
                    (start_utc.replace(tzinfo=None), end_utc.replace(tzinfo=None)),
                )
                saved_counts = {str(email): int(count or 0) for email, count in cur.fetchall()}
            except Exception:
                # Older installations may not yet have account_used. Activity
                # analytics still remain complete; only the daily saved KPI is 0.
                saved_counts = {}

    report = aggregate_daily_report(
        rows,
        saved_counts=saved_counts,
        account_names=_load_account_names(),
        report_date=target_date,
        generated_at_utc=now_utc,
    )
    report["window_start_utc"] = start_utc.isoformat()
    report["window_end_utc"] = end_utc.isoformat()
    return report


def format_email_report(report: Dict[str, Any]) -> str:
    totals = report["totals"]
    report_day = datetime.fromisoformat(report["report_date"]).strftime("%d %b %Y")
    generated = datetime.fromisoformat(report["generated_at"]).strftime("%d %b %Y, %I:%M %p IST")
    login_rows = []
    login_sections = []
    for index, login in enumerate(report["logins"], 1):
        summary = login["summary"]
        login_rows.append(
            f"<tr><td>{index}</td><td><strong>{escape(login['name'])}</strong><br>"
            f"<span style='color:#64748b'>{escape(login['email'])}</span></td>"
            f"<td style='text-align:right'>{login['events']:,}</td>"
            f"<td style='text-align:right'>{summary['pages']:,}</td>"
            f"<td style='text-align:right'>{summary['evaluated']:,}</td>"
            f"<td style='text-align:right;color:#059669;font-weight:700'>{summary['required']:,}</td>"
            f"<td style='text-align:right'>{summary['existing']:,}</td>"
            f"<td style='text-align:right'>{summary['rejected']:,}</td>"
            f"<td style='text-align:right'>{summary['saved']:,}</td>"
            f"<td style='text-align:right'>{summary['required_rate']:.1f}%</td></tr>"
        )
        search_rows = "".join(
            f"<tr><td>{escape(search['search_word'])}</td><td>{escape(search['search_bar_word'])}</td>"
            f"<td style='text-align:right'>{search['pages']:,}</td><td style='text-align:right'>{search['evaluated']:,}</td>"
            f"<td style='text-align:right;color:#059669'>{search['required']:,}</td>"
            f"<td style='text-align:right'>{search['required_rate']:.1f}%</td></tr>"
            for search in login["searches"]
        ) or "<tr><td colspan='6' style='color:#64748b'>No PAGE_EVALUATED search data recorded.</td></tr>"
        event_text = " · ".join(f"{escape(name)}: {count:,}" for name, count in login["event_counts"].items())
        login_sections.append(
            f"<div style='margin-top:18px;padding:18px;border:1px solid #dbe4ee;border-radius:12px'>"
            f"<h3 style='margin:0 0 4px;color:#0f172a'>{escape(login['name'])}</h3>"
            f"<div style='font-size:12px;color:#64748b'>{escape(login['email'])} · "
            f"{escape(login['first_activity'])} to {escape(login['last_activity'])}</div>"
            f"<div style='margin:10px 0;font-size:12px;color:#475569'>{event_text}</div>"
            f"<table style='width:100%;border-collapse:collapse;font-size:12px'><thead><tr>"
            f"<th>Search context</th><th>Keyword</th><th>Pages</th><th>Checked</th><th>Required</th><th>Yield</th>"
            f"</tr></thead><tbody>{search_rows}</tbody></table></div>"
        )

    if not login_rows:
        login_rows.append("<tr><td colspan='10' style='padding:22px;text-align:center;color:#64748b'>No extension login activity was recorded today.</td></tr>")

    card = "display:inline-block;min-width:125px;margin:0 8px 8px 0;padding:12px 14px;border:1px solid #dbe4ee;border-radius:10px;background:#f8fafc"
    th = "padding:9px;text-align:left;border-bottom:1px solid #cbd5e1;color:#475569"
    td_css = "table td{padding:9px;border-bottom:1px solid #e2e8f0}table th{padding:9px;text-align:left;background:#f1f5f9;color:#475569}"
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>{td_css}</style></head>
<body style='margin:0;background:#eef2f7;font-family:Arial,sans-serif;color:#1e293b'>
<div style='max-width:1100px;margin:0 auto;padding:24px'>
  <div style='background:#fff;border:1px solid #dbe4ee;border-radius:16px;overflow:hidden'>
    <div style='padding:24px;background:linear-gradient(135deg,#0f172a,#164e63);color:#fff'>
      <div style='font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#67e8f9'>Daily Extension Intelligence</div>
      <h1 style='margin:7px 0 5px;font-size:25px'>Extension Analytics · {report_day}</h1>
      <div style='font-size:13px;color:#cbd5e1'>Only logins with activity on this IST day · generated {generated}</div>
    </div>
    <div style='padding:18px 24px'>
      <div style='{card}'><strong style='font-size:22px'>{totals['active_logins']:,}</strong><br><span style='font-size:11px;color:#64748b'>Active logins</span></div>
      <div style='{card}'><strong style='font-size:22px'>{totals['events']:,}</strong><br><span style='font-size:11px;color:#64748b'>Activity events</span></div>
      <div style='{card}'><strong style='font-size:22px'>{totals['evaluated']:,}</strong><br><span style='font-size:11px;color:#64748b'>Contacts checked</span></div>
      <div style='{card}'><strong style='font-size:22px;color:#059669'>{totals['required']:,}</strong><br><span style='font-size:11px;color:#64748b'>Required ({totals['required_rate']:.1f}%)</span></div>
      <div style='{card}'><strong style='font-size:22px'>{totals['saved']:,}</strong><br><span style='font-size:11px;color:#64748b'>Saved today</span></div>
      <h2 style='margin:16px 0 8px;font-size:17px'>Login summary</h2>
      <div style='overflow-x:auto'><table style='width:100%;border-collapse:collapse;font-size:12px'>
        <thead><tr><th style='{th}'>#</th><th style='{th}'>Login</th><th style='{th}'>Events</th><th style='{th}'>Pages</th><th style='{th}'>Checked</th><th style='{th}'>Required</th><th style='{th}'>Existing</th><th style='{th}'>Rejected</th><th style='{th}'>Saved</th><th style='{th}'>Yield</th></tr></thead>
        <tbody>{''.join(login_rows)}</tbody>
      </table></div>
      {''.join(login_sections)}
      <p style='margin:18px 0 0;font-size:11px;color:#64748b'>Source: local extension activity and saved-lead ledgers. No Apollo API lookup or credit-consuming enrichment was used.</p>
    </div>
  </div>
</div></body></html>"""


def build_report_csv(report: Dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Report Date (IST)", "Login Name", "Login Email", "First Activity", "Last Activity",
        "Events", "Pages", "Contacts Checked", "Required", "Required Rate %", "Existing",
        "Rejected", "Saved Today", "Cycles", "Batches", "Event Breakdown",
    ])
    for login in report["logins"]:
        summary = login["summary"]
        writer.writerow([
            report["report_date"], login["name"], login["email"], login["first_activity"],
            login["last_activity"], login["events"], summary["pages"], summary["evaluated"],
            summary["required"], summary["required_rate"], summary["existing"], summary["rejected"],
            summary["saved"], " | ".join(login["cycles"]), " | ".join(login["batches"]),
            " | ".join(f"{name}:{count}" for name, count in login["event_counts"].items()),
        ])
    return output.getvalue()


def run_extension_analytics_report_pipeline(
    channels: Optional[List[str]] = None,
    report_date: Optional[date] = None,
    now_utc: Optional[datetime] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    selected = [channel.strip().lower() for channel in (channels or ["email"]) if channel.strip()]
    report = build_report_data(report_date=report_date, now_utc=now_utc)
    totals = report["totals"]
    results = {
        "status": "ok",
        "report_date": report["report_date"],
        "total_active_logins": totals["active_logins"],
        "total_events": totals["events"],
        "total_contacts_checked": totals["evaluated"],
        "total_required": totals["required"],
        "channels": {},
    }
    if dry_run:
        results["status"] = "dry_run"
        results["preview"] = report
        return results

    if "email" in selected:
        from scripts.send_saving_report import dispatch_email
        report_day = datetime.fromisoformat(report["report_date"]).strftime("%d %b %Y")
        subject = (
            f"[Extension Analytics] {report_day} • {totals['active_logins']} Active Logins • "
            f"{totals['evaluated']:,} Checked • {totals['required']:,} Required"
        )
        results["channels"]["email"] = dispatch_email(
            subject,
            format_email_report(report),
            build_report_csv(report),
            attachment_filename=f"extension_analytics_{report['report_date'].replace('-', '')}.csv",
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily extension analytics email report")
    parser.add_argument("--channels", default="email", help="Comma-separated channels (email)")
    parser.add_argument("--date", help="IST report date in YYYY-MM-DD format")
    parser.add_argument("--dry-run", action="store_true", help="Build report without sending")
    args = parser.parse_args()
    target_date = date.fromisoformat(args.date) if args.date else None
    channels = [value.strip() for value in args.channels.split(",") if value.strip()]
    print(json.dumps(run_extension_analytics_report_pipeline(channels, target_date, dry_run=args.dry_run), indent=2, default=str))


if __name__ == "__main__":
    main()
