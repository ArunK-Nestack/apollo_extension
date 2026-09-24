#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Audit Domains that DO NOT EXIST in Freshsales CRM
=================================================
Cross-references the 6,232 unique domains from vraghavan@nestack.com against:
  1. Freshsales CRM (`freshsales_companies` mirror - 1,845,456 accounts)
  2. Master RDS CRM (`emails` table - 7,474,161 records)
"""
from __future__ import annotations

import os
import sys
import csv
from pathlib import Path

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


def main():
    domain_to_info = {}
    with open(SOURCE_FILE, "r", encoding="utf-8", errors="replace") as fp:
        for row in csv.DictReader(fp):
            email = (row.get("Email") or "").strip().lower()
            if "@" in email:
                dom = email.split("@")[1].strip().lower()
                comp = (row.get("Company Name") or row.get("Company Name for Emails") or row.get("Account") or "").strip()
                if dom and dom not in domain_to_info:
                    domain_to_info[dom] = {"company": comp, "count": 1}
                elif dom:
                    domain_to_info[dom]["count"] += 1

    all_domains = list(domain_to_info.keys())
    total_domains = len(all_domains)

    conn = pymysql.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", "3306")),
        charset="utf8mb4",
        connect_timeout=15,
    )

    fs_domains = set()
    master_crm_domains = set()
    chunk_size = 1000

    with conn.cursor() as cur:
        for i in range(0, len(all_domains), chunk_size):
            chunk = all_domains[i : i + chunk_size]
            placeholders = ",".join(["%s"] * len(chunk))

            # 1. Freshsales mirror
            cur.execute(
                f"SELECT DISTINCT domain FROM freshsales_companies WHERE domain IN ({placeholders})",
                tuple(chunk),
            )
            for r in cur.fetchall():
                if r[0]:
                    fs_domains.add(r[0].lower().strip())

            # 2. Master CRM emails
            cur.execute(
                f"SELECT DISTINCT domain FROM emails WHERE domain IN ({placeholders})",
                tuple(chunk),
            )
            for r in cur.fetchall():
                if r[0]:
                    master_crm_domains.add(r[0].lower().strip())

    conn.close()

    not_in_fs = [d for d in all_domains if d not in fs_domains]
    not_in_master = [d for d in all_domains if d not in master_crm_domains]
    not_in_either = [d for d in all_domains if d not in fs_domains and d not in master_crm_domains]

    leads_not_in_fs = sum(domain_to_info[d]["count"] for d in not_in_fs)
    leads_not_in_master = sum(domain_to_info[d]["count"] for d in not_in_master)
    leads_not_in_either = sum(domain_to_info[d]["count"] for d in not_in_either)

    print("=" * 75)
    print("      DOMAINS NOT IN CRM AUDIT (vraghavan@nestack.com)")
    print("=" * 75)
    print(f"Total Unique Corporate Domains in this File : {total_domains:,d}")
    print(f"Total Deliverable Leads in this File        : 7,875\n")

    print("─" * 75)
    print("  1. FRESHSALES CRM (`freshsales_companies` table — 1,845,456 Accounts)")
    print("─" * 75)
    print(f"  • Already Exist in Freshsales             : {len(fs_domains):>5,d} domains ({len(fs_domains)/total_domains*100:5.1f}%)")
    print(f"  • ★ DO NOT EXIST IN FRESHSALES (Net-New)  : {len(not_in_fs):>5,d} domains ({len(not_in_fs)/total_domains*100:5.1f}%)")
    print(f"    └── Total Leads with these New Domains  : {leads_not_in_fs:>5,d} leads   ({leads_not_in_fs/7875*100:5.1f}% of file)")

    print("\n" + "─" * 75)
    print("  2. MASTER RDS CRM DATABASE (`emails` table — 7,474,161 Contacts)")
    print("─" * 75)
    print(f"  • Already Exist in Master CRM             : {len(master_crm_domains):>5,d} domains ({len(master_crm_domains)/total_domains*100:5.1f}%)")
    print(f"  • ★ DO NOT EXIST IN MASTER CRM (Net-New)  : {len(not_in_master):>5,d} domains ({len(not_in_master)/total_domains*100:5.1f}%)")
    print(f"    └── Total Leads with these New Domains  : {leads_not_in_master:>5,d} leads   ({leads_not_in_master/7875*100:5.1f}% of file)")

    print("\n" + "─" * 75)
    print("  3. COMPLETELY BRAND NEW EVERYWHERE (Neither in Freshsales nor Master CRM)")
    print("─" * 75)
    print(f"  • ★ DO NOT EXIST IN EITHER CRM            : {len(not_in_either):>5,d} domains ({len(not_in_either)/total_domains*100:5.1f}%)")
    print(f"    └── Total Leads with Virgin Domains     : {leads_not_in_either:>5,d} leads   ({leads_not_in_either/7875*100:5.1f}% of file)")

    print("\n" + "=" * 75)
    print("  SAMPLE NET-NEW DOMAINS NOT IN FRESHSALES (First 20):")
    print("=" * 75)
    print(f"  {'#':<4} {'Domain':<30} {'Company Name':<32} {'Leads'}")
    print("  " + "─" * 71)
    for i, d in enumerate(not_in_fs[:20], 1):
        comp = (domain_to_info[d]["company"] or "N/A")[:30]
        cnt = domain_to_info[d]["count"]
        print(f"  [{i:>2}] {d:<30} {comp:<32} {cnt:>3}")

    print("=" * 75 + "\n")


if __name__ == "__main__":
    main()
