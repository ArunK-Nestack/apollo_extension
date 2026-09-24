#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apollo Saved Search & Account Leads Inspector
============================================
1. Selects any Apollo login (from the 19 configured accounts).
2. Extracts and loads live & registered saved searches for that login.
3. Computes the official 3 Apollo Web Search metrics for each search:
   - Total      : All matching contacts
   - New (Net)  : Contacts not yet saved in this account (prospected_by_current_team = 'no')
   - Saved      : Contacts already saved in this account (prospected_by_current_team = 'yes')
4. Displays the account's overall "Saved" tab leads count (contacts saved across all lists).
5. Interactively inspects, browses, or exports leads for any selected search or the whole Saved tab.
"""

from __future__ import annotations

import os
import sys
import json
import time
import copy
import argparse
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

# Ensure UTF-8 stdout for Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv()

from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row
from backend.api import get_connection, ensure_apollo_saved_leads_table
from backend.enrich_api import ensure_enrich_saved_leads_table

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "apollo_accounts.json")
SAVED_SEARCHES_PATH = os.path.join(PROJECT_ROOT, "config", "saved_searches.json")
EXPORTS_DIR = os.path.join(PROJECT_ROOT, "dist", "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

# Known Apollo internal views to exclude
SYSTEM_VIEW_NAMES = {
    "default view", "people auto-score", "scoring v2 autogen",
    "companies auto-score", "my people", "all people",
    "contacts that opened my email in the last hour",
    "my enrichable people", "all enrichable people"
}


def mask_key(key: str) -> str:
    """Mask sensitive API key for terminal display."""
    if not key or key == "YOUR_APOLLO_API_KEY_HERE":
        return "[NOT SET]"
    if len(key) <= 8:
        return "..." + key[-3:]
    return key[:3] + "..." + key[-4:]


def load_apollo_accounts() -> List[Dict[str, Any]]:
    """Load all configured accounts from config/apollo_accounts.json."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"{CONFIG_PATH} not found.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def check_account_health(api_key: str) -> Dict[str, Any]:
    """Check Apollo connection status and rate limit."""
    headers = {"Content-Type": "application/json", "X-Api-Key": api_key}
    try:
        res = requests.get("https://api.apollo.io/api/v1/auth/health", headers=headers, timeout=10)
        rate_limit = res.headers.get("x-rate-limit-minute", "60")
        if res.status_code == 200:
            return {"status": "Active", "rate_limit": f"{rate_limit} req/min"}
        elif res.status_code in (401, 403):
            return {"status": "Unauthorized", "rate_limit": "0"}
        return {"status": f"HTTP {res.status_code}", "rate_limit": "Unknown"}
    except Exception as ex:
        return {"status": "Error", "rate_limit": str(ex)}


def fetch_account_total_saved_contacts(api_key: str) -> int:
    """Get the overall total number of contacts saved in this Apollo account (Saved tab)."""
    headers = {"Content-Type": "application/json", "X-Api-Key": api_key}
    try:
        res = requests.post("https://api.apollo.io/api/v1/contacts/search", headers=headers, json={"per_page": 1, "page": 1}, timeout=12)
        if res.status_code == 200:
            data = res.json()
            pag = data.get("pagination") or {}
            return int(pag.get("total_entries", 0) or data.get("total_entries", 0))
    except Exception:
        pass
    return 0


def fetch_account_labels(api_key: str) -> List[Dict[str, Any]]:
    """Fetch all contact lists/labels in the account with their cached contact counts."""
    headers = {"Content-Type": "application/json", "X-Api-Key": api_key}
    try:
        res = requests.get("https://api.apollo.io/api/v1/labels", headers=headers, timeout=12)
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list):
                return [l for l in raw if l.get("modality") == "contacts"]
    except Exception:
        pass
    return []


def fetch_live_apollo_searches(api_key: str) -> List[Dict[str, Any]]:
    """Retrieve live saved searches (finder views) directly from Apollo REST API."""
    headers = {"Content-Type": "application/json", "X-Api-Key": api_key}
    try:
        res = requests.post("https://api.apollo.io/api/v1/finder_views/people/search", headers=headers, json={}, timeout=15)
        if res.status_code == 200:
            views = res.json().get("finder_views") or []
            live_searches = []
            for v in views:
                if v.get("system") is True or v.get("archived") is True:
                    continue
                name = (v.get("name") or "Untitled View").strip()
                if name.lower() in SYSTEM_VIEW_NAMES:
                    continue

                filters = v.get("signals") or v.get("filters") or {}
                live_searches.append({
                    "id": v.get("id"),
                    "name": name,
                    "display_name": name,
                    "filters": filters if isinstance(filters, dict) else {},
                    "is_live": True,
                })
            return live_searches
    except Exception as ex:
        print(f"[Notice] Could not fetch live finder views: {ex}", flush=True)
    return []


