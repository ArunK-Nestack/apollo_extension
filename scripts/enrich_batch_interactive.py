#!/usr/bin/env python3
"""
Interactive Apollo Multi-Account Enrichment Cycle Manager
==========================================================
- Shows saved/pending leads, active cycle, and cached credits for all configured logins
- Selects a batch from `apollo_saved_leads` or `enrich_saved_leads`
- Filters to strictly 1 lead per unique company domain
- Defaults to the batch-owning login and performs a free live balance probe before enrichment
- Offers: enrich now, carry forward, or decide later
- Caps enrichment by the live balance minus a configurable safety reserve
- Executes in 10-lead micro-batches via POST /api/v1/people/bulk_match
- Disables personal email and phone revelation
- Updates `apollo_saved_leads` in-place under the SAME batch tag
- Records per-lead attempts plus a run-level credit/audit summary
"""

import sys
import os
import json
import time
import csv
import uuid
import argparse
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.api import (
    backfill_enrichment_ledger_from_saved_leads,
    ensure_batch_enrichment_ledger_table,
    ensure_enrichment_run_audit_table,
    ensure_lead_enrichment_state_table,
    fetch_lead_enrichment_decisions,
    fetch_unattempted_leads_for_batch,
    get_batch_enrichment_summary,
    get_connection,
    get_seniority_score,
    normalize_text,
    record_lead_enrichment_decisions,
    record_enrichment_ledger_attempts,
    start_enrichment_run,
    update_enrichment_run,
)
from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row
from scripts.lead_guardrails import apply_4_layer_guardrails

load_dotenv()

CONFIG_PATH = os.path.join("config", "apollo_accounts.json")
TEMPLATE_PATH = os.path.join("config", "apollo_accounts.template.json")
EXPORTS_DIR = os.path.join("dist", "exports")
CREDIT_REPORT_PATH = os.path.join("config", "apollo_live_account_report.json")
DEFAULT_CREDIT_RESERVE = 10

# =====================================================================
# SECURITY & CONFIGURATION VAULT
# =====================================================================

def mask_key(key: str) -> str:
    """Mask sensitive API keys for terminal display (e.g. sk_...4b2c)."""
    if not key or key == "YOUR_APOLLO_API_KEY_HERE":
        return "[NOT SET]"
    if len(key) <= 8:
        return "..." + key[-3:]
    return key[:3] + "..." + key[-4:]

def load_apollo_accounts() -> List[Dict[str, Any]]:
    """Load and validate the 19 accounts from config/apollo_accounts.json."""
    if not os.path.exists(CONFIG_PATH):
        if os.path.exists(TEMPLATE_PATH):
            import shutil
            shutil.copy(TEMPLATE_PATH, CONFIG_PATH)
        else:
            print(f"ERROR: {CONFIG_PATH} not found.")
            sys.exit(1)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            accounts = json.load(f)
    except Exception as ex:
        print(f"ERROR: Could not parse {CONFIG_PATH}: {ex}")
        sys.exit(1)

    if not isinstance(accounts, list) or len(accounts) == 0:
        print(f"ERROR: {CONFIG_PATH} must be a JSON array of accounts.")
        sys.exit(1)

    return accounts


