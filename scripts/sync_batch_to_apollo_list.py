#!/usr/bin/env python3
"""
Sync Batch to Apollo "My Lists" Engine
=====================================
Selects any batch from MySQL `apollo_saved_leads` and pushes all leads
directly into Apollo Web under "My lists" (People) at $0.00 cost (0 email credits).

Features:
1. Interactive batch selection from MySQL `apollo_saved_leads`.
2. Automatic List creation in Apollo via API (`POST /v1/labels`).
3. Multi-threaded direct contact sync (`POST /v1/contacts`) with:
   - First Name
   - Last Name
   - Job Title
   - Company Name
   - Company Domain (if verified)
   - LinkedIn URL
   - Label / List Tag
4. Also provides an option to generate 5,000-row split CSVs for Apollo Web CSV Import.
5. ZERO Apollo email credits consumed.
"""

import os
import sys
import json
import time
import requests
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import get_connection

CONFIG_PATH = os.path.join("config", "apollo_accounts.json")
EXPORTS_DIR = os.path.join("dist", "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)


def load_apollo_accounts() -> List[Dict[str, Any]]:
    """Load active Apollo accounts from config."""
    if not os.path.exists(CONFIG_PATH):
        print(f"[!] Error: {CONFIG_PATH} not found.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        accounts = json.load(f)
    active = [
        a for a in accounts
        if a.get("active", True) and a.get("api_key") and not str(a["api_key"]).startswith("YOUR_")
    ]
    return active


def get_available_batches() -> List[Dict[str, Any]]:
    """Query MySQL apollo_saved_leads for all distinct batches and counts."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT `batch`, COUNT(*) as total,
                       SUM(CASE WHEN `email` IS NOT NULL AND `email` != '' THEN 1 ELSE 0 END) as enriched
                FROM `apollo_saved_leads`
                GROUP BY `batch`
                ORDER BY total DESC
            """)
            rows = cur.fetchall()
            return [
                {"batch": r[0] or "unassigned", "total": r[1], "enriched": r[2]}
                for r in rows
            ]


def get_batch_leads(batch_name: str, table_name: str = "apollo_saved_leads") -> List[Dict[str, Any]]:
    """Fetch all leads for a given batch from apollo_saved_leads or enrich_saved_leads."""
    target_table = "enrich_saved_leads" if table_name == "enrich_saved_leads" else "apollo_saved_leads"
    with get_connection() as conn:
        with conn.cursor() as cur:
            if target_table == "enrich_saved_leads":
                cur.execute("""
                    SELECT id, first_name, last_name, name, job_title, company, 
                           company_domain, website_link, linkedin_url, '' as apollo_id
                    FROM `enrich_saved_leads`
                    WHERE `batch` = %s
                """, (batch_name,))
            else:
                cur.execute("""
                    SELECT id, first_name, last_name, name, job_title, company, 
                           company_domain, website_link, linkedin_url, apollo_id
                    FROM `apollo_saved_leads`
                    WHERE `batch` = %s
                """, (batch_name,))
            rows = cur.fetchall()
            leads = []
            for r in rows:
                fn = r[1] or ""
                ln = r[2] or ""
                if not fn and not ln and r[3]:
                    parts = str(r[3]).strip().split(None, 1)
                    fn = parts[0]
                    ln = parts[1] if len(parts) > 1 else ""

                leads.append({
                    "id": r[0],
                    "first_name": fn,
                    "last_name": ln,
                    "name": r[3] or f"{fn} {ln}".strip(),
                    "job_title": r[4] or "",
                    "company": r[5] or "",
                    "company_domain": r[6] or "",
                    "website_link": r[7] or "",
                    "linkedin_url": r[8] or "",
                    "apollo_id": r[9] or "",
                })
            return leads


def create_apollo_label(api_key: str, list_name: str) -> Optional[str]:
    """Create a new list/label in Apollo if it doesn't already exist."""
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key,
    }

    # 1. Check if label already exists
    try:
        resp = requests.get("https://api.apollo.io/v1/labels", headers=headers, params={"modality": "contacts"}, timeout=10)
        if resp.status_code == 200:
            existing = resp.json()
            if isinstance(existing, list):
                for item in existing:
                    if str(item.get("name", "")).strip().lower() == list_name.strip().lower():
                        return item.get("id")
    except Exception:
        pass

    # 2. Create new label
    try:
        payload = {"name": list_name, "modality": "contacts"}
        resp = requests.post("https://api.apollo.io/v1/labels", headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("label", {}).get("id") or data.get("id")
    except Exception as e:
        print(f"[!] Error creating label '{list_name}': {e}")
    return None


def push_single_contact(api_key: str, lead: Dict[str, Any], list_name: str) -> bool:
    """Push one contact into Apollo with the list label."""
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
    }
    payload = {
        "first_name": lead.get("first_name") or "",
        "last_name": lead.get("last_name") or "",
        "title": lead.get("job_title") or "",
        "organization_name": lead.get("company") or "",
        "label_names": [list_name],
    }
    if lead.get("company_domain"):
        payload["corporate_domain"] = lead["company_domain"]
        payload["website_url"] = f"https://{lead['company_domain']}"
    if lead.get("linkedin_url"):
        payload["linkedin_url"] = lead["linkedin_url"]

    try:
        resp = requests.post("https://api.apollo.io/v1/contacts", headers=headers, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def sync_batch_to_apollo_api(api_key: str, leads: List[Dict[str, Any]], list_name: str, max_workers: int = 10):
    """Multi-threaded sync of leads into Apollo under the given list name."""
    total = len(leads)
    print(f"\n[*] Creating/verifying list '{list_name}' in Apollo...")
    label_id = create_apollo_label(api_key, list_name)
    if label_id:
        print(f"[✓] List '{list_name}' confirmed in Apollo! (ID: {label_id})")
    else:
        print(f"[*] Proceeding with list name '{list_name}'...")

    print(f"[*] Pushing {total:,d} contacts into list '{list_name}' with {max_workers} worker threads (0 credits)...")
    success = 0
    failed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_lead = {
            executor.submit(push_single_contact, api_key, lead, list_name): lead
            for lead in leads
        }
        for idx, future in enumerate(as_completed(future_to_lead), 1):
            if future.result():
                success += 1
            else:
                failed += 1

            if idx % 25 == 0 or idx == total:
                elapsed = max(1.0, time.time() - t0)
                rate = idx / elapsed
                pct = (idx / total) * 100
                sys.stdout.write(f"\r  Progress: {idx}/{total} ({pct:.1f}%) | Success: {success:,d} | Failed: {failed:,d} | Rate: {rate:.1f}/s")
                sys.stdout.flush()

    print("\n" + "=" * 80)
    print(f"[✓] Successfully synced {success:,d} contacts into Apollo list '{list_name}'!")
    print(f"    Open Apollo Web -> 'My lists' -> '{list_name}' to see all records!")
    print("=" * 80 + "\n")


def export_split_csvs_for_batch(leads: List[Dict[str, Any]], batch_name: str, chunk_size: int = 5000):
    """Export clean 5,000-row split CSVs for manual or web import."""
    import csv

    total = len(leads)
    chunks = [leads[i:i + chunk_size] for i in range(0, total, chunk_size)]
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    print(f"\n[*] Generating {len(chunks)} split CSVs (max {chunk_size:,d} rows per file)...")
    for i, chunk in enumerate(chunks, 1):
        filename = f"apollo_list_{batch_name}_part{i}_{timestamp}.csv"
        filepath = os.path.join(EXPORTS_DIR, filename)

        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["First Name", "Last Name", "Job Title", "Company Name", "Company Domain", "LinkedIn URL"])
            for l in chunk:
                writer.writerow([
                    l["first_name"],
                    l["last_name"],
                    l["job_title"],
                    l["company"],
                    l["company_domain"],
                    l["linkedin_url"],
                ])
        print(f"  └── [Part {i}/{len(chunks)}] Saved: {filepath} ({len(chunk):,d} rows)")

    print(f"\n[✓] Export complete! In Apollo Web -> Contacts -> Import CSV, upload each file and select the list '{batch_name}'.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync batch from MySQL apollo_saved_leads to Apollo Web 'My Lists'")
    parser.add_argument("--batch", type=str, help="Batch name to sync")
    parser.add_argument("--list", type=str, help="Target list name in Apollo")
    parser.add_argument("--account", type=str, help="Account name or index")
    parser.add_argument("--mode", choices=["api", "csv"], default="", help="Sync mode: 'api' or 'csv'")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent workers for API upload")
    args = parser.parse_args()

    print("=" * 80)
    print("APOLLO LIST SYNC: MySQL `apollo_saved_leads` -> Apollo Web 'My Lists'")
    print("=" * 80)

    # 1. Load accounts
    accounts = load_apollo_accounts()
    if not accounts:
        print("[!] No active Apollo accounts configured in config/apollo_accounts.json.")
        return

    selected_account = accounts[0]
    if args.account:
        for acc in accounts:
            if args.account.lower() in acc.get("name", "").lower():
                selected_account = acc
                break
    elif len(accounts) > 1 and not args.batch:
        print("\nSelect Apollo Account:")
        for idx, acc in enumerate(accounts, 1):
            print(f"  [{idx}] {acc.get('name', 'Default')} ({acc.get('email', '')})")
        choice = input(f"\nSelect Account [1-{len(accounts)}] (default 1): ").strip()
        try:
            acc_idx = int(choice) - 1 if choice else 0
            selected_account = accounts[acc_idx]
        except Exception:
            selected_account = accounts[0]

    api_key = selected_account["api_key"]
    print(f"[✓] Using account: {selected_account.get('name', 'Default')}")

    # 2. Select Batch
    batches = get_available_batches()
    if not batches:
        print("[!] No batches found in `apollo_saved_leads`.")
        return

    selected_batch = args.batch or ""
    if not selected_batch:
        print("\nAvailable Batches in `apollo_saved_leads`:")
        for idx, b in enumerate(batches[:15], 1):
            print(f"  [{idx}] {b['batch']:<35} Total: {b['total']:,d} leads ({b['enriched']:,d} enriched)")

        b_choice = input(f"\nSelect Batch [1-{min(15, len(batches))}] (or type exact batch name): ").strip()
        try:
            b_idx = int(b_choice) - 1
            selected_batch = batches[b_idx]["batch"]
        except Exception:
            selected_batch = b_choice

    if not selected_batch:
        selected_batch = batches[0]["batch"]

    leads = get_batch_leads(selected_batch)
    if not leads:
        print(f"[!] No leads found for batch '{selected_batch}'.")
        return

    print(f"\n[✓] Selected batch '{selected_batch}' ({len(leads):,d} leads).")

    # 3. Choose List Name
    list_name = args.list or ""
    if not list_name:
        list_name_input = input(f"Enter Apollo List Name (default: '{selected_batch}'): ").strip()
        list_name = list_name_input if list_name_input else selected_batch

    # 4. Choose Mode
    mode = args.mode or ""
    if not mode:
        print("\nChoose Delivery Mode:")
        print("  [1] Direct API Stream into Apollo Web 'My lists' (Instant, 0 credits)")
        print("  [2] Export Split CSVs (5,000 rows max each for manual Apollo Web CSV Import)")
        choice = input("Select [1/2] (default 1): ").strip()
        mode = "csv" if choice == "2" else "api"

    if mode == "csv":
        export_split_csvs_for_batch(leads, selected_batch)
    else:
        sync_batch_to_apollo_api(api_key, leads, list_name, max_workers=args.workers)


if __name__ == "__main__":
    main()
