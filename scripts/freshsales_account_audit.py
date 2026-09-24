#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freshsales Account Audit
========================
Fetches unique company accounts from:
  1. Freshsales CRM (via scroll API using view ID from /filters)
  2. MySQL RDS freshsales_companies table

Usage:
    python scripts/freshsales_account_audit.py
"""
from __future__ import annotations
import os, sys, time, requests
from pathlib import Path
from datetime import datetime

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

FS_API_KEY = os.getenv("FRESHSALES_API_KEY", "").strip()
FS_DOMAIN  = os.getenv("FRESHSALES_DOMAIN", "https://nestack.freshsales.io").rstrip("/")
DB_HOST    = os.getenv("DB_HOST", "").strip()
DB_USER    = os.getenv("DB_USER", "").strip()
DB_PASS    = os.getenv("DB_PASSWORD", "").strip()
DB_NAME    = os.getenv("DB_NAME", "apollo_scrapers").strip()
DB_PORT    = int(os.getenv("DB_PORT", "3306"))


def p(msg=""):
    print(msg, flush=True)


def divider(c="─", w=70):
    p(c * w)


# ---------------------------------------------------------------------------
# Freshsales helpers
# ---------------------------------------------------------------------------
_FS_HEADERS = lambda: {
    "Authorization": f"Token token={FS_API_KEY}",
    "Content-Type": "application/json",
}

def fs_get(path: str, **params) -> dict:
    url = f"{FS_DOMAIN}/{path.lstrip('/')}"
    r = requests.get(url, headers=_FS_HEADERS(), params=params or None, timeout=20)
    if r.status_code in (404, 403):
        return {"_status": r.status_code, "_text": r.text[:200]}
    r.raise_for_status()
    return r.json() if r.content else {}


def get_account_view_id() -> str | None:
    """Get the 'All Accounts' view ID from /api/sales_accounts/filters."""
    resp = fs_get("api/sales_accounts/filters")
    filters = resp.get("filters", [])
    if not filters:
        return None
    for f in filters:
        if str(f.get("name", "")).strip().lower() == "all accounts":
            return str(f.get("id"))
    return str(filters[0].get("id"))  # fallback to first filter


def fetch_fs_accounts() -> list[dict]:
    p("[1/2] Fetching Freshsales CRM accounts via Scroll API ...")

    view_id = get_account_view_id()
    if not view_id:
        p("      [ERROR] Could not retrieve any account view/filter ID. Skipping.")
        return []
    p(f"      Using view ID: {view_id}")

    accounts: list[dict] = []
    last_id  = None
    page_n   = 0

    while True:
        page_n += 1
        params: dict = {"limit": 100}
        if last_id is not None:
            params["last_fetched_id"] = last_id
        try:
            resp = fs_get(f"api/sales_accounts/scroll/{view_id}", **params)
        except Exception as exc:
            p(f"      [ERROR] Page {page_n}: {exc}")
            break

        if resp.get("_status") == 403:
            p(f"      [403 Forbidden] Scroll endpoint also blocked: {resp.get('_text','')}")
            break

        batch = resp.get("sales_accounts", [])
        if not batch:
            p(f"      No more results at page {page_n}.")
            break

        accounts.extend(batch)
        last_id = batch[-1].get("id")
        p(f"      Page {page_n}: +{len(batch)} accounts  (total: {len(accounts):,d})")

        if len(batch) < 100:
            break
        time.sleep(0.75)

    return accounts


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------
def fetch_mysql_accounts() -> list[dict]:
    p("\n[2/2] Querying MySQL freshsales_companies ...")
    try:
        import pymysql
        import pymysql.cursors
    except ImportError:
        p("      [SKIP] pymysql not installed.")
        return []

    if not DB_HOST:
        p("      [SKIP] DB_HOST not configured.")
        return []

    try:
        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            db=DB_NAME, port=DB_PORT, charset="utf8mb4",
            connect_timeout=10,
        )
        p(f"      Connected to {DB_NAME}@{DB_HOST}")

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Total count
            cur.execute("SELECT COUNT(*) AS cnt FROM freshsales_companies")
            total = cur.fetchone()["cnt"]
            p(f"      Total rows in freshsales_companies: {total:,d}")

            # Unique company names
            cur.execute("SELECT COUNT(DISTINCT company_name) AS cnt FROM freshsales_companies")
            unique_names = cur.fetchone()["cnt"]

            # Unique domains
            cur.execute("SELECT COUNT(DISTINCT domain) AS cnt FROM freshsales_companies WHERE domain IS NOT NULL AND domain != ''")
            unique_domains = cur.fetchone()["cnt"]

            # Unique websites
            cur.execute("SELECT COUNT(DISTINCT website) AS cnt FROM freshsales_companies WHERE website IS NOT NULL AND website != ''")
            unique_websites = cur.fetchone()["cnt"]

            # Sample rows (most recent)
            cur.execute("""
                SELECT id, account_id, company_name, domain, website, created_at
                FROM freshsales_companies
                ORDER BY created_at DESC
                LIMIT 50
            """)
            sample = list(cur.fetchall())

        conn.close()
        return [{
            "__total": total,
            "__unique_names": unique_names,
            "__unique_domains": unique_domains,
            "__unique_websites": unique_websites,
            "__sample": sample,
        }]

    except Exception as exc:
        p(f"      [ERROR] {exc}")
        return []


# ---------------------------------------------------------------------------
# Reporters
# ---------------------------------------------------------------------------
def report_fs(accounts: list[dict]):
    p()
    p("=" * 70)
    p("  FRESHSALES CRM  --  Sales Accounts (Companies)")
    p("=" * 70)
    if not accounts:
        p("  No accounts returned.")
        p()
        p("  NOTE: The API key may lack 'Sales Accounts' read permission.")
        p("  The bulk upsert (contacts) key often doesn't have accounts access.")
        p("  To enable: Freshsales Admin > API Settings > Role Permissions")
        return

    unique_names   = {str(a.get("name","")).strip().lower() for a in accounts if a.get("name")}
    unique_websites= {str(a.get("website","") or "").strip().lower() for a in accounts if a.get("website")}

    p(f"  Total accounts   : {len(accounts):>6,d}")
    p(f"  Unique by name   : {len(unique_names):>6,d}")
    p(f"  Unique by website: {len(unique_websites):>6,d}")
    p()

    sorted_acc = sorted(accounts, key=lambda x: x.get("id", 0), reverse=True)
    p(f"  {'#':<4}  {'Freshsales ID':<14}  {'Company Name':<35}  {'Website'}")
    divider()
    for i, a in enumerate(sorted_acc[:50], 1):
        name    = str(a.get("name","") or "")[:34]
        website = str(a.get("website","") or "")[:36]
        acc_id  = str(a.get("id",""))
        p(f"  {i:<4}  {acc_id:<14}  {name:<35}  {website}")
    if len(accounts) > 50:
        p(f"  ... and {len(accounts)-50:,d} more.")


def report_mysql(result: list[dict]):
    p()
    p("=" * 70)
    p("  MYSQL RDS  --  freshsales_companies")
    p("=" * 70)
    if not result:
        p("  No data.")
        return

    meta   = result[0]
    sample = meta.get("__sample", [])

    p(f"  Total rows          : {meta.get('__total', 0):>8,d}")
    p(f"  Unique company names: {meta.get('__unique_names', 0):>8,d}")
    p(f"  Unique domains      : {meta.get('__unique_domains', 0):>8,d}")
    p(f"  Unique websites     : {meta.get('__unique_websites', 0):>8,d}")
    p()
    p(f"  Most recently added (up to 50):")
    p()
    p(f"  {'#':<4}  {'FS Account ID':<16}  {'Company Name':<35}  {'Domain':<28}  {'Created At'}")
    divider()
    for i, r in enumerate(sample, 1):
        acc_id  = str(r.get("account_id","") or "")[:15]
        name    = str(r.get("company_name","") or "")[:34]
        domain  = str(r.get("domain","") or "")[:27]
        created = str(r.get("created_at","") or "")[:19]
        p(f"  {i:<4}  {acc_id:<16}  {name:<35}  {domain:<28}  {created}")
    if meta.get("__total", 0) > 50:
        p(f"  ... and {meta['__total']-50:,d} more rows.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    p()
    p("=" * 70)
    p("  FRESHSALES ACCOUNT AUDIT")
    p(f"  CRM  : {FS_DOMAIN}")
    p(f"  DB   : {DB_NAME}@{DB_HOST or 'N/A'}")
    p(f"  Time : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p("=" * 70)

    if not FS_API_KEY:
        p("[ERROR] FRESHSALES_API_KEY not configured. Check freshsales_agent/.env")
        sys.exit(1)

    fs_accs  = fetch_fs_accounts()
    mysql_r  = fetch_mysql_accounts()

    report_fs(fs_accs)
    report_mysql(mysql_r)

    p()
    p("=" * 70)
    p("  SUMMARY")
    p("=" * 70)
    p(f"  Freshsales CRM sales accounts : {len(fs_accs):,d}")
    if mysql_r:
        p(f"  MySQL freshsales_companies    : {mysql_r[0].get('__total',0):,d} total rows  "
          f"({mysql_r[0].get('__unique_names',0):,d} unique companies)")
    p()


if __name__ == "__main__":
    main()