def load_cached_credit_report() -> Dict[str, Dict[str, Any]]:
    """Load the last free credit probe without making an API request."""
    try:
        with open(CREDIT_REPORT_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    return {
        str(row.get("email") or "").strip().lower(): row
        for row in payload.get("accounts", [])
        if row.get("email")
    }


def get_account_credit_snapshot(account: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """Get a balance without touching a credit-consuming endpoint."""
    cached = load_cached_credit_report().get(str(account.get("email") or "").strip().lower(), {})
    if dry_run:
        return {
            "status": "dry_run",
            "credits_remaining": int(cached.get("credits_remaining", 0) or 0),
            "credits_avail": int(cached.get("credits_avail", 0) or 0),
            "source": "cached report",
        }

    try:
        from scripts.apollo_account_report import _probe_account

        live = _probe_account(account)
        if live.get("status") == "active":
            live["source"] = "live free account probe"
            return live
    except Exception as ex:
        live = {"status": f"error: {ex}"}

    if cached:
        return {
            **cached,
            "status": "cached",
            "source": "cached report fallback",
        }
    return {**live, "credits_remaining": 0, "credits_avail": 0, "source": "unavailable"}


def calculate_safe_enrichment_limit(eligible_count: int, credits_remaining: int, reserve: int = DEFAULT_CREDIT_RESERVE) -> int:
    return max(0, min(int(eligible_count or 0), int(credits_remaining or 0) - max(0, int(reserve or 0))))


def _batch_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def filter_canonical_cycle_batches(
    accounts: List[Dict[str, Any]],
    batches: List[Dict[str, Any]],
    cycle_resolver=None,
) -> List[Dict[str, Any]]:
    """Return only `login-email(active-cycle)` batches created by the cycle workflow."""
    if cycle_resolver is None:
        from scripts.apollo_saved_search_inspector import get_account_cycle_window

        cycle_resolver = lambda email: get_account_cycle_window(email)[2]

    canonical_keys = set()
    for account in accounts:
        email = str(account.get("email") or "").strip()
        if not email:
            continue
        try:
            cycle = cycle_resolver(email)
        except Exception:
            continue
        canonical_keys.add(_batch_key(f"{email}({cycle})"))

    return [batch for batch in batches if _batch_key(batch.get("batch", "")) in canonical_keys]


def print_account_overview(
    accounts: List[Dict[str, Any]],
    batches: List[Dict[str, Any]],
    credit_map: Dict[str, Dict[str, Any]],
) -> None:
    """Show all configured logins using local lead inventory and cached free credit data."""
    from scripts.apollo_saved_search_inspector import get_account_cycle_window

    print("\n[19-LOGIN SAVED LEAD & CREDIT OVERVIEW]")
    print(f"{'#':<3} {'Login':<38} {'Cycle':<22} {'Saved':>8} {'Pending':>9} {'Credits':>9}")
    print("-" * 95)
    for idx, account in enumerate(accounts, 1):
        email = str(account.get("email") or "").strip()
        email_key = email.lower()
        owned = [
            batch for batch in batches
            if str(batch.get("account_used") or "").strip().lower() == email_key
            or email_key in str(batch.get("batch") or "").lower()
        ]
        saved = sum(int(batch.get("total_leads") or 0) for batch in owned)
        pending = sum(int(batch.get("unenriched_count") or 0) for batch in owned)
        credits = int(credit_map.get(email_key, {}).get("credits_remaining", 0) or 0)
        try:
            _, _, cycle = get_account_cycle_window(email)
        except Exception:
            cycle = "unknown"
        print(f"[{idx:02d}] {email[:38]:<38} {cycle[:22]:<22} {saved:>8,d} {pending:>9,d} {credits:>9,d}")
    print("  Credits come from the last free account report; the selected login is probed again before a real run.")

# =====================================================================
# APOLLO API ENGINE (STRICT 1-CREDIT / BULK-10)
# =====================================================================

def check_account_credit_health(api_key: str) -> Dict[str, Any]:
    """Check account connection and available credit quota via Apollo API."""
    if not api_key or "YOUR_APOLLO_API_KEY" in api_key:
        return {"status": "unconfigured", "email_credits": 0, "message": "API Key is not configured."}

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }

    # Try auth health endpoint
    try:
        url = "https://api.apollo.io/api/v1/auth/health"
        res = requests.get(url, headers=headers, timeout=10)
        rate_limit = res.headers.get("x-rate-limit-minute", "1000")
        if res.status_code == 200:
            return {
                "status": "active",
                "rate_limit": f"{rate_limit} req/min",
                "email_credits": "Active (Tracked live per verified email)",
                "message": "Connected successfully"
            }
        elif res.status_code == 401 or res.status_code == 403:
            return {"status": "invalid", "rate_limit": "0", "email_credits": "Unauthorized", "message": "Invalid API Key"}
    except Exception as ex:
        return {"status": "error", "rate_limit": "0", "email_credits": "Error", "message": f"Connection error: {ex}"}

    return {"status": "active", "rate_limit": "1000 req/min", "email_credits": "Active", "message": "Key validated"}

class EnrichmentChunkResult(list):
    def __init__(self, values=(), credits_consumed: float = 0, request_failed: bool = False):
        super().__init__(values)
        self.credits_consumed = float(credits_consumed or 0)
        self.request_failed = bool(request_failed)


def enrich_leads_chunk(api_key: str, chunk: List[Dict[str, Any]], dry_run: bool = False) -> EnrichmentChunkResult:
    """
    Call Apollo bulk_match endpoint for up to 10 leads.
    Strictly enforce:
      - reveal_personal_emails: false
      - reveal_phone_number: false (0 mobile credits)
    """
    if dry_run:
        # Simulate dry run output
        simulated = []
        for r in chunk:
            simulated.append({
                "db_id": r["id"],
                "apollo_id": r["apollo_id"],
                "email": f"{r['first_name'].lower() or 'lead'}@{r['company_domain']}",
                "email_status": "verified",
                "annual_revenue": "$10M - $25M",
                "employee_count": 50,
                "industry": "Manufacturing",
                "tech_stack": ["Microsoft 365", "AWS", "WordPress"],
                "keywords": ["engineering", "fabrication"],
                "company_phone": "+1 800-555-0199",
                "hq_address": "100 Industrial Parkway, Chicago, IL 60601, US",
                "company_linkedin_url": f"https://www.linkedin.com/company/{r['company_domain'].split('.')[0]}",
                "credits_charged": 0
            })
        return EnrichmentChunkResult(simulated, credits_consumed=0)

    url = "https://api.apollo.io/api/v1/people/bulk_match"
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }

    details_payload = []
    id_map = {}
    for r in chunk:
        item = {}
        if r.get("apollo_id") and not r["apollo_id"].startswith("apollo-row-"):
            item["id"] = r["apollo_id"]
        if r.get("first_name"):
            item["first_name"] = r["first_name"]
        if r.get("last_name"):
            item["last_name"] = r["last_name"]
        if r.get("name"):
            item["name"] = r["name"]
        if r.get("company_domain"):
            item["domain"] = r["company_domain"]
        if r.get("company"):
            item["organization_name"] = r["company"]

        details_payload.append(item)
        # Map back by apollo_id or domain/name
        id_map[r.get("apollo_id")] = r["id"]
        id_map[(normalize_text(r.get("name")), r.get("company_domain"))] = r["id"]

    body = {
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
        "details": details_payload
    }

    max_retries = 3
    backoff = 2.0

    for attempt in range(1, max_retries + 1):
        try:
            res = requests.post(url, json=body, headers=headers, timeout=25)
            
            # Rate limit backoff
            if res.status_code == 429:
                wait_sec = int(res.headers.get("Retry-After", 10))
                print(f"\n[Apollo Rate Limit 429] Backing off for {wait_sec}s...")
                time.sleep(wait_sec)
                continue

            if res.status_code == 402:
                print(f"\n[Apollo Account Quota Exhausted] Status 402: {res.text}")
                return EnrichmentChunkResult(request_failed=True)

            if res.status_code == 422:
                print(f"\n[Apollo Unprocessable Entity 422] {res.text}")
                return EnrichmentChunkResult(request_failed=True)

            if not res.ok:
                print(f"\n[Apollo API Error {res.status_code}] {res.text[:150]}")
                time.sleep(backoff)
                backoff *= 2
                continue

            data = res.json()
            matches = data.get("matches") or []
            
            parsed_results = []
            for m in matches:
                if not m:
                    continue
                
                # Match to DB record ID
                m_apollo_id = m.get("id") or ""
                m_name = normalize_text(m.get("name") or "")
                org = m.get("organization") or {}
                m_domain = org.get("primary_domain") or ""

                db_id = id_map.get(m_apollo_id) or id_map.get((m_name, m_domain))
                
                # Extract Verified Email
                email = (m.get("email") or "").strip()
                email_status = m.get("email_status") or ("verified" if email else "unavailable")
                credits_charged = 1 if (email and email_status != "unavailable") else 0

                # Extract All 25+ Free Company Columns
                annual_rev = org.get("annual_revenue_printed") or (f"${org.get('annual_revenue'):,}" if org.get("annual_revenue") else "")
                emp_count = org.get("estimated_num_employees")
                industry = org.get("industry") or ""
                
                # Tech stack
                tech_list = []
                for t in org.get("current_technologies") or []:
                    if isinstance(t, dict) and t.get("name"):
                        tech_list.append(t["name"])
                    elif isinstance(t, str):
                        tech_list.append(t)

                # Keywords
                kw_list = org.get("keywords") or []

                # Address
                address_parts = [org.get("street_address"), org.get("city"), org.get("state"), org.get("postal_code"), org.get("country")]
                hq_address = ", ".join([str(p).strip() for p in address_parts if p]) or (org.get("raw_address") or "")

                company_phone = org.get("sanitized_phone") or ""
                company_linkedin = org.get("linkedin_url") or ""

                parsed_results.append({
                    "db_id": db_id,
                    "apollo_id": m_apollo_id,
                    "email": email,
                    "email_status": email_status,
                    "annual_revenue": str(annual_rev)[:64],
                    "employee_count": emp_count,
                    "industry": str(industry)[:128],
                    "tech_stack": tech_list,
                    "keywords": kw_list,
                    "company_phone": str(company_phone)[:64],
                    "hq_address": str(hq_address)[:512],
                    "company_linkedin_url": str(company_linkedin)[:512],
                    "credits_charged": credits_charged,
                    "raw_match": m
                })

            request_credits = data.get("credits_consumed")
            if request_credits is None:
                request_credits = sum(float(row.get("credits_charged") or 0) for row in parsed_results)
            return EnrichmentChunkResult(parsed_results, credits_consumed=request_credits)

        except Exception as ex:
            if attempt == max_retries:
                print(f"\n[Network Error after {max_retries} attempts]: {ex}")
                return EnrichmentChunkResult(request_failed=True)
            time.sleep(backoff)
            backoff *= 2

    return EnrichmentChunkResult(request_failed=True)

