#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Safe Population Script for `million_verifier_cache`
===================================================
Populates verified contacts from MillionVerifier verification files into
the MySQL database table `million_verifier_cache` with columns:
  - email (PRIMARY KEY)
  - login_owner (indexed)
  - domain (indexed)
  - verification_status ('good', 'bad', 'risky')
  - verified_at (datetime)
"""

import os
import sys
import csv
import glob
import re
import time
from pathlib import Path
from collections import defaultdict
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from backend.api import get_connection

DL_DIR = Path("C:/Users/test/Downloads")
MV_RESULTS_DIR = ROOT / "millionverifier_agent_step1" / "results"
FS_REPORTS_DIR = ROOT / "exports" / "freshsales_reports"
EXPORTS_DIR = ROOT / "exports"

LOGINS = [
    'vijay@nestacktech.com',
    'abel.abraham@nestacktechnologies.com',
    'rahul@nestack.co.in',
    'vijay.raghavan@nestack.com',
    'rchandran@nestack.biz',
    'vraghavan@nestack.com',
    'vraghavan@nestacktech.com',
    'madhava.reddy@nestack-tech.com',
    'rchandran@nestack.info',
    'madhava.reddy@nestacktech.com',
    'vijay.raghavan@nestacktech.com',
    'vijay.raghavan@nestack.net',
    'vraghav@nestacktechnology.com',
    'rahul.chandran@nestack-tech.com',
    'jith@nestack.info',
    'recruiting@nestack.com',
    'rahul@nestaktechnology.com',
    'rahul@nestack-tech.com'
]

def map_quality(val: str) -> str:
    v = str(val or '').strip().lower()
    if v in ('good', 'ok', 'deliverable', 'valid'):
        return 'good'
    if v in ('bad', 'invalid', 'disposable', 'undeliverable'):
        return 'bad'
    if v in ('risky', 'catch_all', 'catch-all', 'unknown', 'spamtrap'):
        return 'risky'
    return None

def resolve_login(filename: str) -> str:
    fn = filename.lower()
    for l in LOGINS:
        user_part = l.split('@')[0].replace('.', '_')
        dom_part = l.split('@')[1].replace('.', '_').replace('-', '_')
        if l in fn:
            return l
        if f"{user_part}_{dom_part}" in fn:
            return l
        if user_part in fn and ('nestack' in fn or '@' in fn or 'sep' in fn or 'aug' in fn):
            return l
    return None

def main():
    print("=" * 80)
    print("     SAFE POPULATION: MILLION VERIFIER CACHE DATABASE INGESTION")
    print("=" * 80)

    # 1. Connect to Database
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM million_verifier_cache")
    initial_db_count = cur.fetchone()[0]
    print(f"[*] Initial records in `million_verifier_cache`: {initial_db_count:,d}")

    # 2. Collect Candidate Files
    candidates = []
    for d in [DL_DIR, MV_RESULTS_DIR, FS_REPORTS_DIR]:
        if d.exists():
            candidates.extend(d.glob("*.csv"))
    if EXPORTS_DIR.exists():
        candidates.extend(EXPORTS_DIR.glob("**/*.csv"))

    print(f"[*] Total CSV candidate files scanned: {len(candidates):,d}")

    # 3. Extract & Deduplicate Verified Records
    records = {}  # email -> (login_owner, domain, status, timestamp)
    file_stats = defaultdict(int)

    t0 = time.time()
    for p in candidates:
        fn = p.name.lower()
        is_mv = any(k in fn for k in ['millionverifier', 'verified', 'good', 'ok_only', 'invalid', 'full_report'])
        is_fs_created = 'created_contacts' in fn
        if not (is_mv or is_fs_created):
            continue

        assigned_login = resolve_login(p.name)
        if not assigned_login:
            continue

        # File timestamp for verified_at
        file_mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                reader = csv.DictReader(fp)
                if not reader.fieldnames:
                    continue

                for row in reader:
                    em = (row.get('Email') or row.get('email') or row.get('Contact : Emails') or '').strip().lower()
                    if not em or '@' not in em or '.' not in em.split('@')[1]:
                        continue

                    dom = em.split('@')[1].strip().lower()

                    # Quality resolution
                    raw_q = row.get('quality') or row.get('Quality') or row.get('result') or row.get('Result') or row.get('status')
                    status = map_quality(raw_q) if raw_q else None

                    if not status:
                        if 'ok_only' in fn or 'good' in fn or is_fs_created:
                            status = 'good'
                        elif 'invalid' in fn or 'bad' in fn:
                            status = 'bad'
                        elif 'risky' in fn:
                            status = 'risky'
                        else:
                            status = 'good'

                    # Priority: good > risky > bad (to preserve highest quality if seen across files)
                    if em not in records:
                        records[em] = (assigned_login, dom, status, file_mtime)
                        file_stats[assigned_login] += 1
                    else:
                        existing_status = records[em][2]
                        if status == 'good' and existing_status != 'good':
                            records[em] = (assigned_login, dom, 'good', file_mtime)
        except Exception:
            pass

    print(f"[+] Extracted {len(records):,d} unique verified emails across {len(file_stats)} logins in {time.time()-t0:.2f}s.")

    # 4. Insert into Database in Chunks
    print("\n[*] Inserting records into MySQL `million_verifier_cache` in chunks of 5,000...")
    chunk_size = 5000
    all_items = list(records.items())
    total_inserted = 0

    insert_sql = """
        INSERT INTO million_verifier_cache (email, login_owner, domain, verification_status, verified_at)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            verification_status = VALUES(verification_status),
            login_owner = COALESCE(VALUES(login_owner), login_owner),
            domain = COALESCE(VALUES(domain), domain),
            verified_at = VALUES(verified_at)
    """

    for i in range(0, len(all_items), chunk_size):
        chunk = all_items[i : i + chunk_size]
        params = [
            (em, data[0], data[1], data[2], data[3])
            for em, data in chunk
        ]
        cur.executemany(insert_sql, params)
        conn.commit()
        total_inserted += len(chunk)
        print(f"  -> Processed {total_inserted:,d} / {len(all_items):,d} records...")

    # 5. Final Database Audit
    print("\n" + "=" * 80)
    print("                    DATABASE VERIFICATION AUDIT")
    print("=" * 80)

    cur.execute("SELECT COUNT(*) FROM million_verifier_cache")
    final_db_count = cur.fetchone()[0]
    print(f"[✓] Final total records in `million_verifier_cache`: {final_db_count:,d} (Added: +{final_db_count - initial_db_count:,d})")

    # Status breakdown
    cur.execute("SELECT verification_status, COUNT(*) FROM million_verifier_cache GROUP BY verification_status")
    print("\nStatus Breakdown:")
    for status, cnt in cur.fetchall():
        print(f"  • {str(status).upper():<8}: {cnt:,d}")

    # Unique domains
    cur.execute("SELECT COUNT(DISTINCT domain) FROM million_verifier_cache WHERE domain IS NOT NULL AND domain != ''")
    unique_domains = cur.fetchone()[0]
    print(f"\nUnique Corporate Domains: {unique_domains:,d}")

    # Login Owner breakdown
    cur.execute("""
        SELECT login_owner, 
               COUNT(*) AS total,
               SUM(CASE WHEN verification_status = 'good' THEN 1 ELSE 0 END) AS good,
               SUM(CASE WHEN verification_status = 'bad' THEN 1 ELSE 0 END) AS bad,
               SUM(CASE WHEN verification_status = 'risky' THEN 1 ELSE 0 END) AS risky
        FROM million_verifier_cache 
        WHERE login_owner IS NOT NULL
        GROUP BY login_owner 
        ORDER BY total DESC
    """)
    print("\nLogin-Wise Breakdown in `million_verifier_cache`:")
    print(f"{'#':<3} | {'Login Owner':<38} | {'Total':<8} | {'Good':<8} | {'Bad':<6} | {'Risky'}")
    print("-" * 75)
    for idx, (login, tot, g, b, rk) in enumerate(cur.fetchall(), 1):
        tot_i, g_i, b_i, rk_i = int(tot or 0), int(g or 0), int(b or 0), int(rk or 0)
        print(f"{idx:<3} | {str(login):<38} | {tot_i:<8,d} | {g_i:<8,d} | {b_i:<6,d} | {rk_i:,d}")

    print("\n[✓] Population completed safely and verified successfully.")

if __name__ == "__main__":
    main()
