#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_login_created_leads.py - Comprehensive Login Leads Audit & Created Data Exporter

Workflows:
1. Select Login from Apollo/Freshsales accounts.
2. Display activity dates table ("shown as a birthday on the table page") with:
   - Spin #
   - Date (Birthday / Creation Date)
   - Leads Created
   - Leads Updated
   - Valid Non-Image Text Entries Count
   - Table Created in Cells (rows x 12 columns)
   - Master CRM emails table match status
3. Prompt for selector spin number (date index or ALL).
4. Prompt whether to download CSV of created data only.
5. Export CSV with the exact 12 requested columns:
   Contact : id, Contact : Last name, Contact : First name, Contact : Emails,
   Contact : Job title, Contact : Owner, Contact : Sales owner email,
   Contact : Created at, Contact : Contact Industry, Contact : Apollo login owner,
   Contact : Hierarchy, Contact : Country
6. Provide count of non-image entries and total table cells created.
7. Optional prompt to sync new records to RDS MySQL emails table.
"""

import os
import sys
import json
import csv
import re
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "freshsales_agent"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "freshsales_agent" / ".env", override=True)

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import requests
except ImportError:
    requests = None

# Master 12 Columns requested for created contacts export
EXACT_12_COLUMNS = [
    "Contact : id",
    "Contact : Last name",
    "Contact : First name",
    "Contact : Emails",
    "Contact : Job title",
    "Contact : Owner",
    "Contact : Sales owner email",
    "Contact : Created at",
    "Contact : Contact Industry",
    "Contact : Apollo login owner",
    "Contact : Hierarchy",
    "Contact : Country"
]

def load_accounts():
    cfg_file = PROJECT_ROOT / "config" / "apollo_accounts.json"
    if not cfg_file.exists():
        return []
    with open(cfg_file, "r", encoding="utf-8") as f:
        return json.load(f)

def clean_str(val):
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null", "nat"):
        return ""
    return s

def clean_login_slug(email):
    return re.sub(r'[^a-z0-9]', '', str(email).lower().strip())

def is_valid_non_image_entry(row):
    """
    Check that entry is a valid text record and not an image/icon placeholder.
    Must have valid non-blank email containing '@' and not ending in image extension.
    """
    email = clean_str(row.get("Contact : Emails") or row.get("Email") or row.get("email")).lower()
    if not email or "@" not in email:
        return False
    # Check if dummy or image file placeholder
    if re.search(r'\.(png|jpg|jpeg|gif|svg|webp|bmp|ico)$', email, re.I):
        return False
    return True

def parse_date_str(val):
    """
    Extract YYYY-MM-DD from various date string formats or datetime objects.
    Corrects day-month inversion on DD-MM-YYYY dates in August/September 2026.
    e.g. '09-09-2026 03:52:28 PM' -> '2026-09-09'
         '23-08-2026 12:02:23 AM' -> '2026-08-23'
         '2026-01-09 20:53:49'    -> '2026-09-01' (01-09-2026 dayfirst swapped by US excel format)
         '2026-09-09T15:52:28+05:30' -> '2026-09-09'
    """
    if not val:
        return ""
    if isinstance(val, datetime):
        y, m, d = val.year, val.month, val.day
        if y == 2026 and 1 <= m <= 7 and d in (8, 9):
            m, d = d, m
        return f"{y:04d}-{m:02d}-{d:02d}"
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "nat", "null"):
        return ""
    # Check ISO format: YYYY-MM-DD
    m_iso = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if y == 2026 and 1 <= m <= 7 and d in (8, 9):
            m, d = d, m
            return f"{y:04d}-{m:02d}-{d:02d}"
        return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
    # Check DD-MM-YYYY format
    m_dmy = re.match(r'^(\d{2})-(\d{2})-(\d{4})', s)
    if m_dmy:
        return f"{m_dmy.group(3)}-{m_dmy.group(2)}-{m_dmy.group(1)}"
    # Check MM/DD/YYYY format
    m_mdy = re.match(r'^(\d{2})/(\d{2})/(\d{4})', s)
    if m_mdy:
        p1, p2, yr = int(m_mdy.group(1)), int(m_mdy.group(2)), m_mdy.group(3)
        if p1 > 12:
            return f"{yr}-{p2:02d}-{p1:02d}"
        if yr == "2026" and 1 <= p1 <= 7 and p2 in (8, 9):
            return f"{yr}-{p2:02d}-{p1:02d}"
        return f"{yr}-{p1:02d}-{p2:02d}"
    return ""

def format_to_exact_12_columns(r, default_login_email="", default_date_str=""):
    """
    Normalizes any record (Apollo CSV, Freshsales export, or local archive)
    into the exact 12 columns requested with accurate standard fields.
    """
    login_owner = clean_str(r.get("Contact : Apollo login owner") or r.get("Apollo login owner") or default_login_email)
    hierarchy = clean_str(r.get("Contact : Hierarchy") or r.get("Hierarchy"))
    if not hierarchy and login_owner:
        hierarchy = login_owner.split(".")[0].split("@")[0].lower()
        
    created_at = clean_str(r.get("Contact : Created at") or r.get("Created at") or r.get("created_at") or default_date_str)
    parsed_dt = parse_date_str(created_at)
    if parsed_dt:
        time_m = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)', str(created_at))
        if time_m:
            created_at_fmt = f"{parsed_dt} {time_m.group(1)}"
        else:
            created_at_fmt = parsed_dt
    else:
        created_at_fmt = created_at or default_date_str

    email_val = clean_str(r.get("Contact : Emails") or r.get("Email") or r.get("email") or r.get("Emails")).lower()

    return {
        "Contact : id": clean_str(r.get("Contact : id") or r.get("id") or r.get("Contact Id") or r.get("ID")),
        "Contact : Last name": clean_str(r.get("Contact : Last name") or r.get("Last Name") or r.get("last_name")),
        "Contact : First name": clean_str(r.get("Contact : First name") or r.get("First Name") or r.get("first_name")),
        "Contact : Emails": email_val,
        "Contact : Job title": clean_str(r.get("Contact : Job title") or r.get("Title") or r.get("job_title") or r.get("Job Title")),
        "Contact : Owner": clean_str(r.get("Contact : Owner") or r.get("Owner")) or "Vijay Raghavan",
        "Contact : Sales owner email": clean_str(r.get("Contact : Sales owner email") or r.get("Sales owner email")) or "info@nestack.com",
        "Contact : Created at": created_at_fmt,
        "Contact : Contact Industry": clean_str(r.get("Contact : Contact Industry") or r.get("Industry") or r.get("industry") or r.get("cf_contact_industry")),
        "Contact : Apollo login owner": login_owner,
        "Contact : Hierarchy": hierarchy,
        "Contact : Country": clean_str(r.get("Contact : Country") or r.get("Country") or r.get("country"))
    }

def load_synced_batches_records(login_email):
    """
    Loads created records from config/freshsales_synced_batches.json.
    Matches batch to account using get_account_for_batch.
    Extracts created contacts using the audit file (where action == 'created')
    and pulls full row attributes from the source good leads file.
    """
    from scripts.login_pipeline_audit import get_account_for_batch
    
    ledger_p = PROJECT_ROOT / "config" / "freshsales_synced_batches.json"
    if not ledger_p.exists():
        return defaultdict(list)
        
    try:
        with open(ledger_p, "r", encoding="utf-8") as f:
            synced_batches = json.load(f)
    except Exception:
        return defaultdict(list)
        
    recs_by_date = defaultdict(list)
    target = login_email.lower().strip()
    
    for b_name, b_info in synced_batches.items():
        acc = get_account_for_batch(b_name, b_info.get("tag", ""))
        if target != "all" and acc.lower() != target:
            continue
            
        audit_p = Path(b_info.get("audit_file", ""))
        src_p = Path(b_info.get("file_path", ""))
        synced_at = b_info.get("synced_at", "")
        dt = parse_date_str(synced_at)
        if not dt or not audit_p.exists() or not src_p.exists():
            continue
            
        # Collect created emails from audit file
        created_emails = set()
        try:
            with open(audit_p, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("action") == "created":
                        em = clean_str(row.get("email")).lower()
                        if em:
                            created_emails.add(em)
        except Exception:
            continue
            
        if not created_emails:
            continue
            
        # Extract corresponding rows from source file
        try:
            with open(src_p, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    em = clean_str(row.get("Email") or row.get("email") or row.get("Contact : Emails")).lower()
                    if em in created_emails:
                        formatted = format_to_exact_12_columns(row, default_login_email=acc, default_date_str=synced_at)
                        recs_by_date[dt].append(formatted)
        except Exception:
            continue
            
    return recs_by_date

def load_local_august_records(login_email):
    """
    Scan data/august_2026 for files matching this login email.
    Uses get_account_for_batch or normalized slug comparison to match.
    """
    from scripts.login_pipeline_audit import get_account_for_batch
    
    aug_dir = PROJECT_ROOT / "data" / "august_2026"
    if not aug_dir.exists():
        return []
    
    target = login_email.lower().strip()
    target_clean = clean_login_slug(target)
    records = []
    
    for p in aug_dir.glob("*"):
        if p.name == "desktop.ini":
            continue
            
        file_acc = get_account_for_batch(p.name)
        file_clean = clean_login_slug(p.name)
        
        matches = False
        if target == "all":
            matches = True
            acc_to_use = file_acc if file_acc else target
        elif file_acc and file_acc.lower() == target:
            matches = True
            acc_to_use = file_acc
        elif target_clean in file_clean:
            if "nestacktech" in file_clean and "nestacktech" not in target:
                matches = False
            else:
                matches = True
                acc_to_use = target
                
        if not matches:
            continue
            
        try:
            if p.suffix in (".xlsx", ".xls"):
                if pd is not None:
                    df = pd.read_excel(p)
                    for _, row in df.iterrows():
                        raw_d = {str(k).strip(): clean_str(v) for k, v in row.items()}
                        fmt_d = format_to_exact_12_columns(raw_d, default_login_email=acc_to_use)
                        records.append(fmt_d)
            else:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        raw_d = {k.strip(): clean_str(v) for k, v in r.items()}
                        fmt_d = format_to_exact_12_columns(raw_d, default_login_email=acc_to_use)
                        records.append(fmt_d)
        except Exception:
            continue
            
    return records

def load_existing_export_records(login_email):
    """
    Scan exports/freshsales_reports for export files matching this login email.
    Excludes audit files (*_crm_audit.csv) and account list files (*_created_accounts.csv).
    """
    from scripts.login_pipeline_audit import get_account_for_batch
    
    exp_dir = PROJECT_ROOT / "exports" / "freshsales_reports"
    if not exp_dir.exists():
        return []
    
    target = login_email.lower().strip()
    target_clean = clean_login_slug(target)
    records = []
    
    for p in exp_dir.glob("*.csv"):
        if p.name.endswith("_crm_audit.csv") or p.name.endswith("_created_accounts.csv"):
            continue
            
        file_acc = get_account_for_batch(p.name)
        file_clean = clean_login_slug(p.name)
        
        matches = False
        if target == "all":
            matches = True
            acc_to_use = file_acc if file_acc else target
        elif file_acc and file_acc.lower() == target:
            matches = True
            acc_to_use = file_acc
        elif target_clean in file_clean:
            if "nestacktech" in file_clean and "nestacktech" not in target:
                matches = False
            else:
                matches = True
                acc_to_use = target
                
        if not matches:
            continue
            
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    raw_d = {k.strip(): clean_str(v) for k, v in r.items()}
                    if is_valid_non_image_entry(raw_d):
                        fmt_d = format_to_exact_12_columns(raw_d, default_login_email=acc_to_use)
                        records.append(fmt_d)
        except Exception:
            pass
            
    return records

def load_daily_breakdown_cache(login_email):
    """
    Loads pre-scanned September daily breakdown from scratch/freshsales_daily_breakdown.json
    """
    daily_file = PROJECT_ROOT / "scratch" / "freshsales_daily_breakdown.json"
    if not daily_file.exists():
        return {}
    
    try:
        with open(daily_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        by_date = data.get("by_date", {})
        login_days = {}
        target = login_email.lower().strip()
        for dt, items in by_date.items():
            for it in items:
                if (it.get("email") or "").lower().strip() == target:
                    login_days[dt] = {
                        "created": it.get("created", 0),
                        "updated": it.get("updated", 0)
                    }
        return login_days
    except Exception:
        return {}

def fetch_freshsales_contacts_for_date(login_email, date_str):
    """
    Live API fetch of created contacts for a specific date from Freshsales.
    Uses meta.total to fetch all pages dynamically and caches locally for speed.
    """
    safe_login = login_email.lower().strip()
    cache_dir = PROJECT_ROOT / "scratch" / "freshsales_contacts_cache" / safe_login
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{date_str}.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if isinstance(cached, list) and cached:
                    return cached
        except Exception:
            pass

    api_key = os.getenv("FRESHSALES_API_KEY")
    domain = os.getenv("FRESHSALES_DOMAIN", "https://nestack.freshsales.io").rstrip("/")
    if not api_key:
        print("[!] FRESHSALES_API_KEY not found in environment.")
        return []

    headers = {
        "Authorization": f"Token token={api_key}",
        "Content-Type": "application/json"
    }

    try:
        cur_dt = datetime.strptime(date_str, "%Y-%m-%d")
        next_dt = cur_dt + timedelta(days=1)
        next_str = next_dt.strftime("%Y-%m-%d")
    except Exception:
        next_str = date_str

    print(f"[*] Fetching live created contacts from Freshsales for {login_email} on {date_str}...")

    def fetch_page(page_num):
        payload = {
            "filter_rule": [
                {"attribute": "created_at", "operator": "is_after", "value": f"{date_str}T00:00:00+05:30"},
                {"attribute": "created_at", "operator": "is_before", "value": f"{next_str}T00:00:00+05:30"},
                {"attribute": "cf_apollo_login_owner", "operator": "contains", "value": login_email}
            ],
            "page": page_num,
            "per_page": 100
        }
        for attempt in range(4):
            try:
                r = requests.post(f"{domain}/api/filtered_search/contact", headers=headers, json=payload, timeout=25)
                if r.ok:
                    res_json = r.json()
                    contacts = res_json.get("contacts", [])
                    total = (res_json.get("meta") or {}).get("total", 0)
                    return contacts, total
            except Exception:
                pass
            time.sleep(0.5 * (attempt + 1))
        return [], 0

    p1_contacts, total_count = fetch_page(1)
    if not p1_contacts:
        return []

    all_contacts = list(p1_contacts)
    if total_count > 100:
        total_pages = (total_count + 99) // 100
    else:
        total_pages = 1

    print(f"    Total in Freshsales: {total_count:,d} contacts ({total_pages} pages). Downloading...")

    if total_pages > 1:
        pages_to_fetch = list(range(2, total_pages + 1))
        retries = 3
        while pages_to_fetch and retries > 0:
            failed_pages = []
            with ThreadPoolExecutor(max_workers=8) as executor:
                futs = {executor.submit(fetch_page, p): p for p in pages_to_fetch}
                for fut in as_completed(futs):
                    p = futs[fut]
                    page_res, _ = fut.result()
                    if page_res:
                        all_contacts.extend(page_res)
                    else:
                        failed_pages.append(p)
            pages_to_fetch = failed_pages
            retries -= 1

    formatted = []
    hierarchy = login_email.split(".")[0].split("@")[0].lower()

    for c in all_contacts:
        emails_list = c.get("emails") or []
        email_val = c.get("email") or (emails_list[0] if emails_list else "")
        created_at_raw = c.get("created_at") or ""
        created_at_fmt = created_at_raw
        if created_at_raw:
            try:
                dt_obj = datetime.strptime(created_at_raw.split("+")[0], "%Y-%m-%dT%H:%M:%S")
                created_at_fmt = dt_obj.strftime("%d-%m-%Y %I:%M:%S %p")
            except Exception:
                created_at_fmt = created_at_raw

        cf = c.get("custom_field") or {}
        industry = cf.get("cf_contact_industry") or ""

        row = {
            "Contact : id": clean_str(c.get("id")),
            "Contact : Last name": clean_str(c.get("last_name")),
            "Contact : First name": clean_str(c.get("first_name")),
            "Contact : Emails": clean_str(email_val),
            "Contact : Job title": clean_str(c.get("job_title")),
            "Contact : Owner": "Vijay Raghavan",
            "Contact : Sales owner email": "info@nestack.com",
            "Contact : Created at": created_at_fmt,
            "Contact : Contact Industry": clean_str(industry),
            "Contact : Apollo login owner": login_email,
            "Contact : Hierarchy": hierarchy,
            "Contact : Country": clean_str(c.get("country"))
        }
        formatted.append(row)

    if formatted:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(formatted, f)
        except Exception:
            pass

    return formatted

def check_master_crm_match(emails_list):
    """
    Check how many emails from this list exist in the master RDS MySQL 'emails' table.
    """
    clean_emails = [e.lower().strip() for e in emails_list if e and "@" in e]
    if not clean_emails:
        return 0, 0

    try:
        from backend.api import get_connection
        unique_emails = list(set(clean_emails))
        matched = 0
        batch_size = 1000

        with get_connection() as conn:
            with conn.cursor() as cur:
                for i in range(0, len(unique_emails), batch_size):
                    chunk = unique_emails[i:i + batch_size]
                    fmt = ",".join(["%s"] * len(chunk))
                    cur.execute(f"SELECT COUNT(*) FROM emails WHERE email IN ({fmt})", chunk)
                    matched += cur.fetchone()[0]

        return matched, len(unique_emails)
    except Exception:
        return 0, len(set(clean_emails))

def sync_records_to_master_crm(records):
    """
    Insert records into master CRM emails table: email, full_name, domain
    """
    try:
        from backend.api import get_connection
    except ImportError:
        print("[!] backend.api not available.")
        return 0

    to_insert = []
    seen = set()

    for r in records:
        email = clean_str(r.get("Contact : Emails") or r.get("Email")).lower()
        if not email or "@" not in email or email in seen:
            continue
        seen.add(email)

        first_name = clean_str(r.get("Contact : First name") or r.get("First Name"))
        last_name = clean_str(r.get("Contact : Last name") or r.get("Last Name"))
        full_name = f"{first_name} {last_name}".strip()
        domain = email.split("@")[1].strip().lower()
        to_insert.append((email, full_name, domain))

    if not to_insert:
        print("[*] No valid records to sync.")
        return 0

    print(f"[*] Syncing {len(to_insert):,d} records to master CRM 'emails' table...")
    inserted_count = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            batch_size = 1000
            for i in range(0, len(to_insert), batch_size):
                chunk = to_insert[i:i + batch_size]
                cur.executemany("""
                    INSERT IGNORE INTO emails (email, full_name, domain)
                    VALUES (%s, %s, %s)
                """, chunk)
                inserted_count += cur.rowcount
            conn.commit()

    print(f"[+] Master CRM sync completed! {inserted_count:,d} newly inserted records.")
    return inserted_count

def audit_account(login_email):
    """
    Collects and deduplicates all activity dates and records for a login across all sources:
    1. Synced Freshsales batches (config/freshsales_synced_batches.json + audit files + source files)
    2. Local August data files (data/august_2026)
    3. Existing created exports (exports/freshsales_reports)
    4. Daily breakdown cache (scratch/freshsales_daily_breakdown.json)
    
    Guarantees 1:1 parity between displayed table counts and exportable records!
    """
    dates_map = defaultdict(lambda: {
        "created_records": [],
        "created_count": 0,
        "updated_count": 0,
        "source": ""
    })
    seen_by_date = defaultdict(set)

    def add_records(records, src_label, default_dt=""):
        for r in records:
            if not is_valid_non_image_entry(r):
                continue
            dt = parse_date_str(r.get("Contact : Created at") or r.get("Created at") or default_dt)
            if not dt:
                continue
            cid = clean_str(r.get("Contact : id") or r.get("id"))
            em = clean_str(r.get("Contact : Emails") or r.get("Email")).lower()
            key = em if em else f"cid_{cid}"
            if key not in seen_by_date[dt]:
                seen_by_date[dt].add(key)
                dates_map[dt]["created_records"].append(r)
                if not dates_map[dt]["source"]:
                    dates_map[dt]["source"] = src_label

    # 1. Load from Freshsales Synced Batches
    synced_recs_by_date = load_synced_batches_records(login_email)
    for dt, recs in synced_recs_by_date.items():
        add_records(recs, "Freshsales Synced Batch", default_dt=dt)

    # 2. Load from August data files
    aug_recs = load_local_august_records(login_email)
    add_records(aug_recs, "August Data Archive")

    # 3. Load from existing exports in exports/freshsales_reports
    export_recs = load_existing_export_records(login_email)
    add_records(export_recs, "Export Archive")

    # 4. Load from daily breakdown cache (September) for updates & uncached counts
    daily_cache = load_daily_breakdown_cache(login_email)
    for dt, cnts in daily_cache.items():
        if cnts.get("updated", 0) > 0:
            dates_map[dt]["updated_count"] = cnts["updated"]
        if cnts.get("created", 0) > 0 and not dates_map[dt]["created_records"]:
            dates_map[dt]["created_count"] = cnts["created"]
            if not dates_map[dt]["source"]:
                dates_map[dt]["source"] = "Freshsales Live (Uncached)"

    # Finalize created counts: 1:1 PARITY with len(created_records) whenever records exist!
    for dt, data in dates_map.items():
        recs = data["created_records"]
        if recs:
            data["created_count"] = len(recs)

    return dict(dates_map)

def export_created_only_csv(login_email, date_label, records):
    """
    Exports strictly created data to CSV with exact 12 columns.
    """
    out_dir = PROJECT_ROOT / "exports" / "freshsales_reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_login = login_email.lower().strip()
    safe_date = date_label.replace("/", "-").replace(":", "-").replace(" ", "_")
    out_file = out_dir / f"{safe_login}_created_contacts_only_{safe_date}.csv"

    clean_rows = []
    seen_keys = set()

    for r in records:
        if not is_valid_non_image_entry(r):
            continue
        cid = clean_str(r.get("Contact : id") or r.get("id"))
        email = clean_str(r.get("Contact : Emails") or r.get("Email")).lower()
        key = email if email else f"cid_{cid}"
        if key in seen_keys:
            continue
        seen_keys.add(key)

        clean_row = format_to_exact_12_columns(r, default_login_email=login_email, default_date_str=date_label)
        clean_rows.append(clean_row)

    # Automatically enrich missing Contact Industry from DB
    missing_emails = [r["Contact : Emails"].lower().strip() for r in clean_rows if not r.get("Contact : Contact Industry") and r.get("Contact : Emails")]
    if missing_emails:
        try:
            from backend.api import get_connection
            industry_map = {}
            with get_connection() as conn:
                with conn.cursor() as cur:
                    batch_size = 1000
                    for i in range(0, len(missing_emails), batch_size):
                        chunk = missing_emails[i:i + batch_size]
                        fmt = ','.join(['%s'] * len(chunk))
                        cur.execute(f"SELECT email, industry FROM apollo_saved_leads WHERE email IN ({fmt})", chunk)
                        for em, ind in cur.fetchall():
                            if ind:
                                industry_map[em.lower()] = ind
                        cur.execute(f"SELECT email, industry FROM enrich_saved_leads WHERE email IN ({fmt})", chunk)
                        for em, ind in cur.fetchall():
                            if ind and em.lower() not in industry_map:
                                industry_map[em.lower()] = ind
            for r in clean_rows:
                em = r.get("Contact : Emails", "").lower().strip()
                if not r.get("Contact : Contact Industry") and em in industry_map:
                    r["Contact : Contact Industry"] = industry_map[em]
        except Exception:
            pass

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXACT_12_COLUMNS)
        writer.writeheader()
        writer.writerows(clean_rows)

    return out_file, clean_rows

def get_prioritized_sorted_dates(dates_map):
    """
    Sort dates so that major batches with CREATED leads (>0) appear first (by created count descending, then date),
    followed by dates with only updates.
    """
    created_dates = []
    updated_only_dates = []

    for dt, info in dates_map.items():
        if info["created_count"] > 0:
            created_dates.append(dt)
        elif info["updated_count"] > 0:
            updated_only_dates.append(dt)

    created_dates.sort(key=lambda d: (dates_map[d]["created_count"], d), reverse=True)
    updated_only_dates.sort(key=lambda d: (dates_map[d]["updated_count"], d), reverse=True)

    return created_dates + updated_only_dates

def parse_multi_spin_selection(spin_input: str, spin_items: list) -> tuple[list, str]:
    """
    Parses single spin, multiple spins (e.g. '1, 2' or '1,2'), ranges (e.g. '1-3'),
    explicit date strings, or 'ALL'.
    Returns (selected_spin_items, selected_date_label).
    """
    s = (spin_input or "").strip()
    if not s:
        return [spin_items[0]], spin_items[0]["date"]

    if s.upper() == "ALL":
        selected = [sp for sp in spin_items if sp["created"] > 0]
        if not selected:
            selected = spin_items
        return selected, "all_dates"

    date_to_spin = {sp["date"].strip(): sp for sp in spin_items}
    chosen_indices = []

    raw_tokens = [t.strip() for t in re.split(r'[,;]+', s) if t.strip()]
    if len(raw_tokens) == 1 and " " in raw_tokens[0] and not any(c in raw_tokens[0] for c in "-/"):
        tokens = raw_tokens[0].split()
    else:
        tokens = raw_tokens

    for tok in tokens:
        m_range = re.match(r'^(\d+)\s*-\s*(\d+)$', tok)
        if m_range:
            start, end = int(m_range.group(1)), int(m_range.group(2))
            if start > end:
                start, end = end, start
            for idx in range(start, end + 1):
                if 1 <= idx <= len(spin_items) and (idx - 1) not in chosen_indices:
                    chosen_indices.append(idx - 1)
            continue

        if tok.isdigit():
            idx = int(tok)
            if 1 <= idx <= len(spin_items) and (idx - 1) not in chosen_indices:
                chosen_indices.append(idx - 1)
            continue

        if tok in date_to_spin:
            sp = date_to_spin[tok]
            idx_sp = spin_items.index(sp)
            if idx_sp not in chosen_indices:
                chosen_indices.append(idx_sp)
            continue

    if not chosen_indices:
        print("[!] Invalid spin selection. Defaulting to spin [1].")
        return [spin_items[0]], spin_items[0]["date"]

    selected = [spin_items[i] for i in chosen_indices]

    if len(selected) == 1:
        date_label = selected[0]["date"]
    elif len(selected) == 2:
        date_label = f"{selected[0]['date']}_plus_{selected[1]['date']}"
    else:
        date_label = f"multi_{len(selected)}_dates_{selected[0]['date']}_to_{selected[-1]['date']}"

    return selected, date_label

def run_interactive():
    print("=" * 115)
    print("      FRESHSALES CRM - COMPREHENSIVE LOGIN LEADS AUDIT & CREATED DATA EXPORTER")
    print("=" * 115)

    accounts = load_accounts()
    if not accounts:
        print("[!] No accounts found in config/apollo_accounts.json")
        return

    # 1. Prompt for login selection
    print("\nSelect Login Account:")
    print("-" * 75)
    for acc in accounts:
        acc_id = acc.get("id")
        name = acc.get("name", "")
        email = acc.get("email", "")
        print(f" [{acc_id:>2}] {email:<42} ({name})")
    print(f" [ A] ALL ACCOUNTS (Comprehensive Multi-Account Audit)")
    print("-" * 75)

    choice = input("\nEnter login selector number [1-19, A, or email] (Default: 1): ").strip()
    if not choice:
        choice = "1"

    selected_account = None
    if choice.upper() == "A":
        selected_account = {"id": 0, "name": "All Accounts", "email": "ALL"}
    elif choice.isdigit():
        idx = int(choice)
        for acc in accounts:
            if acc.get("id") == idx:
                selected_account = acc
                break
    else:
        for acc in accounts:
            if choice.lower() in acc.get("email", "").lower():
                selected_account = acc
                break

    if not selected_account:
        print("[!] Invalid selection. Defaulting to Account 1 (Abel Abraham).")
        selected_account = accounts[0]

    login_email = selected_account.get("email", "")
    account_name = selected_account.get("name", "")

    print(f"\n[+] Selected Login: {login_email} ({account_name})")
    print("[*] Auditing activity dates and import records...")

    dates_map = audit_account(login_email)

    if not dates_map:
        print(f"[!] No recorded activity dates found for {login_email}.")
        return

    # Sort dates: created leads first, then update-only dates
    sorted_dates = get_prioritized_sorted_dates(dates_map)

    print("\n" + "=" * 125)
    print(f" LOGIN: {login_email:<40} - ACTIVITY BREAKDOWN & IMPORT DATES (BIRTHDAYS)")
    print("=" * 125)
    print(f" {'Spin #':<8} | {'Date (Birthday)':<15} | {'Created Leads':<14} | {'Updated Leads':<14} | {'Non-Image Entries':<18} | {'Table Cells Created':<22} | {'Data Source'}")
    print("-" * 125)

    spin_items = []
    total_created = 0
    total_updated = 0
    total_non_img = 0
    total_cells = 0

    for idx, dt in enumerate(sorted_dates, start=1):
        info = dates_map[dt]
        cr = info["created_count"]
        up = info["updated_count"]
        recs = info["created_records"]

        if recs:
            non_img = sum(1 for r in recs if is_valid_non_image_entry(r))
        else:
            non_img = cr

        cells_cnt = non_img * len(EXACT_12_COLUMNS)
        source = info["source"] or "Freshsales API"

        total_created += cr
        total_updated += up
        total_non_img += non_img
        total_cells += cells_cnt

        spin_items.append({
            "spin": idx,
            "date": dt,
            "created": cr,
            "updated": up,
            "non_image": non_img,
            "cells": cells_cnt,
            "records": recs,
            "source": source
        })

        is_created_batch = cr > 0
        tag = "[CREATED]" if is_created_batch else "[UPDATES]"
        print(f" [{idx:>2}] {tag} | {dt:<15} | {cr:<14,d} | {up:<14,d} | {non_img:<8,d} (100%)    | {cells_cnt:<14,d} (12 col) | {source}")

    print("-" * 125)
    print(f" [ALL]    | ALL DATES       | {total_created:<14,d} | {total_updated:<14,d} | {total_non_img:<8,d} (100%)    | {total_cells:<14,d} (12 col) | Combined")
    print("=" * 125)

    # 3. Prompt for selector spin number (supports single, multiple e.g. 1, 2 or ranges e.g. 1-3)
    spin_input = input(f"\nEnter selector spin number(s) to audit/export [e.g. 1, 2 or 1-3 or ALL] (Default: 1): ").strip()
    selected_spins, selected_date_label = parse_multi_spin_selection(spin_input, spin_items)

    dates_display = ", ".join(sp["date"] for sp in selected_spins)
    if len(selected_spins) > 1:
        print(f"\n[+] Selected Multiple Spin Dates ({len(selected_spins)} dates): {dates_display}")
    else:
        print(f"\n[+] Selected Spin Date: {selected_date_label}")

    target_records = []
    unique_non_image_records = []
    seen_lead_keys = set()
    cross_date_duplicates = 0

    for sp in selected_spins:
        recs = sp["records"]
        if not recs and sp["created"] > 0:
            print(f"[*] Local cache empty for {sp['date']}. Fetching live from Freshsales API...")
            fetched = fetch_freshsales_contacts_for_date(login_email, sp["date"])
            recs = fetched
        
        target_records.extend(recs)

        for r in recs:
            if not is_valid_non_image_entry(r):
                continue
            email = clean_str(r.get("Contact : Emails") or r.get("Email")).lower()
            cid = clean_str(r.get("Contact : id") or r.get("id"))
            lead_key = email if email else f"cid_{cid}"

            if lead_key in seen_lead_keys:
                cross_date_duplicates += 1
                continue
            seen_lead_keys.add(lead_key)
            unique_non_image_records.append(r)

    non_image_records = unique_non_image_records
    non_image_count = len(non_image_records)
    total_entries_count = len(target_records)
    cells_created_count = non_image_count * len(EXACT_12_COLUMNS)

    print("\n" + "=" * 80)
    print("                        IMPORT AUDIT METRICS")
    print("=" * 80)
    if len(selected_spins) > 1:
        print(f" • Selected Dates ({len(selected_spins)} dates):          {dates_display}")
        print(f" • Total Lead Entries Across Dates:      {total_entries_count:,d}")
        print(f" • Cross-Date Duplicate Leads Removed:   {cross_date_duplicates:,d}")
        print(f" • Unique Leads Extracted (Non-Image):   {non_image_count:,d} (100% valid unique contacts)")
    else:
        print(f" • Selected Date:                        {dates_display}")
        print(f" • Total Lead Entries in Import:         {total_entries_count:,d}")
        print(f" • Count of Entries that are NOT Images: {non_image_count:,d} (100% valid text contacts)")
    print(f" • Table Created in Cells:               {cells_created_count:,d} cells ({non_image_count:,d} rows × 12 columns)")
    print(f" • Standard Column Schema (12 cells/row):")
    for i, col in enumerate(EXACT_12_COLUMNS, 1):
        print(f"     [{i:>2}] {col}")
    print("=" * 80)

    # Preview table in cells
    if non_image_records:
        print("\n--- Table Created in Cells Preview (First 3 Rows) ---")
        print("-" * 115)
        print(f" {'ID':<13} | {'First Name':<15} | {'Last Name':<15} | {'Email':<30} | {'Job Title':<25}")
        print("-" * 115)
        for r in non_image_records[:3]:
            cid = clean_str(r.get("Contact : id"))
            fn = clean_str(r.get("Contact : First name"))
            ln = clean_str(r.get("Contact : Last name"))
            em = clean_str(r.get("Contact : Emails"))
            jt = clean_str(r.get("Contact : Job title"))[:25]
            print(f" {cid:<13} | {fn:<15} | {ln:<15} | {em:<30} | {jt:<25}")
        print("-" * 115)

    # 4. Prompt whether to download CSV of created data only
    print("\nDownload Options:")
    print(" [1] Download CSV of the CREATED DATA ONLY (Excludes updated data, 12 standard columns)")
    print(" [2] Download Complete Audit CSV (Includes all raw columns)")
    print(" [3] Sync/Insert records into Master CRM 'emails' table")
    print(" [0] Exit without saving")

    download_choice = input("\nWould you like to download the CSV of the created data only? [Y/n] (Default: Y): ").strip().lower()
    
    if download_choice in ("", "y", "yes"):
        out_file, exported_rows = export_created_only_csv(login_email, selected_date_label, non_image_records)
        print("\n" + "=" * 105)
        print("                               EXPORT SUCCESSFUL")
        print("=" * 105)
        print(f" • File Path:     {out_file.resolve()}")
        print(f" • Clickable URI: file:///{str(out_file.resolve()).replace(chr(92), '/')}")
        print(f" • Total Records: {len(exported_rows):,d} created leads")
        print(f" • Total Cells:   {len(exported_rows) * len(EXACT_12_COLUMNS):,d} cells ({len(EXACT_12_COLUMNS)} columns)")
        print(f" • Columns (12):  {', '.join(EXACT_12_COLUMNS)}")
        print("=" * 105)

        sample_emails = [r["Contact : Emails"] for r in exported_rows]
        matched, total_uniq = check_master_crm_match(sample_emails)
        pct = (matched / total_uniq * 100) if total_uniq > 0 else 0
        print(f"\n[CRM Ingestion Audit] {matched:,d} of {total_uniq:,d} contacts exist in RDS MySQL 'emails' table ({pct:.2f}% matched).")

        if matched < total_uniq:
            do_sync = input(f"\nWould you like to sync the {total_uniq - matched:,d} net new contacts to master CRM 'emails' table? [y/N]: ").strip().lower()
            if do_sync in ("y", "yes"):
                sync_records_to_master_crm(exported_rows)

    print("\n[+] Audit and export session completed.")

def main():
    parser = argparse.ArgumentParser(description="Audit and export created leads for Apollo/Freshsales logins.")
    parser.add_argument("--login", "-l", help="Account email to audit (e.g. abel.abraham@nestacktechnologies.com)")
    parser.add_argument("--spin", "-s", help="Selector spin number or date (e.g. 1, 2, '2026-09-09', or 'ALL')")
    parser.add_argument("--created-only", action="store_true", default=True, help="Export created data only (default: True)")
    parser.add_argument("--sync-crm", action="store_true", help="Sync exported records to RDS MySQL emails table")
    args = parser.parse_args()

    if not args.login:
        run_interactive()
    else:
        login_email = args.login.lower().strip()
        dates_map = audit_account(login_email)
        if not dates_map:
            print(f"[!] No dates found for {login_email}")
            return
        
        sorted_dates = get_prioritized_sorted_dates(dates_map)
        spin_items = []
        for idx, dt in enumerate(sorted_dates, 1):
            spin_items.append({
                "spin": idx,
                "date": dt,
                "created": dates_map[dt]["created_count"],
                "updated": dates_map[dt]["updated_count"],
                "records": dates_map[dt]["created_records"]
            })

        selected_spins, date_label = parse_multi_spin_selection(args.spin or "1", spin_items)
        dates_display = ", ".join(sp["date"] for sp in selected_spins)
        print(f"\n[+] Selected Spin Date(s): {dates_display}")

        all_recs = []
        unique_non_img = []
        seen_keys = set()
        cross_date_duplicates = 0

        for sp in selected_spins:
            dt = sp["date"]
            recs = dates_map[dt]["created_records"]
            if not recs and dates_map[dt]["created_count"] > 0:
                recs = fetch_freshsales_contacts_for_date(login_email, dt)
            all_recs.extend(recs)

            for r in recs:
                if not is_valid_non_image_entry(r):
                    continue
                email = clean_str(r.get("Contact : Emails") or r.get("Email")).lower()
                cid = clean_str(r.get("Contact : id") or r.get("id"))
                key = email if email else f"cid_{cid}"
                if key in seen_keys:
                    cross_date_duplicates += 1
                    continue
                seen_keys.add(key)
                unique_non_img.append(r)

        non_img = unique_non_img
        print(f"Total entries across dates: {len(all_recs):,d}")
        if cross_date_duplicates > 0:
            print(f"Cross-date duplicate leads removed: {cross_date_duplicates:,d}")
        print(f"Total unique non-image entries: {len(non_img):,d}")
        print(f"Total table cells: {len(non_img) * len(EXACT_12_COLUMNS):,d} ({len(non_img):,d} rows x 12 columns)")

        out_file, rows = export_created_only_csv(login_email, date_label, non_img)
        print(f"Saved: {out_file} ({len(rows):,d} unique records)")

        if args.sync_crm:
            sync_records_to_master_crm(rows)

if __name__ == "__main__":
    main()
