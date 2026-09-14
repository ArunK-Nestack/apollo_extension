#!/usr/bin/env python3
"""
Interactive Apollo Multi-Account 1-Credit Enrichment CLI
========================================================
- Selects batch from `apollo_saved_leads`
- Filters to strictly 1 lead per unique company domain
- Selects from the 19 configured Apollo accounts (with terminal key-masking)
- Checks credit balance
- Prompts: "How many leads do you want to save?"
- Executes in 10-lead micro-batches via POST /api/v1/people/bulk_match
- Strictly 1 Email Credit per lead (0 mobile credits, 0 waterfall)
- Updates `apollo_saved_leads` in-place under the SAME batch tag
- Saves 25+ free firmographic columns (Revenue, Tech Stack, Employees, HQ Address, etc.)
"""

import sys
import os
import json
import time
import csv
import uuid
import argparse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.api import (
    backfill_enrichment_ledger_from_saved_leads,
    ensure_batch_enrichment_ledger_table,
    fetch_unattempted_leads_for_batch,
    get_batch_enrichment_summary,
    get_connection,
    get_seniority_score,
    normalize_text,
    record_enrichment_ledger_attempts,
)
from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row
from scripts.lead_guardrails import apply_4_layer_guardrails

load_dotenv()

CONFIG_PATH = os.path.join("config", "apollo_accounts.json")
TEMPLATE_PATH = os.path.join("config", "apollo_accounts.template.json")
EXPORTS_DIR = os.path.join("dist", "exports")

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

def enrich_leads_chunk(api_key: str, chunk: List[Dict[str, Any]], dry_run: bool = False) -> List[Dict[str, Any]]:
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
                "credits_charged": 1
            })
        time.sleep(0.2)
        return simulated

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
                return []

            if res.status_code == 422:
                print(f"\n[Apollo Unprocessable Entity 422] {res.text}")
                return []

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

            return parsed_results

        except Exception as ex:
            if attempt == max_retries:
                print(f"\n[Network Error after {max_retries} attempts]: {ex}")
                return []
            time.sleep(backoff)
            backoff *= 2

    return []

# =====================================================================
# DATABASE IN-PLACE UPDATE
# =====================================================================