def load_account_saved_searches(account_email: str) -> List[Dict[str, Any]]:
    """Load searches saved in config/saved_searches.json for this account."""
    if not os.path.exists(SAVED_SEARCHES_PATH):
        return []
    try:
        with open(SAVED_SEARCHES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get(account_email.lower(), [])
    except Exception:
        pass
    return []


def probe_search_metrics(api_key: str, filters: Dict[str, Any]) -> Dict[str, int]:
    """
    Probe the 3 core Apollo Web metrics for a search:
      - total : Total contacts matching search
      - new   : Contacts not yet saved (prospected_by_current_team = ['no'])
      - saved : Contacts already saved in account (prospected_by_current_team = ['yes'])
    """
    headers = {"Content-Type": "application/json", "X-Api-Key": api_key}
    base = copy.deepcopy(filters) if isinstance(filters, dict) else {}

    def _query(extra_param: Optional[Dict[str, Any]] = None) -> int:
        p = copy.deepcopy(base)
        p["per_page"] = 1
        p["page"] = 1
        if extra_param:
            p.update(extra_param)
        else:
            p.pop("prospected_by_current_team", None)

        for attempt in range(3):
            try:
                r = requests.post("https://api.apollo.io/api/v1/mixed_people/api_search", headers=headers, json=p, timeout=15)
                if r.status_code == 200:
                    d = r.json()
                    tot = d.get("total_entries")
                    if tot is None:
                        tot = (d.get("pagination") or {}).get("total_entries", 0)
                    return int(tot or 0)
                elif r.status_code == 429:
                    time.sleep(3)
                    continue
                else:
                    return 0
            except Exception:
                time.sleep(1)
        return 0

    total_cnt = _query(None)
    new_cnt = _query({"prospected_by_current_team": ["no"]})
    saved_cnt = _query({"prospected_by_current_team": ["yes"]})

    return {
        "total": total_cnt,
        "new": new_cnt,
        "saved": saved_cnt,
    }


def batch_probe_all_searches(api_key: str, searches: List[Dict[str, Any]], max_workers: int = 4) -> None:
    """Probe all searches concurrently so the 3-metric table loads fast."""
    def _worker(s):
        metrics = probe_search_metrics(api_key, s.get("filters", {}))
        s["total"] = metrics["total"]
        s["new"] = metrics["new"]
        s["saved"] = metrics["saved"]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_worker, s) for s in searches]
        for f in as_completed(futures):
            pass


