#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fast September 2026 Comprehensive Login & Pipeline Audit
Executes single aggregate queries to run in < 5 seconds and generates:
1. Executive September Summary
2. Detailed Breakdown Table by Login Name
3. Accounts & Domains Tracking
4. Quality & Waste Analysis
"""

import os
import sys
import json
import csv
import re
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "freshsales_agent"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "freshsales_agent" / ".env", override=False)

from backend.api import get_connection
from scripts.login_pipeline_audit import get_account_for_batch

def main():
    print("[1/5] Loading configurations and cache files...")
    
    # 1. Load Live CRM Daily Breakdown
    daily_file = ROOT / "scratch" / "freshsales_daily_breakdown.json"
    crm_daily = {}
    if daily_file.exists():
        with open(daily_file, "r", encoding="utf-8") as fp:
            crm_daily = json.load(fp)

    # 2. Load MillionVerifier Jobs
    mv_file = ROOT / "config" / "millionverifier_jobs.json"
    mv_by_login = defaultdict(lambda: {"sent": 0, "good": 0, "bad": 0, "risky": 0, "jobs": 0})
    if mv_file.exists():
        with open(mv_file, "r", encoding="utf-8") as fp:
            for j in json.load(fp):
                l = (j.get("login") or j.get("account_name") or "").strip().lower()
                # Resolve account
                resolved = get_account_for_batch(l, l)
                mv_by_login[resolved]["sent"]  += int(j.get("total_rows", 0) or 0)
                mv_by_login[resolved]["good"]  += int(j.get("good_count", 0) or 0)
                mv_by_login[resolved]["bad"]   += int(j.get("bad_count", 0) or 0)
                mv_by_login[resolved]["risky"] += int(j.get("risky_count", 0) or 0)
                mv_by_login[resolved]["jobs"]  += 1

    # 3. Load Freshsales Synced Batches Ledger & Extract Accounts Created
    fs_file = ROOT / "config" / "freshsales_synced_batches.json"
    fs_by_login = defaultdict(lambda: {
        "pushed": 0, "created": 0, "updated": 0, "tld_blocked": 0,
        "companies": set(), "domains": set()
    })
    if fs_file.exists():
        with open(fs_file, "r", encoding="utf-8") as fp:
            ledger = json.load(fp)
            for key, entry in ledger.items():
                tag = entry.get("tag", "")
                stem = str(entry.get("file_stem", "")).lower()
                tag_email = tag.split("(")[0].strip().lower()
                resolved = get_account_for_batch(stem, tag_email)

                # Parse audit actions
                src_path = Path(entry.get("file_path", ""))
                aud_path = Path(entry.get("audit_file", ""))
                if src_path.exists() and aud_path.exists():
                    actions = {}
                    with open(aud_path, "r", encoding="utf-8", errors="replace") as afp:
                        for r in csv.DictReader(afp):
                            actions[r.get("email", "").strip().lower()] = r.get("action", "")
                    with open(src_path, "r", encoding="utf-8", errors="replace") as sfp:
                        for r in csv.DictReader(sfp):
                            e = r.get("Email", "").strip().lower()
                            if actions.get(e) == "created":
                                c = (r.get("Company Name") or r.get("Company Name for Emails") or r.get("Account") or "").strip()
                                dom = e.split("@")[1] if "@" in e else ""
                                if c:
                                    fs_by_login[resolved]["companies"].add(c)
                                if dom:
                                    fs_by_login[resolved]["domains"].add(dom.lower())

                is_retry = "_unsynced_retry_" in key.lower() or "_retry_" in key.lower()
                if not is_retry:
                    fs_by_login[resolved]["pushed"] += int(entry.get("total_leads", 0) or 0)
                fs_by_login[resolved]["created"]     += int(entry.get("created", 0) or 0)
                fs_by_login[resolved]["updated"]     += int(entry.get("updated", 0) or 0)
                fs_by_login[resolved]["tld_blocked"] += int(entry.get("tld_blocked", 0) or 0)

    print("[2/5] Executing database single-pass aggregations...")
    api_by_login = defaultdict(lambda: {"attempts": 0, "found": 0, "no_email": 0, "no_match": 0, "credits": 0})
    db_by_login  = defaultdict(lambda: {"saved": 0, "enriched": 0, "api": 0, "web": 0, "accounts": set(), "domains": set()})
    enrich_by_login = defaultdict(lambda: {"saved": 0, "enriched": 0})

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Stage 1: API Enrichment ledger
            cur.execute("""
                SELECT LOWER(login_email), outcome, SUM(credits_charged), COUNT(*)
                FROM batch_enrichment_ledger
                GROUP BY LOWER(login_email), outcome
            """)
            for le, outcome, cred, cnt in cur.fetchall():
                le = le.strip()
                cred = int(cred or 0); cnt = int(cnt or 0)
                api_by_login[le]["attempts"] += cnt
                api_by_login[le]["credits"]  += cred
                if outcome == "email_found":
                    api_by_login[le]["found"] += cnt
                elif outcome == "no_email":
                    api_by_login[le]["no_email"] += cnt
                elif outcome == "no_match":
                    api_by_login[le]["no_match"] += cnt

            # Stage 2: Saved leads DB
            cur.execute("""
                SELECT batch, account_used,
                       COUNT(*),
                       SUM(CASE WHEN enriched_at IS NOT NULL THEN 1 ELSE 0 END),
                       SUM(COALESCE(credits_charged, 0)),
                       company, company_domain
                FROM apollo_saved_leads
                GROUP BY batch, account_used, company, company_domain
            """)
            for b, acct, cnt, enr, cred, comp, dom in cur.fetchall():
                b_str = str(b or ""); a_str = str(acct or "")
                resolved = get_account_for_batch(b_str, a_str)
                cnt = int(cnt or 0); enr = int(enr or 0); cred = int(cred or 0)
                db_by_login[resolved]["saved"] += cnt
                db_by_login[resolved]["enriched"] += enr
                if cred > 0:
                    db_by_login[resolved]["api"] += enr
                else:
                    db_by_login[resolved]["web"] += enr
                if comp and comp.strip():
                    db_by_login[resolved]["accounts"].add(comp.strip())
                if dom and dom.strip():
                    db_by_login[resolved]["domains"].add(dom.strip().lower())

            # Stage 3: Enrich.so
            cur.execute("""
                SELECT batch, account_used, COUNT(*), SUM(CASE WHEN enriched_at IS NOT NULL THEN 1 ELSE 0 END)
                FROM enrich_saved_leads
                GROUP BY batch, account_used
            """)
            for b, acct, cnt, enr in cur.fetchall():
                b_str = str(b or ""); a_str = str(acct or "")
                resolved = get_account_for_batch(b_str, a_str)
                enrich_by_login[resolved]["saved"] += int(cnt or 0)
                enrich_by_login[resolved]["enriched"] += int(enr or 0)

    print("[3/5] Consolidating all September 2026 logins...")
    
    # Collect all unique logins
    all_logins = set()
    all_logins.update(crm_daily.get("by_login", {}).keys())
    all_logins.update(api_by_login.keys())
    all_logins.update(db_by_login.keys())
    all_logins.update(fs_by_login.keys())
    all_logins.update(mv_by_login.keys())
    # Exclude system/test dummy logins
    all_logins = {l for l in all_logins if "@" in l and not l.startswith("unknown")}

    report_rows = []
    
    for email in sorted(all_logins):
        email_clean = email.lower().strip()
        user_name = email_clean.split("@")[0].replace(".", " ").title()

        # Live CRM September counts
        live_info = crm_daily.get("by_login", {}).get(email_clean, {})
        sept_live_created = 0
        sept_live_updated = 0
        active_dates = []
        for dt, c_dict in live_info.items():
            if dt.startswith("2026-09"):
                c = c_dict.get("created", 0)
                u = c_dict.get("updated", 0)
                sept_live_created += c
                sept_live_updated += u
                if c > 0 or u > 0:
                    active_dates.append(dt)

        # Synced batches counts
        fs_sync = fs_by_login[email_clean]
        sync_created = fs_sync["created"]
        sync_updated = fs_sync["updated"]
        sync_accounts = len(fs_sync["companies"])
        sync_domains  = len(fs_sync["domains"])

        # Best effective counts for created / updated
        eff_created = max(sept_live_created, sync_created)
        eff_updated = sept_live_updated if sept_live_updated > 0 else sync_updated

        # Accounts created
        # In CRM, each company created in sync_accounts is exact. If synced via web import, ratio is ~0.50-0.55 of leads created.
        if sync_accounts > 0:
            eff_accounts = sync_accounts
            eff_domains = sync_domains
            accts_display = f"{sync_accounts:,} ({sync_domains:,} dom)"
        elif eff_created > 0:
            est_acc = int(eff_created * 0.51)
            eff_accounts = est_acc
            eff_domains = est_acc
            accts_display = f"~{est_acc:,} (est)"
        else:
            eff_accounts = len(db_by_login[email_clean]["accounts"])
            eff_domains = len(db_by_login[email_clean]["domains"])
            accts_display = f"{eff_accounts:,}" if eff_accounts > 0 else "0"

        # Channel
        channels = []
        if api_by_login[email_clean]["attempts"] > 0:
            channels.append("API")
        if db_by_login[email_clean]["web"] > 0 or enrich_by_login[email_clean]["saved"] > 0:
            channels.append("Web/Ext")
        if not channels and eff_created > 0:
            channels.append("Web Import")
        channel_str = " & ".join(channels) if channels else "Web Import"

        report_rows.append({
            "login_email": email_clean,
            "owner": user_name,
            "channel": channel_str,
            "created": eff_created,
            "updated": eff_updated,
            "accounts_created": eff_accounts,
            "domains_created": eff_domains,
            "accounts_display": accts_display,
            "apollo_api_attempts": api_by_login[email_clean]["attempts"],
            "apollo_api_found": api_by_login[email_clean]["found"],
            "apollo_api_wasted": api_by_login[email_clean]["no_email"] + api_by_login[email_clean]["no_match"],
            "apollo_credits": api_by_login[email_clean]["credits"],
            "db_saved": db_by_login[email_clean]["saved"],
            "db_enriched": db_by_login[email_clean]["enriched"],
            "enrich_so": enrich_by_login[email_clean]["saved"],
            "mv_sent": mv_by_login[email_clean]["sent"],
            "mv_good": mv_by_login[email_clean]["good"],
            "active_days": len(active_dates),
            "first_active": min(active_dates) if active_dates else "-",
            "last_active": max(active_dates) if active_dates else "-"
        })

    # Sort descending by Created contacts, then Updated
    report_rows.sort(key=lambda r: (r["created"], r["updated"], r["db_saved"]), reverse=True)

    print(f"[4/5] Writing comprehensive September report (Total Logins: {len(report_rows)})...")
    
    out_json = ROOT / "scratch" / "september_audit_report.json"
    with open(out_json, "w", encoding="utf-8") as fp:
        json.dump(report_rows, fp, indent=2)

    # Print summary
    tot_created = sum(r["created"] for r in report_rows)
    tot_updated = sum(r["updated"] for r in report_rows)
    tot_accts   = sum(r["accounts_created"] for r in report_rows)
    tot_credits = sum(r["apollo_credits"] for r in report_rows)
    tot_saved   = sum(r["db_saved"] for r in report_rows)
    tot_mv      = sum(r["mv_sent"] for r in report_rows)

    print("\n" + "=" * 115)
    print("                    SEPTEMBER 2026 PIPELINE AUDIT REPORT (ALL LOGINS)")
    print("=" * 115)
    print(f"Total Logins Processed:   {len(report_rows)}")
    print(f"Total Leads Created:      {tot_created:,}")
    print(f"Total Leads Updated:      {tot_updated:,}")
    print(f"Total Accounts Created:   ~{tot_accts:,}")
    print(f"Total Apollo Credits:     {tot_credits:,}")
    print(f"Total DB Saved Leads:     {tot_saved:,}")
    print(f"Total Sent to MV:         {tot_mv:,}")
    print("=" * 115)
    print(f"{'#':2} | {'Login Name':36} | {'Channel':16} | {'Created':9} | {'Updated':9} | {'Accounts / Domains':20} | {'Days':4}")
    print("-" * 115)
    for idx, r in enumerate(report_rows, 1):
        print(f"{idx:2} | {r['login_email']:36} | {r['channel']:16} | {r['created']:9,d} | {r['updated']:9,d} | {r['accounts_display']:20} | {r['active_days']:4}")
    print("=" * 115)

if __name__ == "__main__":
    main()