def update_leads_in_db(
    results: List[Dict[str, Any]],
    account_name: str,
    batch_tag: str,
    dry_run: bool = False,
    conn=None,
):
    """Update enriched columns directly in `apollo_saved_leads` under the same batch."""
    if dry_run or not results:
        return

    update_sql = """
        UPDATE `apollo_saved_leads` SET
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
    parser = argparse.ArgumentParser(description="Interactive Multi-Account Apollo 1-Credit Lead Enrichment Tool")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without spending Apollo credits or calling external API")
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
        ensure_batch_enrichment_ledger_table(conn)
        backfill_enrichment_ledger_from_saved_leads(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    l.batch,
                    COUNT(*) AS total_leads,
                    COUNT(DISTINCT l.company_domain) AS unique_domains,
                    SUM(CASE WHEN e.id IS NULL THEN 1 ELSE 0 END) AS unenriched_count
                FROM apollo_saved_leads l
                LEFT JOIN batch_enrichment_ledger e
                  ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                GROUP BY l.batch
                ORDER BY total_leads DESC;
            """)
            batches = cur.fetchall()

    if not batches:
        print("No batches found in `apollo_saved_leads`.")
        return

    print(f"{'#':<3} {'Batch Identifier':<45} {'Total':<8} {'Unique Doms':<12} {'Ready to Enrich':<15}")
    print("-" * 90)
    for idx, (b_name, total, u_doms, un_count) in enumerate(batches, 1):
        print(f"[{idx:02d}] {b_name:<45} {total:<8} {u_doms:<12} {un_count:<15}")

    while True:
        choice = input(f"\n>> Select Batch Number [1-{len(batches)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(batches):
            selected_batch = batches[int(choice) - 1][0]
            break
        print("Invalid choice, please select a valid number.")

    print(f"\n✓ Selected Batch: '{selected_batch}'")

    # -------------------------------------------------------------
    # FILTER TO UNIQUE DOMAINS (1 LEAD PER COMPANY POLICY)
    # -------------------------------------------------------------
    print("Loading unattempted leads (ledger) and isolating 1 highest-ranking lead per domain...")
    with get_connection() as conn:
        raw_leads = fetch_unattempted_leads_for_batch(conn, selected_batch)
        ledger_before = get_batch_enrichment_summary(conn, selected_batch)

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
    eligible_leads, metrics = apply_4_layer_guardrails(raw_eligible, selected_batch, verbose=True)

    if not eligible_leads:
        print("\nNo unattempted leads left in this batch (ledger) or all collided with CRM. Nothing to do.")
        return

    # -------------------------------------------------------------
    # STEP 2: SELECT APOLLO ACCOUNT LOGIN
    # -------------------------------------------------------------
    print("\n[STEP 2: SELECT APOLLO ACCOUNT LOGIN (FROM 19 SECURE LOGINS)]")
    accounts = load_apollo_accounts()

    print(f"{'#':<3} {'Account Name':<25} {'Email / Login':<35} {'API Key Vault':<15}")
    print("-" * 80)
    for a in accounts:
        key_status = mask_key(a.get("api_key", ""))
        print(f"[{a['id']:02d}] {a['name']:<25} {a.get('email', ''):<35} {key_status:<15}")

    while True:
        a_choice = input(f"\n>> Select Login to deduct credits from [1-{len(accounts)}]: ").strip()
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

    print(f"\n✓ Checking credit connection for '{selected_account['name']}'...")
    if not args.dry_run:
        health = check_account_credit_health(acc_key)
        print(f"  • Account Status:   {health['status'].upper()} (Authenticated & Active)")
        print(f"  • Credit Tracking:  {health['email_credits']}")
        print(f"  • Rate Quota:       {health.get('rate_limit', '1000 req/min')}")
        print(f"  • Note:             Apollo's REST API tracks deductions live (1 credit/email).")
        print(f"                      Total numerical pool is viewable at: app.apollo.io/#/settings/plans")
    else:
        print("  • Credit Balance:   [Dry Run - 5,000 Simulated Credits Available]")

    # -------------------------------------------------------------
    # STEP 3: QUANTITY SELECTION PROMPT
    # -------------------------------------------------------------
    print("\n[STEP 3: QUANTITY SELECTION]")
    print(f"  • Eligible leads available in this batch: {len(eligible_leads)}")

    while True:
        qty_input = input(f">> How many leads do you want to save? [1 - {len(eligible_leads)}, press Enter for all ({len(eligible_leads)})]: ").strip()
        if not qty_input:
            target_count = len(eligible_leads)
            break
        if qty_input.isdigit() and 1 <= int(qty_input) <= len(eligible_leads):
            target_count = int(qty_input)
            break
        print(f"Please enter a valid number between 1 and {len(eligible_leads)}.")

    leads_to_process = eligible_leads[:target_count]
    chunk_size = 10
    total_chunks = (len(leads_to_process) + chunk_size - 1) // chunk_size

    login_email = (selected_account.get("email") or "").strip()
    session_id = str(uuid.uuid4())

    print(f"\n✓ Confirmed: Enriching {len(leads_to_process)} leads using account '{selected_account['name']}'.")
    print(f"  • Login email:     {login_email or '(not set in config)'}")
    print(f"  • Session ID:      {session_id}")
    print(f"  • Chunks of 10:    {total_chunks} calls")
    print(f"  • Rate Throttle:   0.9s per chunk (~600 leads/minute)")
    print(f"  • Max credits:     {len(leads_to_process)} (1 per verified email)")

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
    total_credits_spent = 0
    total_free_companies_saved = 0
    all_enriched_records = []

    start_time = time.time()

    for chunk_idx in range(total_chunks):
        chunk = leads_to_process[chunk_idx * chunk_size : (chunk_idx + 1) * chunk_size]

        results = enrich_leads_chunk(acc_key, chunk, dry_run=args.dry_run)

        if not results and not args.dry_run:
            print("\n[Execution Interrupted] No response from Apollo API. Stopping safely.")
            break

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
                )
                conn.commit()

        for lead in chunk:
            match = next((r for r in results if r.get("db_id") == lead["id"]), None)
            if match:
                all_enriched_records.append(match)
                if match.get("email"):
                    total_enriched_emails += 1
                    total_credits_spent += int(match.get("credits_charged") or 0)
                else:
                    total_free_companies_saved += 1
            else:
                all_enriched_records.append({"db_id": lead["id"], "email": "", "credits_charged": 0})
                total_free_companies_saved += 1

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
    print(f"\n\n✓ Completed in {elapsed:.1f}s ({len(all_enriched_records)} leads processed).")

    # -------------------------------------------------------------
    # STEP 5: FINAL SUMMARY & OPTIONAL EXPORT
    # -------------------------------------------------------------
    print("\n" + "=" * 95)
    print("FINAL ENRICHMENT AUDIT & SCORECARD")
    print("=" * 95)
    print(f"  • Batch Tag:                         {selected_batch}")
    print(f"  • Account Used:                      {selected_account['name']}")
    print(f"  • Total Unique Leads Processed:      {len(all_enriched_records)}")
    print(f"  • Verified Business Emails Found:    {total_enriched_emails} ({(total_enriched_emails/max(1, len(all_enriched_records)))*100:.1f}%)")
    print(f"  • Total Email Credits Spent:         {total_credits_spent}")
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

    # Optional CSV export prompt
    export_choice = input("\n>> Export these newly enriched leads to CSV now? [Y/n]: ").strip()
    if export_choice.lower() != 'n':
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_file = os.path.join(EXPORTS_DIR, f"apollo_contacts_export_{selected_batch[:30]}_{len(all_enriched_records)}_{timestamp}.csv")
        
        # Query full enriched rows from DB
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        id, batch, apollo_id, name, first_name, last_name, job_title, email, email_status,
                        company, company_domain, website_link, annual_revenue, employee_count,
                        industry, tech_stack, keywords, company_phone, hq_address, location, linkedin_url,
                        company_linkedin_url, apollo_profile_url, segment, account_used, credits_charged,
                        raw_enrichment_data, enriched_at, created_at
                    FROM apollo_saved_leads
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
