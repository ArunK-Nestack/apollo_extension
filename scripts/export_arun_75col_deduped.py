"""
Export and deduplicate 641 contacts from Apollo list 'arun' (List ID: 6aad8f3e649c1c0020cf79fe)
under account RAHUL@NESTAKTECHNOLOGY.COM into the canonical 75-column Apollo CSV schema.
"""

import os
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import csv
import json
import requests
from typing import List, Dict, Any

from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row

API_KEY = "BAu4z6CXZrmTSWtza807mg"
LIST_ID = "6aad8f3e649c1c0020cf79fe"
ACCOUNT_EMAIL = "RAHUL@NESTAKTECHNOLOGY.COM"
LIST_NAME = "arun"

DOWNLOADS_DIR = r"C:\Users\test\Downloads"
RAW_FILE = os.path.join(DOWNLOADS_DIR, "RAHUL_NESTAKTECHNOLOGY_COM_SEP_ARUN_641_75_cols.csv")
DEDUPED_FILE = os.path.join(DOWNLOADS_DIR, "RAHUL_NESTAKTECHNOLOGY_COM_SEP_ARUN_641_75_cols_Cleaned_Deduplicated.csv")

def fetch_all_contacts() -> List[Dict[str, Any]]:
    url = "https://api.apollo.io/api/v1/contacts/search"
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": API_KEY
    }
    
    contacts = []
    page = 1
    total_pages = 1
    
    print(f"Fetching contacts from Apollo for list '{LIST_NAME}' (ID: {LIST_ID})...")
    while page <= total_pages:
        payload = {
            "label_ids": [LIST_ID],
            "page": page,
            "per_page": 100
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        page_contacts = data.get("contacts", [])
        contacts.extend(page_contacts)
        
        pagination = data.get("pagination", {})
        total_pages = pagination.get("total_pages", page)
        print(f"  Page {page}/{total_pages}: fetched {len(page_contacts)} contacts (total so far: {len(contacts)})")
        page += 1
        
    return contacts

def process_and_export():
    contacts = fetch_all_contacts()
    print(f"\nTotal contacts retrieved: {len(contacts)}")
    
    raw_rows = []
    deduped_rows = []
    seen_emails = set()
    duplicate_records = []
    
    blank_email_records = []
    
    for c in contacts:
        # Ensure list/batch name is tagged
        c["batch"] = LIST_NAME
        
        row = format_apollo_lead_row(c, account_email=ACCOUNT_EMAIL)
        raw_rows.append(row)
        
        # Email is at index 5
        email = (c.get("email") or "").strip().lower()
        if not email:
            email = row[5].strip().lower()
            
        if email:
            if email in seen_emails:
                duplicate_records.append((email, c.get("name"), c.get("organization_name")))
                continue
            seen_emails.add(email)
            deduped_rows.append(row)
        else:
            blank_email_records.append((c.get("name"), c.get("organization_name"), c.get("title")))
            
    print(f"\nDeduplication & Cleaning complete:")
    print(f"  Raw Rows: {len(raw_rows)}")
    print(f"  Blank Emails Excluded from Deduped File: {len(blank_email_records)}")
    for bl in blank_email_records:
        print(f"    - No Email: {bl[0]} ({bl[2]} at {bl[1]})")
    print(f"  Duplicates Removed: {len(duplicate_records)}")
    for dup in duplicate_records:
        print(f"    - Duplicate: {dup[0]} ({dup[1]} at {dup[2]})")
    print(f"  Cleaned & Deduplicated Rows (with unique valid emails): {len(deduped_rows)}")
    
    # Write Raw File (all 641)
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    with open(RAW_FILE, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(APOLLO_75_HEADERS)
        writer.writerows(raw_rows)
    print(f"\nSuccessfully wrote raw file: {RAW_FILE} ({os.path.getsize(RAW_FILE):,} bytes)")
    
    # Write Deduped File (639 rows)
    with open(DEDUPED_FILE, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(APOLLO_75_HEADERS)
        writer.writerows(deduped_rows)
    print(f"Successfully wrote deduped file: {DEDUPED_FILE} ({os.path.getsize(DEDUPED_FILE):,} bytes)")

    # Also write with 639 explicit count in filename
    deduped_639_file = os.path.join(DOWNLOADS_DIR, "RAHUL_NESTAKTECHNOLOGY_COM_SEP_ARUN_639_75_cols_Cleaned_Deduplicated.csv")
    with open(deduped_639_file, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(APOLLO_75_HEADERS)
        writer.writerows(deduped_rows)
    print(f"Successfully wrote explicit 639 deduped file: {deduped_639_file} ({os.path.getsize(deduped_639_file):,} bytes)")
    
    # Compute fill rates on deduped rows
    total_deduped = len(deduped_rows)
    fill_counts = [0] * len(APOLLO_75_HEADERS)
    for r in deduped_rows:
        for idx, val in enumerate(r):
            if val and val != "false":
                fill_counts[idx] += 1
                
    print("\nKey Field Fill Rates (Cleaned & Deduplicated):")
    key_fields = [
        "First Name", "Last Name", "Title", "Company Name", "Company Name for Emails",
        "Email", "Email Status", "Contact Owner", "Account Owner", "Corporate Phone",
        "Work Direct Phone", "Mobile Phone", "Industry", "# Employees", "Keywords",
        "Person Linkedin Url", "Website", "Company Linkedin Url", "City", "State", "Country",
        "Technologies", "Apollo Contact Id"
    ]
    
    for kf in key_fields:
        if kf in APOLLO_75_HEADERS:
            idx = APOLLO_75_HEADERS.index(kf)
            cnt = fill_counts[idx]
            pct = (cnt / total_deduped) * 100 if total_deduped else 0
            print(f"  {kf:<35}: {cnt:>4}/{total_deduped} ({pct:>5.1f}%)")

if __name__ == "__main__":
    process_and_export()