# =====================================================================
# DATABASE IN-PLACE UPDATE
# =====================================================================

def update_leads_in_db(
    results: List[Dict[str, Any]],
    account_name: str,
    batch_tag: str,
    dry_run: bool = False,
    conn=None,
    table_name: str = "apollo_saved_leads",
    cycle: str = "",
):
    """Update enriched columns directly in `apollo_saved_leads` or `enrich_saved_leads` under the same batch."""
    if dry_run or not results:
        return

    target_table = "enrich_saved_leads" if table_name == "enrich_saved_leads" else "apollo_saved_leads"

    update_sql = f"""
        UPDATE `{target_table}` SET
            `apollo_id` = %s,
            `email` = %s,
            `email_status` = %s,
            `annual_revenue` = %s,
            `employee_count` = %s,
            `industry` = %s,
            `tech_stack` = %s,
            `keywords` = %s,
            `company_phone` = %s,
            `hq_address` = %s,
            `company_linkedin_url` = %s,
            `account_used` = %s,
            `cycle` = IF(%s != '', %s, `cycle`),
            `credits_charged` = %s,
            `raw_enrichment_data` = %s,
            `enriched_at` = NOW()
        WHERE `id` = %s AND `batch` = %s;
    """

    params = []
    for r in results:
        if not r.get("db_id"):
            continue
        params.append((
            r.get("apollo_id") or "",
            r.get("email") or "",
            r.get("email_status") or "",
            r.get("annual_revenue") or "",
            r.get("employee_count"),
            r.get("industry") or "",
            json.dumps(r.get("tech_stack") or []),
            json.dumps(r.get("keywords") or []),
            r.get("company_phone") or "",
            r.get("hq_address") or "",
            r.get("company_linkedin_url") or "",
            account_name,
            cycle,
            cycle,
            r.get("credits_charged", 0),
            json.dumps(r.get("raw_match") or {}),
            r["db_id"],
            batch_tag
        ))

    if not params:
        return

    if conn is not None:
        with conn.cursor() as cur:
            cur.executemany(update_sql, params)
        return

    with get_connection() as own_conn:
        with own_conn.cursor() as cur:
            cur.executemany(update_sql, params)
            own_conn.commit()

