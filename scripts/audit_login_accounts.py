#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dual CRM Account Audit for Login: vraghavan@nestack.com
=======================================================
Audits unique company accounts created / updated across:
  1. Freshsales CRM (cloud CRM via batch processor audit reports)
  2. AWS RDS MySQL Master CRM (emails table + apollo_saved_leads)
"""
from __future__ import annotations

import os
import sys
import csv
from collections import Counter
from pathlib import Path

# Fix Windows console encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "freshsales_agent"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / "freshsales_agent" / ".env")
load_dotenv(PROJECT_ROOT / ".env", override=False)

import pymysql

SOURCE_FILE = PROJECT_ROOT / "scratch" / "millionverifier_cache" / "vraghavan_nestack_com_aug_20_-_sep_20_categorized" / "good" / "vraghavan_nestack_com_aug_20_-_sep_20 - good - 7875 good - 9229 total.csv"
AUDIT1_FILE = PROJECT_ROOT / "exports" / "freshsales_reports" / "vraghavan_nestack_com_aug_20_-_sep_20 - good - 7875 good - 9229 total_crm_audit.csv"
AUDIT2_FILE = PROJECT_ROOT / "exports" / "freshsales_reports" / "vraghavan_nestack_com_aug_20_-_sep_20_-_good_-_7875_good_-_9229_total_unsynced_retry_4472_crm_audit.csv"


def main():
    print("=" * 75)
    print("      DUAL-CRM AUDIT: UNIQUE ACCOUNTS FOR vraghavan@nestack.com")
    print("=" * 75)

    if not SOURCE_FILE.exists():
        print(f"[ERROR] Source file not found: {SOURCE_FILE}")
        return

    # -------------------------------------------------------------
    # 1. Parse Source File
    # -------------------------------------------------------------
    source_map = {}
    with open(SOURCE_FILE, "r", encoding="utf-8", errors="replace") as fp:
        for row in csv.DictReader(fp):
            email = (row.get("Email") or "").strip().lower()
            if not email:
                continue
            comp = (row.get("Company Name") or row.get("Company Name for Emails") or row.get("Account") or "").strip()
            web = (row.get("Website") or "").strip().lower()
            dom = email.split("@")[1] if "@" in email else ""
            source_map[email] = {
                "company": comp,
                "website": web,
                "domain": dom,
                "first_name": row.get("First Name", "").strip(),
                "last_name": row.get("Last Name", "").strip(),
                "title": row.get("Title", "").strip(),
            }

    print(f"\n[Source MillionVerifier Good File]")
    print(f"  • File: {SOURCE_FILE.name}")
    print(f"  • Total Verified Good Leads: {len(source_map):,d}")

    # -------------------------------------------------------------
    # 2. Merge Freshsales Execution Results (Pass 1 + Pass 2 Retry)
    # -------------------------------------------------------------
    final_actions = {}

    if AUDIT1_FILE.exists():
        with open(AUDIT1_FILE, "r", encoding="utf-8", errors="replace") as fp:
            for r in csv.DictReader(fp):
                e = r.get("email", "").strip().lower()
                final_actions[e] = r.get("action", "")

    if AUDIT2_FILE.exists():
        with open(AUDIT2_FILE, "r", encoding="utf-8", errors="replace") as fp:
            for r in csv.DictReader(fp):
                e = r.get("email", "").strip().lower()
                final_actions[e] = r.get("action", "")

    action_counts = Counter(final_actions.values())

    created_companies = {}
    created_domains = {}
    updated_companies = {}
    updated_domains = {}
    tld_blocked_domains = set()

    for email, meta in source_map.items():
        act = final_actions.get(email, "unprocessed")
        c_norm = meta["company"].lower() if meta["company"] else ""
        d_norm = meta["domain"].lower() if meta["domain"] else ""

        if act == "created":
            if c_norm:
                created_companies[c_norm] = meta["company"]
            if d_norm:
                created_domains[d_norm] = meta["domain"]
        elif act == "updated_in_crm":
            if c_norm:
                updated_companies[c_norm] = meta["company"]
            if d_norm:
                updated_domains[d_norm] = meta["domain"]
        elif act == "excluded_tld":
            if d_norm:
                tld_blocked_domains.add(d_norm)

    # -------------------------------------------------------------
    # 3. Report Freshsales CRM Numbers
    # -------------------------------------------------------------
    print("\n" + "=" * 75)
    print("  CRM 1: FRESHSALES CLOUD CRM (nestack.freshsales.io)")
    print("  Tag Applied: 'vraghavan@nestack.com(aug 20 - sep 20)'")
    print("=" * 75)
    print(f"  Total Contacts Processed     : {len(source_map):,d}")
    print(f"  ├── Newly Created Contacts   : {action_counts.get('created', 0):,d}")
    print(f"  │     └── Unique Companies   : {len(created_companies):,d} unique company accounts")
    print(f"  │     └── Unique Domains     : {len(created_domains):,d} unique domains")
    print(f"  ├── Updated Existing Contacts: {action_counts.get('updated_in_crm', 0):,d} (tag merged safely)")
    print(f"  │     └── Unique Companies   : {len(updated_companies):,d} unique company accounts")
    print(f"  │     └── Unique Domains     : {len(updated_domains):,d} unique domains")
    print(f"  └── Foreign TLDs Blocked     : {action_counts.get('excluded_tld', 0):,d} contacts ({len(tld_blocked_domains)} domains)")

    all_fs_unique_companies = set(created_companies.keys()) | set(updated_companies.keys())
    all_fs_unique_domains = set(created_domains.keys()) | set(updated_domains.keys())
    print(f"\n  ★ TOTAL UNIQUE FRESHSALES ACCOUNTS FOR THIS LOGIN:")
    print(f"    • Total Unique Company Accounts: {len(all_fs_unique_companies):,d}")
    print(f"    • Total Unique Corporate Domains: {len(all_fs_unique_domains):,d}")

    # Top company examples
    print(f"\n  Sample Company Accounts Created in Freshsales (First 15):")
    for i, comp_name in enumerate(list(created_companies.values())[:15], 1):
        print(f"    [{i:>2}] {comp_name}")

    # -------------------------------------------------------------
    # 4. Report MySQL RDS Master CRM Numbers
    # -------------------------------------------------------------
    print("\n" + "=" * 75)
    print("  CRM 2: AWS RDS MASTER CRM DATABASE (MySQL apollo_scrapers)")
    print("=" * 75)

    db_host = os.getenv("DB_HOST", "").strip()
    if not db_host:
        print("  [SKIP] DB_HOST not configured.")
        return

    try:
        conn = pymysql.connect(
            host=db_host,
            user=os.getenv("DB_USER", "nestack"),
            password=os.getenv("DB_PASSWORD", ""),
            db=os.getenv("DB_NAME", "apollo_scrapers"),
            port=int(os.getenv("DB_PORT", "3306")),
            charset="utf8mb4",
            connect_timeout=10,
        )
        with conn.cursor() as cur:
            # Check batches in apollo_saved_leads for account_used or batch
            cur.execute("""
                SELECT batch, account_used, COUNT(id), COUNT(DISTINCT company_domain), COUNT(DISTINCT company)
                FROM apollo_saved_leads
                WHERE account_used LIKE '%vraghavan%' OR batch LIKE '%vraghavan%'
                GROUP BY batch, account_used
            """)
            batch_rows = cur.fetchall()
            print("  [apollo_saved_leads Staging Ledger for vraghavan]:")
            if batch_rows:
                for b in batch_rows:
                    print(f"    • Batch: '{b[0]}' | Account: '{b[1]}'")
                    print(f"        Leads: {b[2]:,d} | Unique Domains: {b[3]:,d} | Unique Companies: {b[4]:,d}")
            else:
                print("    • No direct batches found matching '%vraghavan%' in apollo_saved_leads.")

            # Check overlap against master CRM table: emails
            file_domains = list({s["domain"] for s in source_map.values() if s["domain"]})
            chunk_size = 1000
            in_master_crm = 0
            for i in range(0, len(file_domains), chunk_size):
                chunk = file_domains[i : i + chunk_size]
                format_strings = ",".join(["%s"] * len(chunk))
                cur.execute(
                    f"SELECT COUNT(DISTINCT domain) FROM emails WHERE domain IN ({format_strings})",
                    tuple(chunk),
                )
                in_master_crm += cur.fetchone()[0]

            cur.execute("SELECT COUNT(id) FROM emails")
            total_master_emails = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT domain) FROM emails")
            total_master_domains = cur.fetchone()[0]

            print(f"\n  [Master CRM `emails` Table]:")
            print(f"    • Total Master CRM Records : {total_master_emails:,d}")
            print(f"    • Total Master CRM Domains : {total_master_domains:,d}")
            print(f"    • Domains from this Login already in Master CRM: {in_master_crm:,d} ({in_master_crm/len(file_domains)*100:.1f}%)")
            print(f"    • Net-New Domains for Master CRM             : {len(file_domains) - in_master_crm:,d} ({(len(file_domains) - in_master_crm)/len(file_domains)*100:.1f}%)")

            # Check freshsales_companies table mirror
            cur.execute("SELECT COUNT(id) FROM freshsales_companies")
            fs_mirror_total = cur.fetchone()[0]
            print(f"\n  [Freshsales Companies RDS Mirror (`freshsales_companies`)]: ")
            print(f"    • Total Companies Cached in RDS: {fs_mirror_total:,d}")


        conn.close()
    except Exception as exc:
        print(f"  [ERROR] Database audit failed: {exc}")

    print("\n" + "=" * 75)
    print("  AUDIT COMPLETE")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    main()
