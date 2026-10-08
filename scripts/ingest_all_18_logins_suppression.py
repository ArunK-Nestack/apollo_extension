#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ingest Remaining Bad and Risky Emails for all 18 Logins
======================================================
Strictly extracts and loads Bad & Risky (suppression) contacts across
all 18 accounts from their exact verified reports and input-vs-OK diffs.
Zero 'good' deliverable emails are added.
"""

import sys
import csv
from pathlib import Path
from collections import defaultdict
import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.api import get_connection

DL = Path("C:/Users/test/Downloads")
MV_REPORTS = ROOT / "scratch" / "mv_full_reports"
STEP1_DIR = ROOT / "millionverifier_agent_step1" / "results"
SCRATCH_MV = ROOT / "scratch" / "millionverifier_cache"

def get_emails_csv(p):
    if not p.exists(): return set()
    with open(p, encoding='utf-8', errors='ignore') as fp:
        r = csv.DictReader(fp)
        col = next((c for c in (r.fieldnames or []) if c.strip().lower() == 'email'), None)
        if not col: return set()
        return set(str(row.get(col, '')).strip().lower() for row in r if row.get(col) and '@' in str(row.get(col)))

def get_emails_xlsx(p):
    if not p.exists(): return set()
    try:
        wb = openpyxl.load_workbook(p, read_only=True)
        ws = wb.active
        first_row = next(ws.iter_rows(values_only=True))
        col_idx = next((i for i, h in enumerate(first_row) if str(h).strip().lower() == 'email'), None)
        if col_idx is None: return set()
        emails = set()
        for row in ws.iter_rows(values_only=True):
            if col_idx < len(row):
                em = str(row[col_idx] or '').strip().lower()
                if '@' in em: emails.add(em)
        return emails
    except Exception:
        return set()

def main():
    conn = get_connection()
    cur = conn.cursor()

    print("=" * 80)
    print("   INGESTING EXACT BAD & RISKY EMAILS ACROSS ALL 18 LOGINS")
    print("=" * 80)

    # Dictionary: email -> (login_owner, domain, status)
    new_suppression = {}

    # 1. API Full Reports (8 Jobs)
    if MV_REPORTS.exists():
        for p in MV_REPORTS.glob("*.csv"):
            login = p.stem.split("_", 1)[1] if "_" in p.stem else ""
            with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                r = csv.DictReader(fp)
                em_col = next((c for c in r.fieldnames if c.strip().lower() == "email"), None)
                q_col = next((c for c in r.fieldnames if c.lower() in ("quality", "result")), None)
                if em_col and q_col:
                    for row in r:
                        em = str(row.get(em_col, "")).strip().lower()
                        q = str(row.get(q_col, "")).strip().lower()
                        if em and "@" in em and q in ("bad", "invalid", "risky", "catch_all", "catch-all"):
                            st = "bad" if q in ("bad", "invalid") else "risky"
                            dom = em.split("@")[-1]
                            new_suppression[em] = (login, dom, st)

    # 2. Step 1 Results (13 Verified Files)
    if STEP1_DIR.exists():
        for p in STEP1_DIR.glob("verified_*.csv"):
            login = p.stem.replace("verified_", "").split(" - ")[0].split("(")[0].strip()
            with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                r = csv.DictReader(fp)
                em_col = next((c for c in r.fieldnames if c.strip().lower() == "email"), None)
                q_col = next((c for c in r.fieldnames if c.lower() in ("quality", "result")), None)
                if em_col and q_col:
                    for row in r:
                        em = str(row.get(em_col, "")).strip().lower()
                        q = str(row.get(q_col, "")).strip().lower()
                        if em and "@" in em and q in ("bad", "invalid", "risky", "catch_all", "catch-all"):
                            st = "bad" if q in ("bad", "invalid") else "risky"
                            dom = em.split("@")[-1]
                            new_suppression[em] = (login, dom, st)

    # 3. Categorized Local Batches (VRaghavan & Madhava Reddy)
    if SCRATCH_MV.exists():
        for p in SCRATCH_MV.glob("**/*.csv"):
            fn = p.name.lower()
            if "- bad.csv" in fn: st = "bad"
            elif "- risky.csv" in fn: st = "risky"
            else: continue
            login = fn.split("_")[0]
            with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                r = csv.DictReader(fp)
                em_col = next((c for c in r.fieldnames if c.strip().lower() == "email"), None)
                if em_col:
                    for row in r:
                        em = str(row.get(em_col, "")).strip().lower()
                        if em and "@" in em:
                            dom = em.split("@")[-1]
                            new_suppression[em] = (login, dom, st)

    # 4. Web Batch Full Reports & Mathematical Diffs (for web logins)
    # A. Rahul nestaktech full report
    p_rahul_full = DL / "Rahul_rahul_nestacktechnology-sep_cleaned_sales_leads_20260919_010042_FULL_REPORT_MILLIONVERIFIER.COM.csv"
    if p_rahul_full.exists():
        with open(p_rahul_full, "r", encoding="utf-8", errors="ignore") as fp:
            r = csv.DictReader(fp)
            em_col = next((c for c in r.fieldnames if c.strip().lower() == "email"), None)
            q_col = next((c for c in r.fieldnames if c.lower() in ("quality", "result")), None)
            if em_col and q_col:
                for row in r:
                    em = str(row.get(em_col, "")).strip().lower()
                    q = str(row.get(q_col, "")).strip().lower()
                    if em and "@" in em and q in ("bad", "invalid", "risky", "catch_all", "catch-all"):
                        st = "bad" if q in ("bad", "invalid") else "risky"
                        dom = em.split("@")[-1]
                        new_suppression[em] = ("rahul@nestaktechnology.com", dom, st)

    # B. Input - OK_ONLY diffs
    diff_pairs = [
        (DL / "abel.abraham@nestacktechnologies.com(10 Aug - 10 Sep).csv",
         DL / "abel.abraham@nestacktechnologies.com(10 Aug - 10 Sep)_OK_ONLY_MILLIONVERIFIER.COM.csv",
         "abel.abraham@nestacktechnologies.com"),
        (DL / "vijay@nestacktech.com(8 Aug - 8 Sep).xlsx",
         DL / "vijay@nestacktech.com(8 Aug - 8 Sep)_OK_ONLY_MILLIONVERIFIER.COM.csv",
         "Vijay@nestacktech.com"),
        (DL / "vijay.raghavan@nestack.com(14 Aug to 14 Sep).xlsx",
         DL / "vijay.raghavan@nestack.com(14 Aug to 14 Sep)_OK_ONLY_MILLIONVERIFIER.COM.csv",
         "vijay.raghavan@nestack.com"),
        (DL / "rahul.chandran@nestack-tech.com( 3 Aug - 3 Sep) - Sheet1.csv",
         DL / "rahul.chandran@nestack-tech.com( 3 Aug - 3 Sep) - good - 3439 good - 3905 total.csv",
         "rahul.chandran@nestack-tech.com"),
        (DL / "Recruiting_recruiting-sep_cleaned_sales_leads_20260916_015131.csv",
         DL / "Recruiting_recruiting-sep_cleaned_sales_leads_20260916_015131_OK_ONLY_MILLIONVERIFIER.COM.csv",
         "recruiting@nestack.com"),
        (DL / "R_Chandran_rchandran-biz-sep_cleaned_sales_leads_20260919_193929.csv",
         DL / "R_Chandran_rchandran-biz-sep_20260919_200413 - good - 3488 good - 4217 total.csv",
         "RCHANDRAN@NESTACK.BIZ"),
    ]

    for p_in, p_ok, login in diff_pairs:
        e_in = get_emails_xlsx(p_in) if p_in.suffix == '.xlsx' else get_emails_csv(p_in)
        e_ok = get_emails_xlsx(p_ok) if p_ok.suffix == '.xlsx' else get_emails_csv(p_ok)
        diff = e_in - e_ok
        print(f"[*] Diff for {login:<38}: +{len(diff):,d} bad/risky leads added")
        for em in diff:
            if em not in new_suppression:
                dom = em.split("@")[-1]
                new_suppression[em] = (login, dom, "risky")

    print(f"\n[✓] Total Unique Bad & Risky Leads Compiled: {len(new_suppression):,d}")

    # Breakdown by login
    by_login = defaultdict(lambda: {'bad': 0, 'risky': 0, 'total': 0})
    for em, (l, d, st) in new_suppression.items():
        by_login[l][st] += 1
        by_login[l]['total'] += 1

    print("\nAccount-Wise Suppression Leads to Insert / Update:")
    print(f"{'#':<3} | {'Login Account':<42} | {'Bad':<6} | {'Risky':<6} | {'Total':<6}")
    print("-" * 75)
    for idx, (l, c) in enumerate(sorted(by_login.items(), key=lambda x: -x[1]['total']), 1):
        print(f"{idx:<3} | {l:<42} | {c['bad']:6d} | {c['risky']:6d} | {c['total']:6d}")

    # Insert into MySQL database table
    print("\n[*] Inserting / Updating in MySQL `million_verifier_cache` in chunks of 2,000...")
    insert_sql = """
        INSERT INTO million_verifier_cache (email, login_owner, domain, verification_status, verified_at)
        VALUES (%s, %s, %s, %s, '2026-09-30 23:59:59')
        ON DUPLICATE KEY UPDATE
            login_owner = COALESCE(VALUES(login_owner), login_owner),
            domain = COALESCE(VALUES(domain), domain),
            verification_status = VALUES(verification_status)
    """
    items = list(new_suppression.items())
    chunk_size = 2000
    for i in range(0, len(items), chunk_size):
        chunk = items[i:i + chunk_size]
        batch_data = [(em, l, d, s) for em, (l, d, s) in chunk]
        cur.executemany(insert_sql, batch_data)
        conn.commit()

    # Final DB audit
    cur.execute("SELECT verification_status, COUNT(*) FROM million_verifier_cache GROUP BY verification_status")
    after = dict(cur.fetchall())
    print("\n" + "=" * 80)
    print("FINAL DATABASE STATUS IN `million_verifier_cache`:")
    print("=" * 80)
    tot = sum(after.values())
    for st, cnt in after.items():
        print(f"  • {st.upper():<6}: {cnt:,d}")
    print(f"  ------------------------")
    print(f"  TOTAL : {tot:,d} (Zero 'good' leads - 100% Bad & Risky Blocklist)")

if __name__ == "__main__":
    main()