# =====================================================================
# INTERACTIVE CLI WORKFLOW
# =====================================================================

def run_interactive_enricher():
    parser = argparse.ArgumentParser(description="Interactive Multi-Account Apollo Enrichment Cycle Manager")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without spending Apollo credits or calling external API")
    parser.add_argument("--table", "-t", default="all", choices=["all", "apollo", "enrich"], help="Source table filter: 'all', 'apollo', or 'enrich' (default: all)")
    parser.add_argument("--batch", "-b", default=None, help="Batch name to enrich directly")
    parser.add_argument("--action", choices=["enrich", "carry", "later"], help="Preselect the decision for automation/tests")
    parser.add_argument("--credit-reserve", type=int, default=DEFAULT_CREDIT_RESERVE, help="Credits that must remain unused")
    parser.add_argument("--all-cycles", action="store_true", help="Include legacy and historical batches instead of only the active canonical cycle")
    args = parser.parse_args()

    print("\n" + "=" * 95)
    print("      >>> APOLLO MULTI-ACCOUNT 1-CREDIT LEAD ENRICHMENT ENGINE <<<")
    print("=" * 95)
    if args.dry_run:
        print("  [DRY-RUN MODE ENABLED]: No credits will be deducted; external API calls simulated.")

    # -------------------------------------------------------------
    # STEP 1: FETCH AND SELECT BATCH
    # -------------------------------------------------------------
    print("\n[STEP 1: SELECT BATCH FROM DATABASE]")
    with get_connection() as conn:
        if not args.dry_run:
            ensure_batch_enrichment_ledger_table(conn)
            ensure_enrichment_run_audit_table(conn)
            ensure_lead_enrichment_state_table(conn)
            backfill_enrichment_ledger_from_saved_leads(conn, table_name="apollo_saved_leads")
            backfill_enrichment_ledger_from_saved_leads(conn, table_name="enrich_saved_leads")
            conn.commit()

        with conn.cursor() as cur:
            # Query apollo_saved_leads batches
            cur.execute("""
                SELECT
                    'apollo_saved_leads' AS source_table,
                    'Apollo' AS source_label,
                    l.batch,
                    COUNT(*) AS total_leads,
                    COUNT(DISTINCT l.company_domain) AS unique_domains,
                    SUM(CASE WHEN e.id IS NULL THEN 1 ELSE 0 END) AS unenriched_count,
                    MAX(NULLIF(l.account_used, '')) AS account_used,
                    MAX(NULLIF(l.cycle, '')) AS cycle
                FROM apollo_saved_leads l
                LEFT JOIN batch_enrichment_ledger e
                  ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                GROUP BY l.batch
                ORDER BY total_leads DESC;
            """)
            raw_apollo = cur.fetchall()

            # Query enrich_saved_leads batches
            cur.execute("""
                SELECT
                    'enrich_saved_leads' AS source_table,
                    'Enrich.so' AS source_label,
                    l.batch,
                    COUNT(*) AS total_leads,
                    COUNT(DISTINCT l.company_domain) AS unique_domains,
                    SUM(CASE WHEN e.id IS NULL THEN 1 ELSE 0 END) AS unenriched_count,
                    MAX(NULLIF(l.account_used, '')) AS account_used,
                    MAX(NULLIF(l.cycle, '')) AS cycle
                FROM enrich_saved_leads l
                LEFT JOIN batch_enrichment_ledger e
                  ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                GROUP BY l.batch
                ORDER BY total_leads DESC;
            """)
            raw_enrich = cur.fetchall()

    all_batches_data = []
    for r in raw_apollo:
        all_batches_data.append({
            "source_table": r[0],
            "source_label": r[1],
            "batch": r[2],
            "total_leads": int(r[3] or 0),
            "unique_domains": int(r[4] or 0),
            "unenriched_count": int(r[5] or 0),
            "account_used": str(r[6] or ""),
            "cycle": str(r[7] or ""),
        })
    for r in raw_enrich:
        all_batches_data.append({
            "source_table": r[0],
            "source_label": r[1],
            "batch": r[2],
            "total_leads": int(r[3] or 0),
            "unique_domains": int(r[4] or 0),
            "unenriched_count": int(r[5] or 0),
            "account_used": str(r[6] or ""),
            "cycle": str(r[7] or ""),
        })

    if not all_batches_data:
        print("No batches found in `apollo_saved_leads` or `enrich_saved_leads`.")
        return

    accounts = load_apollo_accounts()
    credit_map = load_cached_credit_report()
    current_cycle_batches = filter_canonical_cycle_batches(accounts, all_batches_data)
    selectable_batches = all_batches_data if args.all_cycles else current_cycle_batches
    print_account_overview(accounts, selectable_batches, credit_map)
    print(
        "  Batch scope: "
        + ("all legacy and historical batches (--all-cycles)" if args.all_cycles else "active canonical cycle batches only")
    )

    active_filter = args.table.lower()

    # Pre-selection if --batch is provided
    selected_item = None
    if args.batch:
        match = next((b for b in all_batches_data if b["batch"].lower() == args.batch.lower()), None)
        if match:
            selected_item = match
        else:
            print(f"[!] Batch '{args.batch}' not found. Falling back to interactive selection.")

    while not selected_item:
        if active_filter == "apollo":
            visible = [b for b in selectable_batches if b["source_table"] == "apollo_saved_leads"]
            filter_desc = "Apollo Only (`apollo_saved_leads`)"
        elif active_filter == "enrich":
            visible = [b for b in selectable_batches if b["source_table"] == "enrich_saved_leads"]
            filter_desc = "Enrich.so Only (`enrich_saved_leads`)"
        else:
            visible = selectable_batches
            filter_desc = "All Tables (Apollo + Enrich.so)"

        print(f"\nActive Filter: {filter_desc}")
        if not visible:
            print("No batches match the active cycle scope. Use --all-cycles to inspect historical batches.")
            return
        print(f"{'#':<3} {'Source':<12} {'Batch Identifier':<42} {'Total':<8} {'Unique Doms':<12} {'Ready to Enrich':<15}")
        print("-" * 96)
        for idx, b in enumerate(visible, 1):
            name_disp = b["batch"]
            if len(name_disp) > 42:
                name_disp = name_disp[:39] + "..."
            print(f"[{idx:02d}] [{b['source_label']:<8}] {name_disp:<42} {b['total_leads']:<8,d} {b['unique_domains']:<12,d} {b['unenriched_count']:<15,d}")

        prompt = f"\n>> Select Batch Number [1-{len(visible)}] (or 'T' to toggle filter): "
        user_choice = input(prompt).strip()

        if user_choice.upper() == "T":
            # Cycle filter: all -> enrich -> apollo -> all
            if active_filter == "all":
                active_filter = "enrich"
            elif active_filter == "enrich":
                active_filter = "apollo"
            else:
                active_filter = "all"
            continue

        if user_choice.isdigit() and 1 <= int(user_choice) <= len(visible):
            selected_item = visible[int(user_choice) - 1]
            break

        # Also support typing partial batch name
        matched_by_name = [b for b in visible if user_choice.lower() in b["batch"].lower()]
        if len(matched_by_name) == 1:
            selected_item = matched_by_name[0]
            break

        print("Invalid choice. Please enter a valid batch number or 'T' to toggle.")

    selected_batch = selected_item["batch"]
    target_table = selected_item["source_table"]
    source_label = selected_item["source_label"]

    print(f"\n✓ Selected Batch: '{selected_batch}' (Source: {source_label} | Table: `{target_table}`)")

    # -------------------------------------------------------------
    # FILTER TO UNIQUE DOMAINS (1 LEAD PER COMPANY POLICY)
    # -------------------------------------------------------------
    print(f"Loading unattempted leads from `{target_table}` (ledger) and isolating 1 highest-ranking lead per domain...")
    with get_connection() as conn:
        raw_leads = fetch_unattempted_leads_for_batch(conn, selected_batch, table_name=target_table)
        ledger_before = get_batch_enrichment_summary(conn, selected_batch)
        saved_decisions = fetch_lead_enrichment_decisions(
            conn, target_table, selected_batch, ensure_table=not args.dry_run
        )

    domain_to_lead = {}
    for lead_dict in raw_leads:
        dom = (lead_dict["company_domain"] or "").lower().strip()
        if not dom:
            continue
        score = get_seniority_score(lead_dict["job_title"] or "")
        if dom not in domain_to_lead or score > domain_to_lead[dom]["score"]:
            domain_to_lead[dom] = {"lead": lead_dict, "score": score}

    raw_eligible = [v["lead"] for v in domain_to_lead.values()]
    print(f"✓ {ledger_before['attempted']} lead(s) already attempted in this batch (ledger).")
    print(f"✓ {len(raw_leads)} unattempted row(s); {len(raw_eligible)} unique domains ready.")

    # Run through full 4-layer defense guardrails
    eligible_leads, metrics = apply_4_layer_guardrails(raw_eligible, selected_batch, verbose=True, table_name=target_table)

    if not eligible_leads:
        print("\nNo unattempted leads left in this batch (ledger) or all collided with CRM. Nothing to do.")
        return

    # -------------------------------------------------------------
    # STEP 2: SELECT APOLLO ACCOUNT LOGIN
    # -------------------------------------------------------------
    print("\n[STEP 2: SELECT APOLLO ACCOUNT LOGIN (FROM 19 SECURE LOGINS)]")

    print(f"{'#':<3} {'Account Name':<25} {'Email / Login':<35} {'API Key Vault':<15}")
    print("-" * 80)
    for a in accounts:
        key_status = mask_key(a.get("api_key", ""))
        print(f"[{a['id']:02d}] {a['name']:<25} {a.get('email', ''):<35} {key_status:<15}")

    owner_email = str(selected_item.get("account_used") or "").strip().lower()
    default_account_index = next(
        (idx for idx, account in enumerate(accounts, 1) if str(account.get("email") or "").strip().lower() == owner_email),
        None,
    )
    while True:
        default_hint = f", Enter for owner [{default_account_index}]" if default_account_index else ""
        a_choice = input(f"\n>> Select Login to deduct credits from [1-{len(accounts)}{default_hint}]: ").strip()
        if not a_choice and default_account_index:
            selected_account = accounts[default_account_index - 1]
            break
        if a_choice.isdigit() and 1 <= int(a_choice) <= len(accounts):
            selected_account = accounts[int(a_choice) - 1]
            break
        print("Invalid choice, please select a valid account number.")

    acc_key = selected_account.get("api_key", "").strip()
    if not acc_key or acc_key == "YOUR_APOLLO_API_KEY_HERE":
        if not args.dry_run:
            print(f"\n❌ ERROR: Account '{selected_account['name']}' has no API key set in {CONFIG_PATH}.")
            print("Please paste your real API key into config/apollo_accounts.json or run with --dry-run.")
            return
        else:
            acc_key = "mock_key_dry_run"

    login_email = (selected_account.get("email") or "").strip()
    from scripts.apollo_saved_search_inspector import get_account_cycle_window

    cycle_start, cycle_end, active_cycle = get_account_cycle_window(login_email)
    _, _, next_cycle = get_account_cycle_window(login_email, target_date=cycle_end + timedelta(seconds=1))

    deferred_count = 0
    available_now = []
    for lead in eligible_leads:
        decision = saved_decisions.get(int(lead["id"]), {})
        if decision.get("decision") == "carry_forward" and decision.get("target_cycle") != active_cycle:
            deferred_count += 1
            continue
        available_now.append(lead)
    eligible_leads = available_now

    if deferred_count:
        print(f"  • Deferred to another cycle: {deferred_count}")
    if not eligible_leads:
        print("\nNo leads are eligible in this login's active cycle.")
        return

    print(f"\n✓ Checking credits for '{selected_account['name']}' using a non-enrichment account probe...")
    credit_snapshot = get_account_credit_snapshot(selected_account, dry_run=args.dry_run)
    credits_before = int(credit_snapshot.get("credits_remaining", 0) or 0)
    safe_limit = len(eligible_leads) if args.dry_run else calculate_safe_enrichment_limit(
        len(eligible_leads), credits_before, args.credit_reserve
    )
    print(f"  • Credit source:      {credit_snapshot.get('source', 'unknown')}")
    print(f"  • Credits remaining:  {credits_before:,}")
    print(f"  • Safety reserve:     {max(0, args.credit_reserve):,}")
    print(f"  • Eligible this cycle:{len(eligible_leads):>7,d}")
    print(f"  • Safe to enrich now: {safe_limit:>7,d}{' (simulated)' if args.dry_run else ''}")

    print("\nWhat would you like to do?")
    print("  [1] Enrich those")
    print("  [2] Carry forward to the next cycle")
    print("  [3] Decide later")
    action_map = {"1": "enrich", "2": "carry", "3": "later"}
    if args.action:
        selected_action = args.action
        print(f">> Preselected action: {selected_action}")
    else:
        while True:
            selected_action = action_map.get(input(">> Enter choice [1-3]: ").strip())
            if selected_action:
                break
            print("Please enter 1, 2, or 3.")

    if selected_action in {"carry", "later"}:
        decision = "carry_forward" if selected_action == "carry" else "decide_later"
        target_cycle = next_cycle if selected_action == "carry" else active_cycle
        if args.dry_run:
            saved_count = len(eligible_leads)
            prefix = "Would mark"
        else:
            with get_connection() as conn:
                saved_count = record_lead_enrichment_decisions(
                    conn,
                    target_table,
                    selected_batch,
                    [lead["id"] for lead in eligible_leads],
                    decision,
                    target_cycle,
                )
                conn.commit()
            prefix = "Marked"
        if selected_action == "carry":
            print(f"\n✓ {prefix} {saved_count:,} lead(s) for the next cycle: {target_cycle}.")
        else:
            print(f"\n✓ {prefix} {saved_count:,} lead(s) as decide later; they will appear on the next scan.")
        print("✓ Original source batch and cycle were not changed. No enrichment credits were used.")
        return

    if safe_limit <= 0:
        print("\nNo credits are safely available after the configured reserve. Nothing was submitted to Apollo.")
        return

    # -------------------------------------------------------------
    # STEP 3: QUANTITY SELECTION PROMPT
    # -------------------------------------------------------------
    print("\n[STEP 3: QUANTITY SELECTION]")
    print(f"  • Eligible leads available in this batch: {len(eligible_leads)}")
    print(f"  • Maximum allowed by credit guard: {safe_limit}")

    while True:
        qty_input = input(f">> How many leads do you want to enrich? [1 - {safe_limit}, press Enter for all ({safe_limit})]: ").strip()
        if not qty_input:
            target_count = safe_limit
            break
        if qty_input.isdigit() and 1 <= int(qty_input) <= safe_limit:
            target_count = int(qty_input)
            break
        print(f"Please enter a valid number between 1 and {safe_limit}.")

    leads_to_process = eligible_leads[:target_count]
    chunk_size = 10
    total_chunks = (len(leads_to_process) + chunk_size - 1) // chunk_size

    session_id = str(uuid.uuid4())

    print(f"\n✓ Confirmed: Enriching {len(leads_to_process)} leads using account '{selected_account['name']}'.")
    print(f"  • Login email:     {login_email or '(not set in config)'}")
    print(f"  • Active Cycle:    {active_cycle}")
    print(f"  • Session ID:      {session_id}")
    print(f"  • Chunks of 10:    {total_chunks} calls")
    print(f"  • Rate Throttle:   0.9s per chunk (~600 leads/minute)")
    print(f"  • Credit ceiling:  {len(leads_to_process)}; actual Apollo response usage will be recorded")

    confirm = input("\n>> Ready to execute? Press [Enter] to start (or 'n' to cancel): ").strip()
    if confirm.lower() == 'n':
        print("Cancelled by operator.")
        return

    # -------------------------------------------------------------
    # STEP 4: EXECUTION LOOP (CHUNKS OF 10)
    # -------------------------------------------------------------
    print("\n" + "=" * 95)
    print(f">>> STARTING ENRICHMENT STREAM: {len(leads_to_process)} LEADS ({total_chunks} CHUNKS)")
    print("=" * 95)

    total_enriched_emails = 0
    total_credits_spent = 0.0
    total_free_companies_saved = 0
    all_enriched_records = []
    interrupted = False

    if not args.dry_run:
        with get_connection() as conn:
            start_enrichment_run(
                conn,
                login_email,
                selected_batch,
                credits_before,
                run_id=session_id,
            )
            conn.commit()

    start_time = time.time()

    for chunk_idx in range(total_chunks):
        chunk = leads_to_process[chunk_idx * chunk_size : (chunk_idx + 1) * chunk_size]

        results = enrich_leads_chunk(acc_key, chunk, dry_run=args.dry_run)

        if getattr(results, "request_failed", False) and not args.dry_run:
            print("\n[Execution Interrupted] No response from Apollo API. Stopping safely.")
            interrupted = True
            break

        for lead in chunk:
            match = next((r for r in results if r.get("db_id") == lead["id"]), None)
            if match:
                all_enriched_records.append(match)
                if match.get("email"):
                    total_enriched_emails += 1
                else:
                    total_free_companies_saved += 1
            else:
                all_enriched_records.append({"db_id": lead["id"], "email": "", "credits_charged": 0})
                total_free_companies_saved += 1

        total_credits_spent += float(getattr(results, "credits_consumed", 0) or 0)

        if not args.dry_run:
            with get_connection() as conn:
                record_enrichment_ledger_attempts(
                    conn,
                    selected_batch,
                    session_id,
                    login_email,
                    selected_account["name"],
                    chunk,
                    results,
                )
                update_leads_in_db(
                    results,
                    selected_account["name"],
                    selected_batch,
                    dry_run=False,
                    conn=conn,
                    table_name=target_table,
                    cycle=active_cycle,
                )
                update_enrichment_run(
                    conn,
                    session_id,
                    len(all_enriched_records),
                    total_enriched_emails,
                    total_credits_spent,
                    "running",
                )
                conn.commit()

        processed_so_far = min((chunk_idx + 1) * chunk_size, len(leads_to_process))
        pct = (processed_so_far / len(leads_to_process)) * 100
        bar_len = 30
        filled = int(bar_len * processed_so_far // len(leads_to_process))
        bar = "█" * filled + "░" * (bar_len - filled)

        print(f"\r[{bar}] {processed_so_far}/{len(leads_to_process)} ({pct:4.1f}%) | "
              f"Emails: {total_enriched_emails} (Credits: {total_credits_spent}) | "
              f"No Email: {total_free_companies_saved}", end="", flush=True)

        # Rate-limiting pause between chunks (0.9 seconds)
        if chunk_idx < total_chunks - 1 and not args.dry_run:
            time.sleep(0.9)

    elapsed = time.time() - start_time
    final_status = "completed" if len(all_enriched_records) == len(leads_to_process) else (
        "partial" if all_enriched_records else "failed"
    )
    if interrupted and not all_enriched_records:
        final_status = "failed"
    if not args.dry_run:
        with get_connection() as conn:
            update_enrichment_run(
                conn,
                session_id,
                len(all_enriched_records),
                total_enriched_emails,
                total_credits_spent,
                final_status,
            )
            conn.commit()
    print(f"\n\n✓ Completed in {elapsed:.1f}s ({len(all_enriched_records)} leads processed).")

    # -------------------------------------------------------------
    # STEP 5: FINAL SUMMARY & OPTIONAL EXPORT
    # -------------------------------------------------------------
    print("\n" + "=" * 95)
    print("FINAL ENRICHMENT AUDIT & SCORECARD")
    print("=" * 95)
    print(f"  • Batch Tag:                         {selected_batch} (Table: `{target_table}`)")
    print(f"  • Account Used:                      {selected_account['name']}")
    print(f"  • Login Name:                        {login_email}")
    print(f"  • Run ID:                            {session_id}")
    print(f"  • Run Status:                        {'dry_run' if args.dry_run else final_status}")
    print(f"  • Total Unique Leads Processed:      {len(all_enriched_records)}")
    print(f"  • Verified Business Emails Found:    {total_enriched_emails} ({(total_enriched_emails/max(1, len(all_enriched_records)))*100:.1f}%)")
    print(f"  • Leads Not Enriched:                {len(all_enriched_records) - total_enriched_emails}")
    print(f"  • Credits Remaining Before:          {credits_before}")
    print(f"  • Actual Apollo Credits Spent:       {total_credits_spent:g}")
    print(f"  • Estimated Credits Remaining:       {max(0, credits_before - total_credits_spent):g}")
    print(f"  • Mobile Credits Deducted:           0 (Zero mobile charges)")
    print(f"  • Free Company Firmographics Saved:  {len(all_enriched_records)} Companies (Revenue, Tech Stack, Address, etc.)")
    print(f"  • Remaining Unattempted (ledger):    {max(0, len(eligible_leads) - len(leads_to_process))}")
    if not args.dry_run:
        with get_connection() as conn:
            ledger_after = get_batch_enrichment_summary(conn, selected_batch)
        print(f"  • Ledger total for batch:            {ledger_after['attempted']} attempted, "
              f"{ledger_after['emails_found']} emails, {ledger_after['no_email']} no-email, "
              f"{ledger_after['credits_spent']} credits")
    print("=" * 95)

    if args.dry_run:
        print("DRY RUN: no Apollo enrichment request was sent and no database rows were changed.")
        return

    # Optional CSV export prompt
    export_choice = input("\n>> Export these newly enriched leads to CSV now? [Y/n]: ").strip()
    if export_choice.lower() != 'n':
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "enrich" if target_table == "enrich_saved_leads" else "apollo"
        export_file = os.path.join(EXPORTS_DIR, f"{prefix}_contacts_export_{selected_batch[:30]}_{len(all_enriched_records)}_{timestamp}.csv")
        
        # Query full enriched rows from DB
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT 
                        id, batch, apollo_id, name, first_name, last_name, job_title, email, email_status,
                        company, company_domain, website_link, annual_revenue, employee_count,
                        industry, tech_stack, keywords, company_phone, hq_address, location, linkedin_url,
                        company_linkedin_url, apollo_profile_url, segment, account_used, credits_charged,
                        raw_enrichment_data, enriched_at, created_at
                    FROM `{target_table}`
                    WHERE batch = %s AND enriched_at IS NOT NULL
                    ORDER BY enriched_at DESC
                    LIMIT %s;
                """, (selected_batch, len(all_enriched_records)))
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        if rows:
            with open(export_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(APOLLO_75_HEADERS)
                for r in rows:
                    writer.writerow(format_apollo_lead_row(r, selected_account.get("email", "")))
            print(f"✓ Successfully exported {len(rows)} enriched leads (Official 75-Column Apollo Schema) to:\n  {os.path.abspath(export_file)}")

if __name__ == "__main__":
    try:
        run_interactive_enricher()
    except KeyboardInterrupt:
        print("\n\n[!] Operation cancelled by operator. Exiting safely.\n")
        sys.exit(0)
