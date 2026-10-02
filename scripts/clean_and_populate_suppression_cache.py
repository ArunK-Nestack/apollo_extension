#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Safe Suppression Cache Manager for `million_verifier_cache`
===========================================================
- Safely removes deliverable ('good') emails so the table functions strictly
  as a negative suppression blocklist.
- Preserves / loads only 'bad' and 'risky' contacts for outreach accounts.
"""

import sys
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.api import get_connection

def main():
    conn = get_connection()
    cur = conn.cursor()

    print("=" * 80)
    print("     SAFE SUPPRESSION CACHE UPDATE (ONLY BAD & RISKY)")
    print("=" * 80)

    # 1. Audit before
    cur.execute("SELECT verification_status, COUNT(*) FROM million_verifier_cache GROUP BY verification_status")
    before = dict(cur.fetchall())
    print("\n[*] Initial Status Breakdown in `million_verifier_cache`:")
    for st, cnt in before.items():
        print(f"    • {st.upper():<6}: {cnt:,d}")

    # 2. Safely remove 'good' emails
    good_count = before.get('good', 0)
    if good_count > 0:
        print(f"\n[*] Safely removing {good_count:,d} 'good' deliverable emails...")
        cur.execute("DELETE FROM million_verifier_cache WHERE verification_status = 'good'")
        conn.commit()
        print("    ✓ All 'good' records successfully purged.")
    else:
        print("\n[*] No 'good' records found in table.")

    # 3. Ingest fresh bad & risky from downloaded API reports and local verified batches
    print("\n[*] Ingesting available bad & risky contacts from this month's batches...")
    
    records = {} # email -> (login, domain, status)
    
    # A. API full reports
    api_dir = ROOT / "scratch" / "mv_full_reports"
    if api_dir.exists():
        for p in api_dir.glob("*.csv"):
            login = p.stem.split("_", 1)[1] if "_" in p.stem else ""
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                    r = csv.DictReader(fp)
                    em_col = next((c for c in r.fieldnames if "email" in c.lower()), None)
                    q_col = next((c for c in r.fieldnames if c.lower() in ("quality", "result")), None)
                    if em_col and q_col:
                        for row in r:
                            em = str(row.get(em_col, "")).strip().lower()
                            q = str(row.get(q_col, "")).strip().lower()
                            if em and "@" in em and q in ("bad", "invalid", "risky", "catch_all", "catch-all"):
                                st = "bad" if q in ("bad", "invalid") else "risky"
                                dom = em.split("@")[-1]
                                records[em] = (login, dom, st)
            except Exception:
                pass

    # B. Scratch categorized batches
    scratch_dir = ROOT / "scratch" / "millionverifier_cache"
    if scratch_dir.exists():
        for p in scratch_dir.glob("**/*.csv"):
            fn = p.name.lower()
            if "- bad.csv" in fn: st = "bad"
            elif "- risky.csv" in fn: st = "risky"
            else: continue
            login = fn.split("_")[0]
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                    r = csv.DictReader(fp)
                    em_col = next((c for c in r.fieldnames if "email" in c.lower()), None)
                    if em_col:
                        for row in r:
                            em = str(row.get(em_col, "")).strip().lower()
                            if em and "@" in em:
                                dom = em.split("@")[-1]
                                records[em] = (login, dom, st)
            except Exception:
                pass

    # C. Step 1 results
    step1_dir = ROOT / "millionverifier_agent_step1" / "results"
    if step1_dir.exists():
        for p in step1_dir.glob("verified_*.csv"):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                    r = csv.DictReader(fp)
                    em_col = next((c for c in r.fieldnames if "email" in c.lower()), None)
                    q_col = next((c for c in r.fieldnames if c.lower() in ("quality", "result")), None)
                    if em_col and q_col:
                        for row in r:
                            em = str(row.get(em_col, "")).strip().lower()
                            q = str(row.get(q_col, "")).strip().lower()
                            if em and "@" in em and q in ("bad", "invalid", "risky", "catch_all", "catch-all"):
                                st = "bad" if q in ("bad", "invalid") else "risky"
                                dom = em.split("@")[-1]
                                if em not in records:
                                    records[em] = (p.name, dom, st)
            except Exception:
                pass

    print(f"    ✓ Found {len(records):,d} active bad & risky records from this month.")

    # Insert / update in chunks
    insert_sql = """
        INSERT INTO million_verifier_cache (email, login_owner, domain, verification_status, verified_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            login_owner = COALESCE(VALUES(login_owner), login_owner),
            domain = COALESCE(VALUES(domain), domain),
            verification_status = VALUES(verification_status),
            verified_at = NOW()
    """
    items = list(records.items())
    chunk_size = 2000
    for i in range(0, len(items), chunk_size):
        chunk = items[i:i + chunk_size]
        batch_data = [(em, l, d, s) for em, (l, d, s) in chunk]
        cur.executemany(insert_sql, batch_data)
        conn.commit()

    # 4. Final Audit
    cur.execute("SELECT verification_status, COUNT(*) FROM million_verifier_cache GROUP BY verification_status")
    after = dict(cur.fetchall())
    print("\n[*] Final Status Breakdown in `million_verifier_cache`:")
    total = sum(after.values())
    for st, cnt in after.items():
        print(f"    • {st.upper():<6}: {cnt:,d}")
    print(f"    -------------------------")
    print(f"    TOTAL:  {total:,d} (Zero 'good' leads - Strictly Bad & Risky Suppression List)")

if __name__ == "__main__":
    main()