def fetch_contacts_preview(
    api_key: str,
    filters: Dict[str, Any],
    prospected_status: Optional[str] = None,
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """
    Fetch preview contacts for a search.
    If prospected_status is 'yes' (Saved tab), queries /contacts/search for unlocked full names & emails.
    Otherwise queries /mixed_people/api_search.
    """
    headers = {"Content-Type": "application/json", "X-Api-Key": api_key}
    p = copy.deepcopy(filters)
    p["per_page"] = min(limit, 50)
    p["page"] = 1

    if prospected_status:
        p["prospected_by_current_team"] = [prospected_status]

    if prospected_status == "yes":
        # Contacts endpoint returns full saved lead details (name, email, etc.)
        url = "https://api.apollo.io/api/v1/contacts/search"
    else:
        url = "https://api.apollo.io/api/v1/mixed_people/api_search"

    try:
        res = requests.post(url, headers=headers, json=p, timeout=20)
        if res.status_code == 200:
            d = res.json()
            return d.get("contacts") or d.get("people") or []
    except Exception as ex:
        print(f"[Error fetching preview]: {ex}")
    return []


def display_leads_table(leads: List[Dict[str, Any]], title_desc: str = "Leads Preview") -> None:
    """Format and print a table of lead records."""
    if not leads:
        print(f"\n[!] No leads returned for '{title_desc}'.")
        return

    print("\n" + "=" * 115)
    print(f" {title_desc.upper()} ({len(leads)} leads shown)")
    print("=" * 115)
    print(f" {'#':<3} | {'Full Name':<22} | {'Job Title':<28} | {'Company':<25} | {'Email':<25}")
    print("-" * 115)

    for idx, l in enumerate(leads, 1):
        name = l.get("name") or f"{l.get('first_name', '')} {l.get('last_name', '')}".strip() or "[Obfuscated]"
        title = (l.get("title") or "")[:28]
        org = l.get("organization") or {}
        comp = (l.get("organization_name") or org.get("name") or "")[:25]
        email = l.get("email") or l.get("email_status") or "[Not Unlocked]"

        print(f" {idx:<3} | {name:<22} | {title:<28} | {comp:<25} | {email:<25}")
    print("=" * 115)


def fetch_contacts_stream(
    api_key: str,
    filters: Dict[str, Any],
    prospected_status: Optional[str] = None,
    max_contacts: Optional[int] = None,
    page_delay: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Stream contacts across pages from Apollo REST API.
    If prospected_status == 'yes' (Saved tab), queries /contacts/search for unlocked full names & emails.
    Otherwise queries /mixed_people/api_search.
    Paginates automatically until max_contacts or end of available results.
    """
    headers = {"Content-Type": "application/json", "X-Api-Key": api_key}
    p = copy.deepcopy(filters)
    per_page = 100
    p["per_page"] = per_page

    if prospected_status:
        p["prospected_by_current_team"] = [prospected_status]

    if prospected_status == "yes":
        url = "https://api.apollo.io/api/v1/contacts/search"
    else:
        url = "https://api.apollo.io/api/v1/mixed_people/api_search"

    all_contacts = []
    page = 1
    total_entries = None

    while True:
        p["page"] = page
        try:
            res = requests.post(url, headers=headers, json=p, timeout=25)
            if res.status_code == 200:
                data = res.json()
                if total_entries is None:
                    pag = data.get("pagination") or {}
                    total_entries = int(pag.get("total_entries") or data.get("total_entries") or 0)
                    total_pages = min(100, max(1, (total_entries + per_page - 1) // per_page))
                    print(f"  [Stream] Total available: {total_entries:,d} leads (~{total_pages} pages). Streaming page {page}...")

                batch_contacts = data.get("contacts") or data.get("people") or []
                if not batch_contacts:
                    break

                all_contacts.extend(batch_contacts)
                print(f"  [Page {page:02d}] Fetched {len(batch_contacts)} leads | Cumulative: {len(all_contacts):,d} leads", flush=True)

                if max_contacts and len(all_contacts) >= max_contacts:
                    all_contacts = all_contacts[:max_contacts]
                    break

                pagination = data.get("pagination") or {}
                if page >= pagination.get("total_pages", 100) or page >= 100:
                    break

                page += 1
                time.sleep(page_delay)
            elif res.status_code == 429:
                retry_after = int(res.headers.get("Retry-After", "5"))
                print(f"\n  [!] Rate limit (429). Pausing for {retry_after + 2}s...", flush=True)
                time.sleep(retry_after + 2)
                continue
            else:
                print(f"  [!] HTTP {res.status_code}: {res.text[:150]}")
                break
        except Exception as ex:
            print(f"  [!] Fetch error on page {page}: {ex}")
            break

    return all_contacts


def export_leads_to_csv(leads: List[Dict[str, Any]], filename_prefix: str, account_email: str) -> str:
    """Export lead records to Apollo official 75-column CSV in dist/exports/."""
    import csv
    ts = time.strftime("%Y%m%d_%H%M%S")
    clean_prefix = filename_prefix.replace(" ", "_").replace("/", "_").replace("\\", "_")
    out_file = os.path.join(EXPORTS_DIR, f"{clean_prefix}_75col_{ts}.csv")

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(APOLLO_75_HEADERS)
        for l in leads:
            writer.writerow(format_apollo_lead_row(l, account_email=account_email))

    print(f"\n[+] Successfully exported {len(leads):,d} leads with ALL 75 Apollo Columns to:")
    print(f"    {os.path.abspath(out_file)}")
    return out_file


def save_leads_to_mysql_batch(
    leads: List[Dict[str, Any]],
    batch_tag: str,
    account_email: str,
    target_table: str = "enrich_saved_leads",
) -> int:
    """Save extracted Apollo contacts directly into MySQL table under a batch, preserving full enriched status."""
    if not leads:
        print("[!] No leads to save to database.")
        return 0

    table_name = "enrich_saved_leads" if target_table == "enrich_saved_leads" else "apollo_saved_leads"
    saved_count = 0
    with get_connection() as conn:
        if table_name == "enrich_saved_leads":
            ensure_enrich_saved_leads_table(conn)
        else:
            ensure_apollo_saved_leads_table(conn)

        rows = []
        for l in leads:
            org = l.get("organization") or {}
            c_name = (org.get("name") or l.get("organization_name") or l.get("company") or "").strip()
            c_dom = (org.get("primary_domain") or l.get("company_domain") or "").strip().lower()
            for prefix in ("https://", "http://", "www."):
                if c_dom.startswith(prefix):
                    c_dom = c_dom[len(prefix):]
            c_dom = c_dom.split("/")[0].strip()

            contact_id = l.get("id") or l.get("person_id") or ""
            name = l.get("name") or f"{l.get('first_name', '')} {l.get('last_name', '')}".strip()
            first_name = l.get("first_name") or ""
            last_name = l.get("last_name") or ""
            if not first_name and not last_name and name:
                parts = name.split(None, 1)
                first_name = parts[0]
                last_name = parts[1] if len(parts) > 1 else ""

            title = l.get("title") or l.get("job_title") or ""
            email = (l.get("email") or "").strip()
            raw_st = (l.get("email_status") or "").strip().lower()
            if raw_st == "verified":
                email_status = "Verified"
            elif raw_st == "unavailable":
                email_status = "Unavailable"
            elif raw_st:
                email_status = raw_st.capitalize()
            else:
                email_status = "Verified" if email else "Unavailable"

            website = org.get("website_url") or l.get("website_link") or ""
            if not website and c_dom:
                website = f"https://{c_dom}"

            annual_revenue = org.get("organization_revenue_printed") or org.get("annual_revenue_printed") or ""
            employee_count = org.get("estimated_num_employees") or None
            industry = org.get("industry") or ""

            # Technologies
            tech_raw = org.get("current_technologies") or []
            if isinstance(tech_raw, list):
                tech_names = [t.get("name") if isinstance(t, dict) else str(t) for t in tech_raw if t]
                tech_json = json.dumps(tech_names)
            else:
                tech_json = json.dumps([])

            # Keywords
            kw_raw = org.get("keywords") or []
            kw_json = json.dumps(kw_raw) if isinstance(kw_raw, list) else json.dumps([])

            comp_phone = org.get("sanitized_phone") or org.get("phone") or ""
            hq_addr = org.get("raw_address") or ""
            location = l.get("formatted_address") or l.get("city") or ""
            linkedin_url = l.get("linkedin_url") or ""
            comp_linkedin = org.get("linkedin_url") or ""
            apollo_profile_url = f"https://app.apollo.io/#/people/{contact_id}" if contact_id else ""
            raw_data = json.dumps(l)

            rows.append((
                batch_tag,
                contact_id,
                name,
                first_name,
                last_name,
                title,
                email,
                email_status,
                c_name,
                c_dom,
                website,
                annual_revenue,
                employee_count,
                industry,
                tech_json,
                kw_json,
                comp_phone,
                hq_addr,
                location,
                linkedin_url,
                comp_linkedin,
                apollo_profile_url,
                "Saved_Enriched_Lead",
                account_email,
                0,  # credits charged
                raw_data
            ))

        sql = f"""
            INSERT INTO `{table_name}` (
                `batch`, `apollo_id`, `name`, `first_name`, `last_name`, `job_title`,
                `email`, `email_status`, `company`, `company_domain`, `website_link`,
                `annual_revenue`, `employee_count`, `industry`, `tech_stack`, `keywords`,
                `company_phone`, `hq_address`, `location`, `linkedin_url`,
                `company_linkedin_url`, `apollo_profile_url`, `segment`,
                `account_used`, `credits_charged`, `enriched_at`, `raw_enrichment_data`
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, NOW(), %s
            )
            ON DUPLICATE KEY UPDATE
                `email` = IF(VALUES(`email`) != '', VALUES(`email`), `email`),
                `email_status` = IF(VALUES(`email_status`) != '', VALUES(`email_status`), `email_status`),
                `annual_revenue` = IF(VALUES(`annual_revenue`) != '', VALUES(`annual_revenue`), `annual_revenue`),
                `employee_count` = COALESCE(VALUES(`employee_count`), `employee_count`),
                `industry` = IF(VALUES(`industry`) != '', VALUES(`industry`), `industry`),
                `tech_stack` = COALESCE(VALUES(`tech_stack`), `tech_stack`),
                `keywords` = COALESCE(VALUES(`keywords`), `keywords`),
                `company_phone` = IF(VALUES(`company_phone`) != '', VALUES(`company_phone`), `company_phone`),
                `hq_address` = IF(VALUES(`hq_address`) != '', VALUES(`hq_address`), `hq_address`),
                `company_linkedin_url` = IF(VALUES(`company_linkedin_url`) != '', VALUES(`company_linkedin_url`), `company_linkedin_url`),
                `raw_enrichment_data` = VALUES(`raw_enrichment_data`),
                `enriched_at` = NOW();
        """
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
            saved_count = cur.rowcount
        conn.commit()

    emails_saved = sum(1 for r in rows if r[6])
    print(f"\n[✓] Successfully saved {len(rows):,d} leads directly into `{table_name}` under batch: '{batch_tag}'")
    print(f"    • Status: ENRICHED (enriched_at = NOW(), full raw Apollo payload preserved)")
    print(f"    • Emails Saved: {emails_saved} leads with verified business email")
    print(f"    • Ready for instant export (75 columns) via manage_batches.py or clean_enriched_export.py!")
    return saved_count


def prompt_batch_selection_for_login(
    target_table: str,
    account_email: str,
    default_batch_name: str,
) -> str:
    """
    Prompt user to either append & merge into an existing batch for this login
    (combining Web + CLI saved leads) or create a new batch name.
    """
    existing_batches = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                acc_slug = account_email.split("@")[0].lower()
                cur.execute(f"""
                    SELECT `batch`, COUNT(*) as cnt, MAX(`created_at`) as last_added
                    FROM `{target_table}`
                    WHERE LOWER(`account_used`) = %s 
                       OR LOWER(`batch`) LIKE %s
                    GROUP BY `batch`
                    ORDER BY cnt DESC, last_added DESC
                    LIMIT 15;
                """, (account_email.lower(), f"%{acc_slug}%"))
                rows = cur.fetchall()
                for r in rows:
                    existing_batches.append({"batch": r[0], "count": r[1], "last_added": str(r[2]) if r[2] else ""})
    except Exception:
        pass

    if existing_batches:
        print(f"\nBATCH DESTINATION FOR ACCOUNT: {account_email}")
        print("  [1] Append & Merge into an EXISTING batch for this login (Combines Web + CLI saved leads - Recommended)")
        print(f"  [2] Create a NEW batch name (Default: '{default_batch_name}')")
        b_mode = input("Select option [1/2, default 1]: ").strip()
        if b_mode in ("", "1"):
            print(f"\nExisting Batches in `{target_table}` for this account:")
            print("-" * 75)
            for idx, eb in enumerate(existing_batches, 1):
                print(f"  [{idx:>2}] {eb['batch']:<40} | {eb['count']:,d} leads")
            print("-" * 75)
            eb_sel = input(f"Select batch number to append to [1-{len(existing_batches)}] or type custom name: ").strip()
            if eb_sel.isdigit() and 1 <= int(eb_sel) <= len(existing_batches):
                chosen = existing_batches[int(eb_sel) - 1]["batch"]
                print(f"✓ Merging into existing batch: '{chosen}'")
                return chosen
            elif eb_sel:
                return eb_sel

    batch_in = input(f"Enter batch name (press Enter for '{default_batch_name}'): ").strip()
    return batch_in if batch_in else default_batch_name


def format_filters_summary(filters: Dict[str, Any]) -> str:
    """Format filter payload into human-readable bullet points."""
    lines = []
    if filters.get("person_titles"):
        t = filters["person_titles"]
        t_list = t if isinstance(t, list) else [t]
        lines.append(f"  - Target Titles      : {', '.join(t_list[:6])}{'...' if len(t_list) > 6 else ''}")
    if filters.get("person_locations"):
        locs = filters["person_locations"]
        l_str = ", ".join(locs) if isinstance(locs, list) else str(locs)
        lines.append(f"  - Target Locations   : {l_str}")
    if filters.get("person_seniorities"):
        sens = filters["person_seniorities"]
        s_str = ", ".join(sens) if isinstance(sens, list) else str(sens)
        lines.append(f"  - Seniority Levels   : {s_str}")
    if filters.get("organization_num_employees_ranges"):
        r = filters["organization_num_employees_ranges"]
        r_str = ", ".join(r) if isinstance(r, list) else str(r)
        lines.append(f"  - Employee Headcount : {r_str}")
    if filters.get("q_organization_keyword_tags"):
        k = filters["q_organization_keyword_tags"]
        k_list = k if isinstance(k, list) else [k]
        lines.append(f"  - Industry Keywords  : {', '.join(k_list[:5])}")
    if filters.get("q_keywords"):
        lines.append(f"  - Keyword Search     : '{filters['q_keywords']}'")
    return "\n".join(lines) if lines else "  - Broad Search (No restrictive keywords/titles)"


# =====================================================================
# MAIN WORKFLOW CONTROLLER
# =====================================================================

def run_inspector(selected_account_idx: Optional[int] = None):
    accounts = load_apollo_accounts()

    print("\n" + "=" * 105)
    print("         APOLLO SAVED SEARCH & ACCOUNT SAVED LEADS INSPECTOR (NEW / TOTAL / SAVED)")
    print("=" * 105)

    # 1. Account Selection
    if selected_account_idx and 1 <= selected_account_idx <= len(accounts):
        chosen_idx = selected_account_idx
    else:
        print("\nSelect an Apollo Login Account to inspect searches & saved leads:")
        print(f" {'#':<3} | {'Account Name':<20} | {'Email Address':<38} | {'API Key'}")
        print("-" * 105)
        for idx, acc in enumerate(accounts, 1):
            print(f" [{idx:<2}] | {acc.get('name', 'N/A'):<20} | {acc.get('email', 'N/A'):<38} | {mask_key(acc.get('api_key', ''))}")
        print("-" * 105)

        while True:
            u_in = input(f"\nEnter Account Number [1-{len(accounts)}] (default 12 VRAGHAVAN): ").strip()
            if not u_in:
                chosen_idx = 12 if len(accounts) >= 12 else 1
                break
            if u_in.isdigit() and 1 <= int(u_in) <= len(accounts):
                chosen_idx = int(u_in)
                break
            print("Invalid selection, please choose a valid number.")

    account = accounts[chosen_idx - 1]
    active_email = str(account.get("email", "")).strip().upper()
    active_name = str(account.get("name", "")).strip()
    active_key = str(account.get("api_key", "")).strip()

    print(f"\n>> Selected Account: {active_email} ({active_name})")
    print("   Checking API connection and probing account metrics...")

    health = check_account_health(active_key)
    if health["status"] == "Unauthorized":
        print(f"[!] ERROR: API key for {active_email} is unauthorized or expired.")
        return

    # 2. Query Account-Wide "Saved" Tab Totals
    total_saved_in_account = fetch_account_total_saved_contacts(active_key)
    account_labels = fetch_account_labels(active_key)

    # 3. Load Searches (Live + Catalog)
    print(f"   Extracting live saved searches from Apollo & local catalog...")
    live_views = fetch_live_apollo_searches(active_key)
    catalog_searches = load_account_saved_searches(active_email)

    # Combine unique searches by name
    searches_map: Dict[str, Dict[str, Any]] = {}
    for s in catalog_searches:
        name = s.get("name") or s.get("display_name")
        if name:
            searches_map[name.strip()] = {
                "name": name.strip(),
                "filters": s.get("filters", {}),
                "source": "Catalog",
            }
    for s in live_views:
        name = s.get("name")
        if name:
            searches_map[name.strip()] = {
                "name": name.strip(),
                "filters": s.get("filters", {}),
                "source": "Apollo Live",
            }

    all_searches = list(searches_map.values())

    if all_searches:
        print(f"   Probing 3-metric breakdown (Total, New, Saved) across {len(all_searches)} searches...")
        batch_probe_all_searches(active_key, all_searches, max_workers=5)

    # 4. Display Main Searches & Saved Vault Overview
    while True:
        print("\n" + "=" * 115)
        print(f" LOGIN: {active_email} ({active_name}) | Status: {health['status']} ({health['rate_limit']})")
        print(f" [*] ACCOUNT OVERALL SAVED TAB: {total_saved_in_account:,d} contacts saved in this account across {len(account_labels)} lists")
        print("=" * 115)

        if all_searches:
            print(f" {'#':<3} | {'Search Name':<36} | {'Total Leads':<13} | {'Net New (Unsaved)':<18} | {'Saved (In Account)':<19} | {'Source'}")
            print("-" * 115)
            for idx, s in enumerate(all_searches, 1):
                tot = f"{s.get('total', 0):,d}"
                new = f"{s.get('new', 0):,d}"
                sav = f"{s.get('saved', 0):,d}"
                print(f" [{idx:<2}] | {s['name']:<36} | {tot:<13} | {new:<18} | {sav:<19} | {s.get('source', 'Saved')}")
            print("-" * 115)
        else:
            print("  [No saved searches registered for this login yet]")

        print("\nOPTIONS:")
        if all_searches:
            print(f"  [1-{len(all_searches)}] Select a search to inspect its Saved or Net New leads")
        print("  [S] View & Browse all leads in the account's SAVED TAB (Overall Saved Vault)")
        print("  [L] View all Contact Lists / Labels in this account")
        print("  [R] Refresh live searches from Apollo")
        print("  [Q] Back / Exit")

        cmd = input(f"\nSelect an option: ").strip().upper()

        if cmd in ("Q", "EXIT", "QUIT"):
            print("\nExiting Inspector.")
            break

        elif cmd == "R":
            print("\n>> Refreshing live searches and account metrics...")
            run_inspector(selected_account_idx=chosen_idx)
            break

        elif cmd == "L":
            print("\n" + "-" * 90)
            print(f"CONTACT LISTS IN ACCOUNT: {active_email}")
            print("-" * 90)
            if not account_labels:
                print("  (No contact lists found in this account)")
            else:
                for idx, lbl in enumerate(account_labels, 1):
                    cnt = lbl.get("cached_count") or lbl.get("count") or 0
                    print(f"  [{idx:>2}] {lbl.get('name', 'List'):<45} | {cnt:,d} contacts")
            print("-" * 90)
            input("\nPress Enter to continue...")
            continue

        elif cmd == "S":
            # Account-Wide Saved Tab Leads
            while True:
                print("\n" + "=" * 95)
                print(f" ACCOUNT-WIDE SAVED TAB LEADS ({total_saved_in_account:,d} total saved contacts across {len(account_labels)} lists)")
                print("=" * 95)
                print("Options:")
                print("  [1] Quick Preview (first 25 saved contacts)")
                print(f"  [2] Extract & Export ALL Saved Contacts to official 75-Column CSV ({total_saved_in_account:,d} leads)")
                print(f"  [3] Extract & Save ALL Saved Contacts to MySQL Database Batch ({total_saved_in_account:,d} leads)")
                print("  [4] Return to main menu")

                sub_opt = input("\nSelect option [1-4, default 1]: ").strip()
                if sub_opt in ("", "1"):
                    leads = fetch_contacts_preview(active_key, filters={}, prospected_status="yes", limit=25)
                    display_leads_table(leads, title_desc=f"{active_email} - Overall Saved Account Leads")
                    exp_prev = input("\n[E]xport this 25-lead preview to 75-Column CSV, or press Enter to return: ").strip().upper()
                    if exp_prev == "E":
                        export_leads_to_csv(leads, f"saved_account_preview_{active_email}", active_email)
                        input("\nPress Enter to continue...")
                elif sub_opt == "2":
                    lim_in = input(f"Enter number of leads to extract (press Enter for ALL {total_saved_in_account:,d}): ").strip()
                    max_leads = int(lim_in) if lim_in.isdigit() and int(lim_in) > 0 else None
                    print(f"\nStreaming saved contacts from Apollo account {active_email} across pages...")
                    leads = fetch_contacts_stream(active_key, filters={}, prospected_status="yes", max_contacts=max_leads)
                    if leads:
                        export_leads_to_csv(leads, f"saved_account_leads_{active_email}", active_email)
                    input("\nPress Enter to continue...")
                elif sub_opt == "3":
                    lim_in = input(f"Enter number of leads to extract (press Enter for ALL {total_saved_in_account:,d}): ").strip()
                    max_leads = int(lim_in) if lim_in.isdigit() and int(lim_in) > 0 else None
                    tbl_in = input("Save to [1] `apollo_saved_leads` (Default for batch sync) or [2] `enrich_saved_leads`? [1/2, default 1]: ").strip()
                    target_tbl = "enrich_saved_leads" if tbl_in == "2" else "apollo_saved_leads"
                    ts_tag = time.strftime("%Y%m%d_%H%M%S")
                    def_batch = f"BATCH_SAVED_{active_email.split('@')[0]}_{ts_tag}"
                    chosen_batch = prompt_batch_selection_for_login(target_tbl, active_email, def_batch)
                    print(f"\nStreaming saved contacts from Apollo account {active_email} across pages...")
                    leads = fetch_contacts_stream(active_key, filters={}, prospected_status="yes", max_contacts=max_leads)
                    if leads:
                        save_leads_to_mysql_batch(leads, chosen_batch, active_email, target_table=target_tbl)
                    input("\nPress Enter to continue...")
                elif sub_opt == "4":
                    break
            continue

        elif cmd.isdigit() and all_searches and 1 <= int(cmd) <= len(all_searches):
            chosen_search = all_searches[int(cmd) - 1]
            s_name = chosen_search["name"]
            s_filters = chosen_search.get("filters", {})

            # Search details sub-menu
            while True:
                print("\n" + "=" * 95)
                print(f" SEARCH DETAILS: '{s_name}'")
                print("=" * 95)
                print(f"  • Total Matching Leads : {chosen_search.get('total', 0):,d}")
                print(f"  • Net New (Available)  : {chosen_search.get('new', 0):,d} (Not yet in account)")
                print(f"  • Already Saved        : {chosen_search.get('saved', 0):,d} (Already saved in this login)")
                print("-" * 95)
                print("Active Search Filters:")
                print(format_filters_summary(s_filters))
                print("-" * 95)
                print("Actions:")
                print("  [1] Browse & Preview SAVED LEADS for this search (In Account)")
                print("  [2] Browse & Preview NET NEW LEADS for this search (Unsaved)")
                print(f"  [3] Extract & Export SAVED LEADS to 75-Column Apollo CSV ({chosen_search.get('saved', 0):,d} leads)")
                print(f"  [4] Extract & Export NET NEW LEADS to 75-Column Apollo CSV ({chosen_search.get('new', 0):,d} leads)")
                print(f"  [5] Extract & Save SAVED LEADS to MySQL Database Batch (for Enrichment)")
                print("  [6] Return to searches list")

                act = input("\nSelect action [1-6, default 1]: ").strip()
                if act in ("", "1"):
                    saved_leads = fetch_contacts_preview(active_key, s_filters, prospected_status="yes", limit=25)
                    display_leads_table(saved_leads, title_desc=f"Saved Leads for '{s_name}'")
                    exp_c = input("\n[E]xport this preview to 75-Column CSV, or press Enter to return: ").strip().upper()
                    if exp_c == "E":
                        export_leads_to_csv(saved_leads, f"saved_preview_{s_name}", active_email)
                        input("\nPress Enter to continue...")
                elif act == "2":
                    new_leads = fetch_contacts_preview(active_key, s_filters, prospected_status="no", limit=25)
                    display_leads_table(new_leads, title_desc=f"Net New Leads for '{s_name}'")
                    exp_c = input("\n[E]xport this preview to 75-Column CSV, or press Enter to return: ").strip().upper()
                    if exp_c == "E":
                        export_leads_to_csv(new_leads, f"net_new_preview_{s_name}", active_email)
                        input("\nPress Enter to continue...")
                elif act == "3":
                    lim_in = input(f"Enter number of saved leads to extract (press Enter for ALL {chosen_search.get('saved', 0):,d}): ").strip()
                    max_leads = int(lim_in) if lim_in.isdigit() and int(lim_in) > 0 else None
                    print(f"\nStreaming saved leads for '{s_name}' from Apollo across pages...")
                    saved_leads = fetch_contacts_stream(active_key, s_filters, prospected_status="yes", max_contacts=max_leads)
                    if saved_leads:
                        export_leads_to_csv(saved_leads, f"saved_{s_name}", active_email)
                    input("\nPress Enter to continue...")
                elif act == "4":
                    lim_in = input(f"Enter number of net new leads to extract (press Enter for ALL {chosen_search.get('new', 0):,d}): ").strip()
                    max_leads = int(lim_in) if lim_in.isdigit() and int(lim_in) > 0 else None
                    print(f"\nStreaming net new leads for '{s_name}' from Apollo across pages...")
                    new_leads = fetch_contacts_stream(active_key, s_filters, prospected_status="no", max_contacts=max_leads)
                    if new_leads:
                        export_leads_to_csv(new_leads, f"net_new_{s_name}", active_email)
                    input("\nPress Enter to continue...")
                elif act == "5":
                    lim_in = input(f"Enter number of saved leads to extract (press Enter for ALL {chosen_search.get('saved', 0):,d}): ").strip()
                    max_leads = int(lim_in) if lim_in.isdigit() and int(lim_in) > 0 else None
                    tbl_in = input("Save to [1] `apollo_saved_leads` (Default for batch sync) or [2] `enrich_saved_leads`? [1/2, default 1]: ").strip()
                    target_tbl = "enrich_saved_leads" if tbl_in == "2" else "apollo_saved_leads"
                    ts_tag = time.strftime("%Y%m%d_%H%M%S")
                    clean_s = s_name.replace(" ", "_")[:20]
                    def_batch = f"BATCH_{clean_s}_{ts_tag}"
                    chosen_batch = prompt_batch_selection_for_login(target_tbl, active_email, def_batch)
                    print(f"\nStreaming saved leads for '{s_name}' from Apollo across pages...")
                    saved_leads = fetch_contacts_stream(active_key, s_filters, prospected_status="yes", max_contacts=max_leads)
                    if saved_leads:
                        save_leads_to_mysql_batch(saved_leads, chosen_batch, active_email, target_table=target_tbl)
                    input("\nPress Enter to continue...")
                elif act == "6":
                    break
        else:
            print("[!] Invalid option. Please choose a valid search number, 'S', 'L', 'R', or 'Q'.")


def main():
    parser = argparse.ArgumentParser(description="Apollo Saved Search & Account Leads Inspector")
    parser.add_argument("--account", "-a", type=int, help="Account index (1-19)")
    args = parser.parse_args()
    try:
        run_inspector(selected_account_idx=args.account)
    except KeyboardInterrupt:
        print("\n\nOperation interrupted by user. Exiting cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    main()
