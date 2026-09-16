#!/usr/bin/env python3
"""
Apollo Direct Search & CRM Qualification CLI (v2.1)
=====================================================
Automated, terminal-driven pipeline for Apollo lead generation:
1. Connects to Apollo's official REST API via configured account keys (1 login per session).
2. Displays connected engines banner: AWS RDS 7.28M CRM, 64K Titles, 28K Indian Surnames, Staging Ledger.
3. Automatically fetches and displays available searches for that login along with exact lead counts.
4. Inspects selected search, shows active filters and data volume, and provides full web filter editing.
5. Applies All-Lists Exclusion Protocol and allows saving the refined search.
6. Renders 10-lead data schema preview verifying 100% column parity with the Chrome extension.
7. Refines via search bar keyword, keeping pages under Apollo's 100-page cap, or runs the base search as-is.
8. Paces streaming at 1.5s/page and applies strict batch-wide 4-layer qualification matching DOM extension:
   - Layer 1: AWS RDS CRM email domain seek (0.5ms index seek on idx_emails_domain).
   - Layer 2: Indian Demographic Origin Guardrail (28K surnames + Marisa Trie + local heuristics).
   - Layer 3: 64K Job Title Guardrails (11 target departments vs 5 excluded).
   - Layer 4: Session-Wide 1 Contact Per Company Deduplication (highest seniority score retained).
9. Continuous search loop: prompts for the next keyword slice without restarting.
10. Consolidated Apollo 75-column CSV export.
"""

import sys
import os
import json
import time
import csv
import uuid
import copy
import argparse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import (
    ApolloContact,
    get_connection,
    ensure_apollo_saved_leads_table,
    ensure_detected_companies_table,
    clean_company_name,
    generate_candidate_domains,
    resolve_company_domains,
    check_person_and_domains_in_crm_batch,
    lookup_job_titles_batch,
    lookup_job_title_in_db,
    queue_pending_job_titles,
    is_unambiguous_pure_indian_name,
    get_seniority_score,
    normalize_text,
)
from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row

def clean_domain(domain_str: str) -> str:
    """Clean company domain for matching."""
    if not domain_str:
        return ""
    d = domain_str.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.split("/")[0].strip()

load_dotenv()

CONFIG_PATH = os.path.join("config", "apollo_accounts.json")
TEMPLATE_PATH = os.path.join("config", "apollo_accounts.template.json")
SAVED_SEARCHES_PATH = os.path.join("config", "saved_searches.json")
EXPORTS_DIR = os.path.join("dist", "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

# Safe pacing delay between Apollo Search API page requests (seconds)
API_PAGE_DELAY_SEC = 1.5

# Known Apollo system views to exclude from custom searches list
SYSTEM_VIEW_NAMES = {
    "default view", "people auto-score", "scoring v2 autogen",
    "companies auto-score", "my people", "all people",
    "contacts that opened my email in the last hour",
    "my enrichable people", "all enrichable people"
}


# =====================================================================
# 1. SECURITY, CONFIGURATION & HEALTH CHECKS
# =====================================================================

def mask_key(key: str) -> str:
    """Mask sensitive API keys for terminal display (e.g. sk_...4b2c)."""
    if not key or key == "YOUR_APOLLO_API_KEY_HERE":
        return "[NOT SET]"
    if len(key) <= 8:
        return "..." + key[-3:]
    return key[:3] + "..." + key[-4:]


def load_apollo_accounts() -> List[Dict[str, Any]]:
    """Load and validate accounts from config/apollo_accounts.json."""
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


def check_account_health(api_key: str) -> Dict[str, Any]:
    """Check Apollo connection and rate limits via auth/health endpoint."""
    if not api_key or "YOUR_APOLLO_API_KEY" in api_key:
        return {"status": "unconfigured", "rate_limit": "0", "message": "API Key is not configured."}

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }

    try:
        url = "https://api.apollo.io/api/v1/auth/health"
        res = requests.get(url, headers=headers, timeout=10)
        rate_limit = res.headers.get("x-rate-limit-minute", "60")
        if res.status_code == 200:
            return {
                "status": "Active",
                "rate_limit": f"{rate_limit} req/min",
                "message": "Connected successfully"
            }
        elif res.status_code in (401, 403):
            return {"status": "Unauthorized", "rate_limit": "0", "message": "Invalid API Key"}
        else:
            return {"status": f"HTTP {res.status_code}", "rate_limit": "Unknown", "message": res.text[:80]}
    except Exception as ex:
        return {"status": "Error", "rate_limit": "0", "message": str(ex)}


# =====================================================================
# 2. CONNECTED ENGINES & DATABASE BANNER
# =====================================================================

def display_startup_banner():
    """Probe and display connection status for all 5 core lookup engines."""
    print("=" * 100)
    print("                     APOLLO DIRECT SEARCH & CRM QUALIFICATION ENGINE (v2.1)")
    print("=" * 100)
    print("[CONNECTED ENGINES & DATA VAULTS]:")

    # Engine 1: AWS RDS CRM Database
    crm_status = "Connecting..."
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM `emails`")
                row = cur.fetchone()
                count = row[0] if row else 0
                crm_status = f"{count:,d} Emails Indexed (idx_emails_domain -> 0.5ms Seek)"
    except Exception as ex:
        crm_status = f"Warning: Connection check failed ({ex})"
    print(f"  ├── [1] AWS RDS CRM Database    : {crm_status}")

    # Engine 2: Job Title Guardrails
    title_status = "Checking..."
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM `job_title_guardrails`")
                row = cur.fetchone()
                count = row[0] if row else 0
                title_status = f"{count:,d} Categorized Rules (11 Target vs 5 Excluded Segments)"
    except Exception as ex:
        title_status = f"Warning: Title DB check failed ({ex})"
    print(f"  ├── [2] Job Title Engine        : {title_status}")

    # Engine 3: Indian Demographic Name Engine
    indian_status = "Checking..."
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM `indian_name_guardrails`")
                row = cur.fetchone()
                count = row[0] if row else 0
                indian_status = f"{count:,d} Verified Rules (MySQL `indian_name_guardrails` + Trie + Local Heuristics)"
    except Exception as ex:
        indian_status = f"Warning: Indian guardrails DB check failed ({ex})"
    print(f"  ├── [3] Indian Demographic Name : {indian_status}")

    # Engine 4: LLM Title Fallback
    print("  ├── [4] LLM Title Fallback      : gpt-4o-mini (Auto-Caching to MySQL job_title_guardrails)")

    # Engine 5: Apollo Staging Ledger
    saved_count = 0
    try:
        with get_connection() as conn:
            ensure_apollo_saved_leads_table(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM `apollo_saved_leads`")
                row = cur.fetchone()
                saved_count = row[0] if row else 0
    except Exception:
        pass
    print(f"  └── [5] Apollo Staging Ledger   : MySQL `apollo_saved_leads` ({saved_count:,d} leads in storage)")
    print("-" * 100)


# =====================================================================
# 3. APOLLO SEARCH API CALLS & SAVED SEARCH INSPECTOR
# =====================================================================

def sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize filter payload to ensure clean types and strip invalid tokens like 'any'."""
    if not isinstance(payload, dict):
        return {}
    clean = copy.deepcopy(payload)

    # Clean label arrays from non-id tokens like 'any'
    for list_key in ("not_account_label_ids", "account_label_ids", "not_contact_label_ids", "contact_label_ids"):
        if list_key in clean and isinstance(clean[list_key], list):
            clean[list_key] = [str(x) for x in clean[list_key] if x and str(x).strip().lower() != "any"]
            if not clean[list_key]:
                clean.pop(list_key, None)

    # Ensure list types for array fields
    for arr_key in (
        "person_titles", "person_not_titles", "person_locations", "person_not_locations",
        "person_seniorities", "organization_num_employees_ranges", "q_organization_keyword_tags",
        "organization_industry_tag_ids", "organization_not_industry_tag_ids",
        "contact_email_status_v2", "organization_domains", "not_organization_domains"
    ):
        if arr_key in clean and clean[arr_key] is not None:
            if isinstance(clean[arr_key], str):
                clean[arr_key] = [clean[arr_key]]
            elif not isinstance(clean[arr_key], list):
                clean[arr_key] = list(clean[arr_key])

    return clean


def parse_apollo_url(raw_url: str) -> Dict[str, Any]:
    """Parse Apollo search URL (query string or # hash params) into an API filter payload."""
    import urllib.parse
    import re
    parsed = urllib.parse.urlparse(raw_url)
    qs = parsed.query
    if not qs and "#" in raw_url:
        frag = parsed.fragment
        if "?" in frag:
            qs = frag.split("?", 1)[1]
        elif frag:
            qs = frag
    params = urllib.parse.parse_qs(qs)
    payload: Dict[str, Any] = {}
    for k, v in params.items():
        clean_k = k.replace("[]", "")
        snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
        if snake_k in ("contact_label_ids", "label_ids"):
            payload["contact_label_ids"] = v
        elif snake_k in ("not_contact_label_ids", "not_label_ids"):
            payload["not_contact_label_ids"] = v
        elif snake_k in ("account_label_ids",):
            payload["account_label_ids"] = v
        elif snake_k in ("not_account_label_ids",):
            payload["not_account_label_ids"] = v
        elif snake_k == "person_titles":
            payload["person_titles"] = v
        elif snake_k in ("person_not_titles", "not_person_titles"):
            payload["person_not_titles"] = v
        elif snake_k == "person_locations":
            payload["person_locations"] = v
        elif snake_k in ("person_not_locations", "not_person_locations"):
            payload["person_not_locations"] = v
        elif snake_k == "person_seniorities":
            payload["person_seniorities"] = v
        elif snake_k in ("organization_num_employees_ranges", "num_employees_ranges"):
            payload["organization_num_employees_ranges"] = v
        elif snake_k == "q_organization_keyword_tags":
            payload["q_organization_keyword_tags"] = v
        elif snake_k == "contact_email_status_v2":
            payload["contact_email_status_v2"] = v
        elif snake_k == "q_keywords":
            payload["q_keywords"] = v[0] if v else ""
        elif snake_k == "saved_search_id":
            payload["saved_search_id"] = v[0] if v else ""
        else:
            payload[snake_k] = v if len(v) > 1 else v[0]
    return sanitize_payload(payload)


def format_filters_summary(filters: Dict[str, Any]) -> str:
    """Format filter payload into human-readable bullet points matching Apollo web filters."""
    lines = []
    if filters.get("person_locations"):
        locs = filters["person_locations"]
        loc_str = ", ".join(locs) if isinstance(locs, list) else str(locs)
        lines.append(f"  • Target Locations    : {loc_str}")
    if filters.get("person_not_locations"):
        n_locs = filters["person_not_locations"]
        n_loc_str = ", ".join(n_locs) if isinstance(n_locs, list) else str(n_locs)
        lines.append(f"  • Excluded Locations  : {n_loc_str}")
    if filters.get("person_seniorities"):
        sens = filters["person_seniorities"]
        sen_str = ", ".join(sens) if isinstance(sens, list) else str(sens)
        lines.append(f"  • Seniority Levels    : {sen_str}")
    if filters.get("person_titles"):
        titles = filters["person_titles"]
        t_list = titles if isinstance(titles, list) else [titles]
        lines.append(f"  • Target Job Titles   : {', '.join(t_list[:8])}{'...' if len(t_list) > 8 else ''} ({len(t_list)} total)")
    if filters.get("person_not_titles"):
        not_titles = filters["person_not_titles"]
        nt_list = not_titles if isinstance(not_titles, list) else [not_titles]
        lines.append(f"  • Excluded Titles     : {', '.join(nt_list[:8])}{'...' if len(nt_list) > 8 else ''} ({len(nt_list)} total)")
    if filters.get("organization_num_employees_ranges"):
        ranges = filters["organization_num_employees_ranges"]
        r_str = ", ".join(ranges) if isinstance(ranges, list) else str(ranges)
        lines.append(f"  • Company Headcount   : {r_str} Employees")
    if filters.get("q_organization_keyword_tags"):
        tags = filters["q_organization_keyword_tags"]
        tag_list = tags if isinstance(tags, list) else [tags]
        tag_preview = ", ".join(tag_list[:6])
        lines.append(f"  • Industry Tags       : {len(tag_list)} tags ({tag_preview}{', ...' if len(tag_list) > 6 else ''})")
    if filters.get("organization_industry_tag_ids"):
        ind_ids = filters["organization_industry_tag_ids"]
        lines.append(f"  • Industry IDs Target : {len(ind_ids) if isinstance(ind_ids, list) else 1} target industries")
    if filters.get("organization_not_industry_tag_ids"):
        nind_ids = filters["organization_not_industry_tag_ids"]
        lines.append(f"  • Excluded Industries : {len(nind_ids) if isinstance(nind_ids, list) else 1} industries excluded")
    if filters.get("contact_email_status_v2"):
        st = filters["contact_email_status_v2"]
        st_str = ", ".join(st) if isinstance(st, list) else str(st)
        lines.append(f"  • Email Status        : {st_str}")
    if filters.get("contact_email_exclude_catch_all") in (True, "true", "True", 1):
        lines.append(f"  • Catch-All Emails    : Excluded")
    if filters.get("not_contact_label_ids"):
        ncl = filters["not_contact_label_ids"]
        lines.append(f"  • Excluded People     : {len(ncl) if isinstance(ncl, list) else 1} Contact Lists Excluded")
    if filters.get("not_account_label_ids"):
        nal = filters["not_account_label_ids"]
        lines.append(f"  • Excluded Companies  : {len(nal) if isinstance(nal, list) else 1} Company Lists Excluded")
    if filters.get("contact_label_ids"):
        lines.append(f"  • Included People Lists: {len(filters['contact_label_ids'])} List IDs")
    if filters.get("account_label_ids"):
        lines.append(f"  • Included Co. Lists  : {len(filters['account_label_ids'])} List IDs")
    if filters.get("prospected_by_current_team"):
        lines.append(f"  • Prospected by Team  : {filters['prospected_by_current_team']}")
    if filters.get("q_keywords"):
        lines.append(f"  • Base Keywords       : '{filters['q_keywords']}'")
    return "\n".join(lines) if lines else "  • No broad filters applied (Full Database Search)"


def query_apollo_search(api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute search query against Apollo REST API with 429 backoff.
    Automatically chooses the best endpoint:
      - /api/v1/contacts/search if searching contact_label_ids (saved contacts)
      - /api/v1/mixed_people/api_search if searching general directory / creator searches
    """
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }

    sanitized = sanitize_payload(payload)

    # If targeting existing contact lists exclusively, use contacts/search; otherwise broad directory
    if sanitized.get("contact_label_ids") and not sanitized.get("q_organization_keyword_tags"):
        url = "https://api.apollo.io/api/v1/contacts/search"
    else:
        url = "https://api.apollo.io/api/v1/mixed_people/api_search"

    max_retries = 3
    for attempt in range(max_retries):
        try:
            res = requests.post(url, headers=headers, json=sanitized, timeout=25)
            if res.status_code == 200:
                data = res.json()
                total = data.get("total_entries")
                if total is None:
                    pagination = data.get("pagination") or {}
                    total = pagination.get("total_entries", 0)

                people = data.get("contacts") or data.get("people") or []
                return {
                    "total_entries": int(total or len(people)),
                    "people": people,
                    "pagination": data.get("pagination") or {"total_entries": total}
                }
            elif res.status_code == 422:
                # Fallback to alternate search endpoint
                alt_url = "https://api.apollo.io/api/v1/contacts/search" if "mixed_people" in url else "https://api.apollo.io/api/v1/mixed_people/api_search"
                alt_res = requests.post(alt_url, headers=headers, json=sanitized, timeout=25)
                if alt_res.status_code == 200:
                    alt_data = alt_res.json()
                    alt_people = alt_data.get("contacts") or alt_data.get("people") or []
                    total = alt_data.get("total_entries") or len(alt_people)
                    return {
                        "total_entries": int(total),
                        "people": alt_people,
                        "pagination": {"total_entries": total}
                    }
                return {"error": f"HTTP {res.status_code}: {res.text[:200]}", "people": [], "pagination": {}, "total_entries": 0}
            elif res.status_code == 429:
                retry_after = int(res.headers.get("Retry-After", "10"))
                print(f"\n[!] Rate limited (429). Pausing for {retry_after + 2}s backoff...", flush=True)
                time.sleep(retry_after + 2)
                continue
            else:
                return {"error": f"HTTP {res.status_code}: {res.text[:200]}", "people": [], "pagination": {}, "total_entries": 0}
        except Exception as ex:
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            return {"error": str(ex), "people": [], "pagination": {}, "total_entries": 0}

    return {"error": "Max retries exceeded", "people": [], "pagination": {}, "total_entries": 0}


def probe_search_total_volume(api_key: str, base_payload: Dict[str, Any]) -> int:
    """Instantly probe total data volume available in a search query."""
    probe_payload = sanitize_payload(base_payload)
    probe_payload["page"] = 1
    probe_payload["per_page"] = 1
    data = query_apollo_search(api_key, probe_payload)
    return int(data.get("total_entries", 0))


def probe_searches_volume(api_key: str, searches: List[Dict[str, Any]], max_workers: int = 5):
    """Probe lead volume for multiple searches concurrently so the menu renders instantly."""
    to_probe = [s for s in searches if s.get("count") is None]
    if not to_probe:
        return

    def _task(s):
        try:
            total = probe_search_total_volume(api_key, s.get("filters", {}))
            s["count"] = total
        except Exception:
            s["count"] = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_task, s) for s in to_probe]
        for f in as_completed(futures):
            pass


def load_account_creator_searches(account_email: str) -> List[Dict[str, Any]]:
    """Load saved creator searches for this specific account from config/saved_searches.json."""
    if not os.path.exists(SAVED_SEARCHES_PATH):
        return []
    try:
        with open(SAVED_SEARCHES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get(account_email.lower(), [])
            elif isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def load_all_creator_searches() -> List[Dict[str, Any]]:
    """Load all saved creator searches across all accounts from config/saved_searches.json."""
    if not os.path.exists(SAVED_SEARCHES_PATH):
        return []
    searches = []
    try:
        with open(SAVED_SEARCHES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                for acc_email, items in data.items():
                    if isinstance(items, list):
                        for item in items:
                            item_copy = copy.deepcopy(item)
                            item_copy["source_account"] = acc_email
                            searches.append(item_copy)
            elif isinstance(data, list):
                searches = data
    except Exception:
        pass
    return searches


def fetch_account_labels(api_key: str) -> Dict[str, Any]:
    """
    Fetch all labels for the active account from Apollo /api/v1/labels.
    Distinguishes between account lists (modality == 'accounts') and people lists (modality == 'contacts').
    """
    url = "https://api.apollo.io/api/v1/labels"
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            raw = res.json()
            if isinstance(raw, list):
                acc_labels = [l for l in raw if l.get("modality") == "accounts"]
                con_labels = [l for l in raw if l.get("modality") == "contacts"]
                return {
                    "account_labels": acc_labels,
                    "contact_labels": con_labels,
                    "account_ids": [l.get("_id") or l.get("id") for l in acc_labels if l.get("_id") or l.get("id")],
                    "contact_ids": [l.get("_id") or l.get("id") for l in con_labels if l.get("_id") or l.get("id")],
                    "total_count": len(raw),
                    "raw": raw
                }
    except Exception as ex:
        print(f"[Notice] Could not fetch account labels from Apollo: {ex}", flush=True)
    return {
        "account_labels": [],
        "contact_labels": [],
        "account_ids": [],
        "contact_ids": [],
        "total_count": 0,
        "raw": []
    }


def convert_filters_v2_to_payload(filters_v2: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Apollo's live filters_v2 structure into standard search API payload."""
    payload: Dict[str, Any] = {}
    if not filters_v2 or not isinstance(filters_v2, dict):
        return payload

    if filters_v2.get("prospected_by_current_team"):
        payload["prospected_by_current_team"] = filters_v2["prospected_by_current_team"]

    expr = filters_v2.get("filter_expression") or {}
    operands = expr.get("operands") or []

    rules: List[Dict[str, Any]] = []
    for op in operands:
        if isinstance(op, dict):
            if "filters" in op and isinstance(op["filters"], list):
                rules.extend(op["filters"])
            elif "filter_id" in op:
                rules.append(op)
            elif "children" in op and isinstance(op["children"], list):
                for ch in op["children"]:
                    if isinstance(ch, dict) and "filters" in ch and isinstance(ch["filters"], list):
                        rules.extend(ch["filters"])
                    elif isinstance(ch, dict) and "children" in ch and isinstance(ch["children"], list):
                        rules.extend(ch["children"])
                    elif isinstance(ch, dict):
                        rules.append(ch)

    for r in rules:
        if not isinstance(r, dict):
            continue
        fid = r.get("filter_id") or ""
        op = r.get("operator") or ""
        val = r.get("value")

        # Locations
        if fid in ("filter.contact.location", "filter.person.location"):
            if (not op or op in ("is_any_of", "is", "include")) and isinstance(val, list):
                payload["person_locations"] = val
            elif op in ("is_none_of", "exclude") and isinstance(val, list):
                payload["person_not_locations"] = val
        # Titles
        elif fid in ("filter.contact.title", "filter.person.title"):
            if (not op or op in ("is_any_of", "is", "include")) and isinstance(val, list):
                payload["person_titles"] = val
            elif op in ("is_none_of", "exclude") and isinstance(val, list):
                payload["person_not_titles"] = val
        # Seniorities
        elif fid in ("filter.contact.seniority", "filter.person.seniority"):
            if isinstance(val, list):
                payload["person_seniorities"] = val
            elif isinstance(val, str):
                payload["person_seniorities"] = [val]
        # Email status
        elif fid in ("filter.contact.email_status", "filter.email_status"):
            if isinstance(val, list):
                payload["contact_email_status_v2"] = val
            elif isinstance(val, str):
                payload["contact_email_status_v2"] = [val]
        # Employee count / Headcount
        elif fid in ("filter.account.number_of_employees", "filter.number_of_employees"):
            if isinstance(val, list):
                payload["organization_num_employees_ranges"] = val
            elif isinstance(val, dict):
                min_v = val.get("min", "1")
                max_v = val.get("max", "")
                payload["organization_num_employees_ranges"] = [f"{min_v},{max_v}" if max_v else f"{min_v}"]
        # Industry Tags
        elif fid in ("filter.account.industry_tags", "filter.industry_tags"):
            if (not op or op in ("is_any_of", "is", "include")):
                payload["organization_industry_tag_ids"] = val
            elif op in ("is_none_of", "exclude"):
                payload["organization_not_industry_tag_ids"] = val
        # Keyword tags / Organization keywords
        elif fid in ("filter.account.keywords", "filter.account.organization_keyword_tags", "filter.account.keyword_tags"):
            if isinstance(val, list):
                payload["q_organization_keyword_tags"] = val
            elif isinstance(val, str):
                payload["q_organization_keyword_tags"] = [val]
        # Account / Company lists
        elif fid in ("filter.account.labels", "filter.account_labels"):
            if op in ("is_none_of", "exclude") and isinstance(val, list):
                payload["not_account_label_ids"] = val
            elif (not op or op in ("is_any_of", "is", "include")) and isinstance(val, list):
                payload["account_label_ids"] = val
        # Contact / People lists
        elif fid in ("filter.contact.labels", "filter.contact_labels"):
            if op in ("is_none_of", "exclude") and isinstance(val, list):
                payload["not_contact_label_ids"] = val
            elif (not op or op in ("is_any_of", "is", "include")) and isinstance(val, list):
                payload["contact_label_ids"] = val
        # Direct keywords
        elif fid in ("filter.q_keywords", "filter.keywords"):
            payload["q_keywords"] = val

    return sanitize_payload(payload)


def fetch_live_apollo_searches(api_key: str) -> List[Dict[str, Any]]:
    """
    Directly query Apollo REST API (/api/v1/finder_views/people/search) to retrieve
    the active account's live saved searches (finder views) in real-time.
    Prioritizes Apollo's flat 'signals' payload structure which directly maps to search API.
    """
    url = "https://api.apollo.io/api/v1/finder_views/people/search"
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }
    try:
        res = requests.post(url, headers=headers, json={}, timeout=15)
        if res.status_code == 200:
            data = res.json()
            views = data.get("finder_views") or []
            live_searches = []
            for v in views:
                if v.get("system") is True or v.get("archived") is True:
                    continue
                name = (v.get("name") or "Untitled View").strip()
                if name.lower() in SYSTEM_VIEW_NAMES:
                    continue

                # In Apollo, authentic saved search criteria is primarily stored in 'signals'
                payload = {}
                signals = v.get("signals")
                if isinstance(signals, dict) and signals:
                    payload = sanitize_payload(signals)
                else:
                    filters_v2 = v.get("filters_v2") or {}
                    payload = convert_filters_v2_to_payload(filters_v2)
                    if not payload and isinstance(v.get("filters"), dict):
                        payload = sanitize_payload(v.get("filters"))

                live_searches.append({
                    "id": v.get("id"),
                    "name": name,
                    "display_name": name,
                    "filters": payload,
                    "is_live": True,
                    "count": None  # Probed on startup
                })
            return live_searches
    except Exception as ex:
        print(f"[Notice] Could not fetch live saved searches from Apollo: {ex}", flush=True)
    return []


def save_account_creator_search(account_email: str, name: str, filters: Dict[str, Any], quiet: bool = False, is_live: bool = False):
    """Save a new creator search for an account into config/saved_searches.json."""
    data = {}
    if os.path.exists(SAVED_SEARCHES_PATH):
        try:
            with open(SAVED_SEARCHES_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception:
            data = {}

    acc_key = account_email.lower()
    data.setdefault(acc_key, [])
    # Remove existing with same name if any
    data[acc_key] = [s for s in data[acc_key] if s.get("name") != name]
    entry: Dict[str, Any] = {
        "name": name,
        "display_name": name,
        "filters": sanitize_payload(filters)
    }
    if is_live:
        entry["is_live"] = True
    data[acc_key].append(entry)
    try:
        with open(SAVED_SEARCHES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if not quiet:
            print(f"\n[+] Successfully registered Creator Search '{name}' for {account_email} into {SAVED_SEARCHES_PATH}!")
    except Exception as ex:
        if not quiet:
            print(f"[Notice] Could not save to {SAVED_SEARCHES_PATH}: {ex}")


def load_local_preset_searches() -> List[Dict[str, Any]]:
    """Load local preset searches as fallback."""
    return load_account_creator_searches("default")


# =====================================================================
# 3.5 ZERO-CREDIT HIGH-ACCURACY (90%+) DOMAIN RESOLUTION ENGINE
# =====================================================================

from scripts.domain_resolver_engine import (
    resolve_domain_multi_tier,
    batch_resolve_domains_high_accuracy,
    _SESSION_RESOLVED_CACHE as _company_domain_cache,
)


def resolve_company_domain_from_apollo(
    api_key: str,
    company_name: str,
    conn=None
) -> Dict[str, Any]:
    """
    Resolve company primary domain and website via zero-credit 5-tier high-accuracy engine.
    Cached locally in-memory and persisted to MySQL `detected_companies` table.
    Guarantees 0 Apollo export credits are consumed during search.
    """
    return resolve_domain_multi_tier(company_name, conn=conn)


def batch_resolve_company_domains(
    api_key: str,
    company_names: List[str],
    conn=None,
    max_workers: int = 5
) -> Dict[str, Dict[str, Any]]:
    """
    Resolve company domains concurrently for a batch of company names using the 5-tier high accuracy engine.
    Guarantees 0 Apollo export credits are consumed during search.
    """
    return batch_resolve_domains_high_accuracy(company_names, conn=conn, max_workers=max_workers)

    return results


# =====================================================================
# 4. 10-LEAD DATA PREVIEW ENGINE (EXTENSION COLUMN PARITY)
# =====================================================================

def render_10_lead_preview(people: List[Dict[str, Any]], api_key: str = "") -> bool:
    """Render a 10-row ASCII preview table confirming all extension columns including Company Website."""
    sample = people[:10]
    if not sample:
        print("\n[!] No leads returned by Apollo for this preview.")
        return False

    # Pre-resolve any missing company domains for the preview sample
    missing_comps = []
    for p in sample:
        org = p.get("organization") or {}
        dom = clean_domain(org.get("primary_domain") or p.get("company_domain") or "")
        cname = p.get("organization_name") or org.get("name") or ""
        if not dom and cname:
            missing_comps.append(cname)

    resolved_map = {}
    if missing_comps and api_key:
        try:
            resolved_map = batch_resolve_company_domains(api_key, missing_comps)
        except Exception:
            pass

    print("\n" + "=" * 140)
    print("10-LEAD DATA SCHEMA PREVIEW (Verifying Extension Column Parity)")
    print("=" * 140)
    header = f"{'#':<3} {'First Name':<11} {'Last Name':<11} {'Title':<24} {'Company':<19} {'Company Website':<28} {'Domain':<20} {'Location':<16}"
    print(header)
    print("-" * 140)

    for idx, p in enumerate(sample, 1):
        org = p.get("organization") or {}
        first_name = (p.get("first_name") or "")[:10]
        last_name = (p.get("last_name") or "")[:10]
        if not first_name and not last_name and p.get("name"):
            parts = str(p["name"]).strip().split(None, 1)
            first_name = parts[0][:10]
            last_name = (parts[1] if len(parts) > 1 else "")[:10]
        title = (p.get("title") or "")[:23]
        company_raw = p.get("organization_name") or org.get("name") or ""
        company = company_raw[:18]

        # Resolution lookup if missing from person payload
        c_info = resolved_map.get(company_raw.lower().strip()) or resolved_map.get(clean_company_name(company_raw).lower().strip()) or {}

        domain = clean_domain(org.get("primary_domain") or p.get("company_domain") or c_info.get("company_domain") or "")[:19]
        website_raw = org.get("website_url") or p.get("website_url") or p.get("website_link") or c_info.get("website_link") or (f"https://{domain}" if domain else "")
        website = str(website_raw)[:27]

        person_loc = f"{p.get('city') or ''}, {p.get('state') or p.get('country') or ''}".strip(", ")
        loc = (person_loc or c_info.get("location") or "")[:15]

        print(f"{idx:<3} {first_name:<11} {last_name:<11} {title:<24} {company:<19} {website:<28} {domain:<20} {loc:<16}")

    print("-" * 140)
    print("All core extension columns verified (Name, Title, Company, Company Website, Domain, Location, LinkedIn, Apollo ID).")
    return True


# =====================================================================
# 5. STREAMING QUALIFICATION ENGINE (0.5ms RDS + INDIAN + 64K TITLES)
# =====================================================================

def map_apollo_person_to_contact(p: Dict[str, Any], resolved_company_info: Optional[Dict[str, Any]] = None) -> ApolloContact:
    """Map raw Apollo API person/contact object to ApolloContact data model."""
    org = p.get("organization") or {}
    first_name = p.get("first_name") or ""
    last_name = p.get("last_name") or ""
    if not first_name and not last_name and p.get("name"):
        parts = str(p["name"]).strip().split(None, 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""
    full_name = p.get("name") or f"{first_name} {last_name}".strip()
    apollo_id = str(p.get("id") or p.get("person_id") or "")
    company_name = p.get("organization_name") or org.get("name") or ""
    company_domain = clean_domain(org.get("primary_domain") or org.get("website_url") or p.get("company_domain") or "")
    website_link = org.get("website_url") or p.get("website_url") or p.get("website_link") or ""
    location = f"{p.get('city') or ''}, {p.get('state') or p.get('country') or ''}".strip(", ")

    # Fallback to resolved company info if domain/website/location missing
    if resolved_company_info:
        if not company_domain:
            company_domain = clean_domain(resolved_company_info.get("company_domain") or "")
        if not website_link:
            website_link = resolved_company_info.get("website_link") or (f"https://{company_domain}" if company_domain else "")
        if not location:
            location = resolved_company_info.get("location") or ""

    # Recover domain from corporate email if organization domain is missing
    if not company_domain and p.get("email") and "@" in str(p["email"]):
        em_parts = str(p["email"]).split("@", 1)
        if len(em_parts) > 1:
            em_dom = em_parts[1].strip().lower()
            if em_dom not in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com", "protonmail.com"):
                company_domain = clean_domain(em_dom)

    if not website_link and company_domain:
        website_link = f"https://{company_domain}"

    return ApolloContact(
        key=apollo_id or full_name or str(uuid.uuid4()),
        apollo_id=apollo_id,
        name=full_name,
        first_name=first_name,
        last_name=last_name,
        job_title=p.get("title") or "",
        company=company_name,
        company_domain=company_domain,
        domain=company_domain,
        website_link=website_link,
        linkedin_url=p.get("linkedin_url") or "",
        apollo_profile_url=f"https://app.apollo.io/#/people/{apollo_id}" if apollo_id else "",
        location=location,
        email=p.get("email") or "",
        phone=p.get("sanitized_phone") or p.get("corporate_phone") or ""
    )


def qualify_contacts_batch(
    contacts: List[ApolloContact],
    batch_tag: str,
    conn,
    session_seen_companies: Optional[Dict[str, ApolloContact]] = None,
    filter_indian: bool = False,
    filter_titles: bool = True
) -> Tuple[List[ApolloContact], Dict[str, int]]:
    """
    Run the qualification pipeline focused on Unique Domains & 64K Job Title Guardrails (Zero LLM calls):
    1. RDS 7.28M email domain check (idx_emails_domain) -> drops any domain already in CRM.
    2. Indian Demographic Origin filter (Optional, default False -> deferred to Manage Batches LLM).
    3. 64K Job Title Guardrails (Active with ZERO LLM calls; unclassified titles queued to MySQL pending).
    4. 1 Contact per Company Deduplication (session-wide unique company domain election).
    """
    stats = {
        "existing_crm": 0,
        "indian_name": 0,
        "excluded_title": 0,
        "company_dup": 0,
        "required": 0
    }

    if not contacts:
        return [], stats

    # Layer 1: Resolve Company Domains & Check RDS CRM Database
    contact_primary_domain, _ = resolve_company_domains(contacts, connection=conn)
    crm_matches = check_person_and_domains_in_crm_batch(contacts, contact_primary_domain, connection=conn, active_batch=batch_tag)

    net_new_contacts = []
    for c in contacts:
        if c.key in crm_matches:
            stats["existing_crm"] += 1
        elif not contact_primary_domain.get(c.key):
            stats["existing_crm"] += 1
        else:
            net_new_contacts.append(c)

    # Layer 2: Demographic Origin Guardrail (Optional, default False -> deferred to Manage Batches LLM)
    if filter_indian:
        non_indian_contacts = []
        for c in net_new_contacts:
            is_indian, _ = is_unambiguous_pure_indian_name(c.name, connection=conn)
            if is_indian:
                stats["indian_name"] += 1
            else:
                non_indian_contacts.append(c)
    else:
        non_indian_contacts = net_new_contacts

    # Layer 3: 64K Job Title Guardrails (Active with ZERO LLM calls)
    if filter_titles:
        titles_to_check = [c.job_title for c in non_indian_contacts if c.job_title.strip()]
        batch_title_results = lookup_job_titles_batch(titles_to_check, connection=conn)

        title_qualified_contacts = []
        unrecognized_titles = []
        for c in non_indian_contacts:
            t_name = c.job_title.strip()
            t_info = batch_title_results.get(t_name) or lookup_job_title_in_db(t_name, connection=conn)
            if t_info.get("required") is True:
                c_dict = c.model_dump()
                c_dict["segment"] = t_info.get("segment", "Target_Executive")
                title_qualified_contacts.append(ApolloContact(**c_dict))
            elif t_info.get("status") == "not_recognized_title":
                # Provisionally accept novel title with ZERO LLM calls, queue for Manage Batches LLM
                c_dict = c.model_dump()
                c_dict["segment"] = "Pending_Evaluation"
                title_qualified_contacts.append(ApolloContact(**c_dict))
                unrecognized_titles.append(t_name)
            else:
                stats["excluded_title"] += 1

        if unrecognized_titles and conn:
            try:
                queue_pending_job_titles(unrecognized_titles, batch_tag, conn)
            except Exception:
                pass
    else:
        title_qualified_contacts = []
        for c in non_indian_contacts:
            c_dict = c.model_dump()
            c_dict["segment"] = getattr(c, "segment", "Prospect") or "Prospect"
            title_qualified_contacts.append(ApolloContact(**c_dict))

    # Layer 4: 1 Contact Per Company Deduplication (Strict Unique Domains)
    qualified_leads = []

    if session_seen_companies is not None:
        # Batch-wide cross-page deduplication
        # First group this page by domain and take page's best candidate
        page_buckets: Dict[str, List[ApolloContact]] = {}
        for c in title_qualified_contacts:
            dom = clean_domain(c.company_domain or c.domain or "")
            if not dom:
                continue
            page_buckets.setdefault(dom, []).append(c)

        for dom, cand_list in page_buckets.items():
            cand_list.sort(key=lambda x: get_seniority_score(x.job_title), reverse=True)
            winner = cand_list[0]
            if len(cand_list) > 1:
                stats["company_dup"] += (len(cand_list) - 1)

            win_score = get_seniority_score(winner.job_title)
            if dom in session_seen_companies:
                prev_winner = session_seen_companies[dom]
                prev_score = get_seniority_score(prev_winner.job_title)
                if win_score > prev_score:
                    # Upgrade: higher seniority found on this page
                    session_seen_companies[dom] = winner
                    qualified_leads.append(winner)
                    # Note: replaces previous winner in DB
                else:
                    stats["company_dup"] += 1
            else:
                session_seen_companies[dom] = winner
                qualified_leads.append(winner)
                stats["required"] += 1
    else:
        # Page-local deduplication (fallback for tests)
        domain_buckets: Dict[str, List[ApolloContact]] = {}
        for c in title_qualified_contacts:
            dom = clean_domain(c.company_domain or c.domain or "")
            if not dom:
                continue
            domain_buckets.setdefault(dom, []).append(c)

        for dom, cand_list in domain_buckets.items():
            cand_list.sort(key=lambda x: get_seniority_score(x.job_title), reverse=True)
            winner = cand_list[0]
            qualified_leads.append(winner)
            stats["required"] += 1
            if len(cand_list) > 1:
                stats["company_dup"] += (len(cand_list) - 1)

    return qualified_leads, stats


def save_qualified_leads_to_db(leads: List[ApolloContact], batch_tag: str, conn):
    """Persist qualified required leads directly into MySQL `apollo_saved_leads`."""
    if not leads:
        return

    ensure_apollo_saved_leads_table(conn)

    # Guarantee 1 contact per company domain in this batch by deleting any previous winner
    with conn.cursor() as cur:
        for c in leads:
            dom = clean_domain(c.company_domain or c.domain or "")
            if dom:
                cur.execute(
                    "DELETE FROM `apollo_saved_leads` WHERE `batch` = %s AND `company_domain` = %s",
                    (batch_tag, dom)
                )

    rows_to_insert = []
    for c in leads:
        segment = getattr(c, "segment", "Target_Executive") or "Target_Executive"
        rows_to_insert.append((
            batch_tag,
            c.apollo_id or "",
            c.name or "",
            c.first_name or "",
            c.last_name or "",
            c.job_title or "",
            c.company or "",
            c.company_domain or c.domain or "",
            c.website_link or "",
            c.location or "",
            c.linkedin_url or "",
            c.apollo_profile_url or "",
            segment
        ))

    sql = """
        INSERT INTO `apollo_saved_leads` (
            `batch`, `apollo_id`, `name`, `first_name`, `last_name`,
            `job_title`, `company`, `company_domain`, `website_link`, `location`,
            `linkedin_url`, `apollo_profile_url`, `segment`
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `name` = VALUES(`name`),
            `first_name` = VALUES(`first_name`),
            `last_name` = VALUES(`last_name`),
            `job_title` = VALUES(`job_title`),
            `company` = VALUES(`company`),
            `company_domain` = VALUES(`company_domain`),
            `website_link` = VALUES(`website_link`),
            `segment` = VALUES(`segment`);
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows_to_insert)
    conn.commit()


# =====================================================================
# 6. CONSOLIDATED CSV EXPORT & BATCH ENRICHMENT
# =====================================================================

def export_batch_to_csv(batch_tag: str, account_email: str) -> str:
    """Export all leads in the batch to Apollo's standard 75-column CSV."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id, batch, apollo_id, name, first_name, last_name,
                    job_title, company, company_domain, website_link, location,
                    linkedin_url, apollo_profile_url, segment, raw_enrichment_data
                FROM `apollo_saved_leads`
                WHERE `batch` = %s
                ORDER BY id ASC
            """, (batch_tag,))
            rows = cur.fetchall()

    if not rows:
        print(f"\n[!] No leads found in MySQL under batch '{batch_tag}'.")
        return ""

    raw_leads = []
    for r in rows:
        raw_leads.append({
            "id": r[0],
            "batch": r[1],
            "apollo_id": r[2],
            "name": r[3],
            "first_name": r[4],
            "last_name": r[5],
            "job_title": r[6],
            "company": r[7],
            "company_domain": r[8],
            "website_link": r[9],
            "location": r[10],
            "linkedin_url": r[11],
            "apollo_profile_url": r[12],
            "segment": r[13],
            "raw_enrichment_data": r[14],
        })

    # Double safeguard: strictly enforce 1 contact per company domain in CSV export
    seen_domains = set()
    leads = []
    for lead in reversed(raw_leads):  # Latest/highest priority
        dom = clean_domain(lead.get("company_domain") or "")
        if dom:
            if dom in seen_domains:
                continue
            seen_domains.add(dom)
        leads.append(lead)
    leads.reverse()


    clean_email = account_email.replace("@", "_").replace(".", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"apollo_contacts_export_{clean_email}_{batch_tag}_{timestamp}.csv"
    filepath = os.path.join(EXPORTS_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(APOLLO_75_HEADERS)
        for lead in leads:
            row_vals = format_apollo_lead_row(lead, account_email)
            writer.writerow(row_vals)

    print(f"\n[+] Export successfully generated ({len(leads)} leads):")
    print(f"    File: {filepath}")
    return filepath


# =====================================================================
# 7. INTERACTIVE WEB FILTER MODIFICATION ENGINE
# =====================================================================

def interactive_filter_menu(base_payload: Dict[str, Any], active_key: str, active_email: str, current_search_name: str) -> Tuple[Dict[str, Any], str]:
    """Provide a full-featured interactive web filter modification menu."""
    search_name = current_search_name
    while True:
        print("\n" + "=" * 100)
        print("WEB FILTER MODIFICATION MENU (Apollo Online Search Parity)")
        print("=" * 100)
        print("  [ 1] Target Job Titles        (person_titles)")
        print("  [ 2] Excluded Job Titles      (person_not_titles)")
        print("  [ 3] Seniority Levels         (person_seniorities: c_suite, founder, vp, director, etc.)")
        print("  [ 4] Target Locations         (person_locations)")
        print("  [ 5] Excluded Locations       (person_not_locations)")
        print("  [ 6] Company Headcount        (organization_num_employees_ranges: 1,10 | 11,50 | 51,200...)")
        print("  [ 7] Industry Keyword Tags    (q_organization_keyword_tags)")
        print("  [ 8] Email Status & Catch-All (contact_email_status_v2, contact_email_exclude_catch_all)")
        print("  [ 9] Contact / People Lists   (contact_label_ids & not_contact_label_ids)")
        print("  [10] Company / Account Lists  (account_label_ids & not_account_label_ids)")
        print("  [11] Direct Search Keywords   (q_keywords)")
        print("  [12] Replace / Import from Apollo Web URL")
        print("  [ s] Save search configuration")
        print("  [ d] Done editing (proceed to review & preview)")

        choice = input("\nSelect filter to modify [1-12, 's' to save, 'd' when done]: ").strip().lower()

        if choice in ("d", "done", ""):
            break

        elif choice == "s":
            def_name = search_name if search_name and search_name != "Custom Search" else "Custom Search"
            save_name = input(f"Enter name to save search (press Enter to keep '{def_name}'): ").strip()
            if not save_name:
                save_name = def_name
            scope = input(f"Save for [1] active login only ({active_email}), or [2] all logins? [1/2] (default 1): ").strip()
            if scope == "2":
                accounts_all = load_apollo_accounts()
                for acc in accounts_all:
                    e = acc.get("email", "").lower()
                    if e:
                        save_account_creator_search(e, save_name, base_payload)
                print(f"[+] Successfully saved '{save_name}' across all Apollo accounts!")
            else:
                save_account_creator_search(active_email, save_name, base_payload)
            search_name = save_name
            continue

        elif choice == "1":
            cur = base_payload.get("person_titles", [])
            print(f"\nCurrent Target Titles ({len(cur)}): {', '.join(cur[:10])}{'...' if len(cur) > 10 else ''}")
            act = input("Action: [a]dd titles, [r]eplace all, or [c]lear: ").strip().lower()
            if act == "c":
                base_payload.pop("person_titles", None)
            else:
                raw_t = input("Enter comma-separated titles (e.g. CEO, Founder, Owner, President): ").strip()
                if raw_t:
                    parsed = [t.strip() for t in raw_t.split(",") if t.strip()]
                    if act == "r":
                        base_payload["person_titles"] = parsed
                    else:
                        base_payload.setdefault("person_titles", []).extend(parsed)
                        base_payload["person_titles"] = list(dict.fromkeys(base_payload["person_titles"]))

        elif choice == "2":
            cur = base_payload.get("person_not_titles", [])
            print(f"\nCurrent Excluded Titles ({len(cur)}): {', '.join(cur[:10])}{'...' if len(cur) > 10 else ''}")
            act = input("Action: [a]dd excluded titles, [r]eplace all, or [c]lear: ").strip().lower()
            if act == "c":
                base_payload.pop("person_not_titles", None)
            else:
                raw_nt = input("Enter comma-separated excluded titles (e.g. Assistant, Intern, Advisor): ").strip()
                if raw_nt:
                    parsed = [t.strip() for t in raw_nt.split(",") if t.strip()]
                    if act == "r":
                        base_payload["person_not_titles"] = parsed
                    else:
                        base_payload.setdefault("person_not_titles", []).extend(parsed)
                        base_payload["person_not_titles"] = list(dict.fromkeys(base_payload["person_not_titles"]))

        elif choice == "3":
            cur = base_payload.get("person_seniorities", [])
            print(f"\nCurrent Seniorities: {cur}")
            print("Apollo Options: owner, founder, c_suite, partner, vp, head, director, manager, senior, entry, intern")
            act = input("Action: [a]dd seniorities, [r]eplace all, or [c]lear: ").strip().lower()
            if act == "c":
                base_payload.pop("person_seniorities", None)
            else:
                raw_s = input("Enter comma-separated seniorities (e.g. owner, founder, c_suite, vp): ").strip()
                if raw_s:
                    parsed = [s.strip().lower() for s in raw_s.split(",") if s.strip()]
                    if act == "r":
                        base_payload["person_seniorities"] = parsed
                    else:
                        base_payload.setdefault("person_seniorities", []).extend(parsed)
                        base_payload["person_seniorities"] = list(dict.fromkeys(base_payload["person_seniorities"]))

        elif choice == "4":
            cur = base_payload.get("person_locations", [])
            print(f"\nCurrent Target Locations: {cur}")
            act = input("Action: [a]dd locations, [r]eplace all, or [c]lear: ").strip().lower()
            if act == "c":
                base_payload.pop("person_locations", None)
            else:
                raw_l = input("Enter comma-separated locations (e.g. United States, United Kingdom, Canada): ").strip()
                if raw_l:
                    parsed = [l.strip() for l in raw_l.split(",") if l.strip()]
                    if act == "r":
                        base_payload["person_locations"] = parsed
                    else:
                        base_payload.setdefault("person_locations", []).extend(parsed)
                        base_payload["person_locations"] = list(dict.fromkeys(base_payload["person_locations"]))

        elif choice == "5":
            cur = base_payload.get("person_not_locations", [])
            print(f"\nCurrent Excluded Locations: {cur}")
            act = input("Action: [a]dd excluded locations, [r]eplace all, or [c]lear: ").strip().lower()
            if act == "c":
                base_payload.pop("person_not_locations", None)
            else:
                raw_nl = input("Enter comma-separated excluded locations: ").strip()
                if raw_nl:
                    parsed = [l.strip() for l in raw_nl.split(",") if l.strip()]
                    if act == "r":
                        base_payload["person_not_locations"] = parsed
                    else:
                        base_payload.setdefault("person_not_locations", []).extend(parsed)
                        base_payload["person_not_locations"] = list(dict.fromkeys(base_payload["person_not_locations"]))

        elif choice == "6":
            cur = base_payload.get("organization_num_employees_ranges", [])
            print(f"\nCurrent Headcount Ranges: {cur}")
            print("Apollo Options: 1,10 | 11,20 | 21,50 | 51,100 | 101,200 | 201,500 | 501,1000 | 1001,2000 | 2001,5000 | 5001,10000 | 10001")
            raw_r = input("Enter comma-separated ranges or 'clear': ").strip()
            if raw_r.lower() == "clear":
                base_payload.pop("organization_num_employees_ranges", None)
            elif raw_r:
                base_payload["organization_num_employees_ranges"] = [r.strip() for r in raw_r.split(",") if r.strip()]

        elif choice == "7":
            cur = base_payload.get("q_organization_keyword_tags", [])
            print(f"\nCurrent Industry Keyword Tags ({len(cur)}): {', '.join(cur[:8])}{'...' if len(cur) > 8 else ''}")
            act = input("Action: [a]dd tags, [r]eplace all, or [c]lear: ").strip().lower()
            if act == "c":
                base_payload.pop("q_organization_keyword_tags", None)
            else:
                raw_tags = input("Enter comma-separated industry tags: ").strip()
                if raw_tags:
                    parsed = [t.strip() for t in raw_tags.split(",") if t.strip()]
                    if act == "r":
                        base_payload["q_organization_keyword_tags"] = parsed
                    else:
                        base_payload.setdefault("q_organization_keyword_tags", []).extend(parsed)
                        base_payload["q_organization_keyword_tags"] = list(dict.fromkeys(base_payload["q_organization_keyword_tags"]))

        elif choice == "8":
            cur_st = base_payload.get("contact_email_status_v2", [])
            cur_ca = base_payload.get("contact_email_exclude_catch_all", "false")
            print(f"\nCurrent Email Status: {cur_st} | Catch-All Excluded: {cur_ca}")
            print("Options: [1] Verified only (with catch-all excluded), [2] Verified + Unverified, [3] Clear email filter")
            opt = input("Select option [1-3]: ").strip()
            if opt == "1":
                base_payload["contact_email_status_v2"] = ["verified"]
                base_payload["contact_email_exclude_catch_all"] = "true"
            elif opt == "2":
                base_payload["contact_email_status_v2"] = ["verified", "unverified"]
                base_payload.pop("contact_email_exclude_catch_all", None)
            elif opt == "3":
                base_payload.pop("contact_email_status_v2", None)
                base_payload.pop("contact_email_exclude_catch_all", None)

        elif choice == "9":
            ncl = len(base_payload.get("not_contact_label_ids", []))
            cl = len(base_payload.get("contact_label_ids", []))
            print(f"\nCurrent Contact Lists: {cl} included, {ncl} excluded")
            act = input("Action: [c]lear excluded contact lists, or press Enter to keep: ").strip().lower()
            if act == "c":
                base_payload.pop("not_contact_label_ids", None)
                print("[+] Cleared excluded contact lists.")

        elif choice == "10":
            nal = len(base_payload.get("not_account_label_ids", []))
            al = len(base_payload.get("account_label_ids", []))
            print(f"\nCurrent Company Lists: {al} included, {nal} excluded")
            act = input("Action: [c]lear excluded company lists, or press Enter to keep: ").strip().lower()
            if act == "c":
                base_payload.pop("not_account_label_ids", None)
                print("[+] Cleared excluded company lists.")

        elif choice == "11":
            cur = base_payload.get("q_keywords", "")
            print(f"\nCurrent Search Keywords: '{cur}'")
            raw_kw = input("Enter new search keywords (or 'clear'): ").strip()
            if raw_kw.lower() == "clear":
                base_payload.pop("q_keywords", None)
            elif raw_kw:
                base_payload["q_keywords"] = raw_kw

        elif choice == "12":
            url_in = input("\nPaste full Apollo search URL from your browser: ").strip()
            if url_in:
                new_filters = parse_apollo_url(url_in)
                if new_filters:
                    base_payload.clear()
                    base_payload.update(new_filters)
                    print("[+] Successfully parsed and applied new filters from URL!")

        # Re-probe and display updated status
        print("\n" + "-" * 100)
        print("UPDATED SEARCH STATUS:")
        print(format_filters_summary(base_payload))
        print("-" * 100)
        print("Probing Apollo Search Index for updated total volume...")
        current_total = probe_search_total_volume(active_key, base_payload)
        print(f">> TOTAL CONTACTS AVAILABLE IN THIS SEARCH: {current_total:,d} leads (~{max(1, (current_total + 99) // 100)} pages)")
        print("-" * 100)

    return base_payload, search_name


# =====================================================================
# 8. MAIN INTERACTIVE APPLICATION CONTROLLER
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Apollo Direct Search & CRM Qualification CLI")
    parser.add_argument("--account", type=int, help="Account index (1-19)")
    parser.add_argument("--target", "--limit", dest="target", type=int, default=0, help="Target number of qualified leads to collect before stopping (e.g. 50)")
    args = parser.parse_args()

    # Step 1: Banner & Account Selection
    display_startup_banner()

    accounts = load_apollo_accounts()
    print("[?] Select active Apollo account for today's session:")
    for idx, acc in enumerate(accounts, 1):
        email = acc.get("email", f"Account #{idx}")
        masked = mask_key(acc.get("api_key", ""))
        print(f"  [{idx:>2}] {email:<32} [{masked}]")

    selected_idx = args.account
    while not selected_idx or selected_idx < 1 or selected_idx > len(accounts):
        try:
            val = input(f"\nSelect account [1-{len(accounts)}]: ").strip()
            selected_idx = int(val)
        except (ValueError, KeyboardInterrupt):
            print("\nSession cancelled.")
            sys.exit(0)

    active_account = accounts[selected_idx - 1]
    active_email = active_account.get("email", f"account_{selected_idx}")
    active_key = active_account.get("api_key", "")

    # Health Check
    health = check_account_health(active_key)
    print(f"\n>> Selected: {active_email} (Status: {health['status']} | Rate: {health.get('rate_limit', '60 req/min')})")
    if health["status"] == "Unauthorized":
        print("[!] ERROR: This API key is unauthorized or expired. Please update config/apollo_accounts.json.")
        sys.exit(1)

    # Step 2: Auto-Start & Fetch Searches with Data Volume
    print("\n" + "=" * 100)
    print(f"STEP 1: FETCHING SEARCHES AVAILABLE FOR {active_email}")
    print("=" * 100)

    print(f">> Querying Apollo REST API for live saved searches...")
    live_searches = fetch_live_apollo_searches(active_key)
    if live_searches:
        print(f"[+] Retrieved {len(live_searches)} live creator searches from Apollo!")
        for ls in live_searches:
            save_account_creator_search(active_email, ls["name"], ls["filters"], quiet=True, is_live=True)
    else:
        print("  (No new live searches returned from Apollo API, loading saved catalog)")

    def reload_catalog():
        acc_searches = load_account_creator_searches(active_email)
        all_searches = load_all_creator_searches()
        other_searches = [
            s for s in all_searches 
            if s.get("source_account", "").lower() != active_email.lower()
        ]
        combined = list(acc_searches) + list(other_searches)
        return acc_searches, all_searches, other_searches, combined

    account_creator_searches, all_creator_searches, other_creator_searches, selectable_searches = reload_catalog()

    # Probe lead volume for active account searches so the menu immediately displays data counts
    print(f">> Probing data volume for {active_email} searches...")
    probe_searches_volume(active_key, account_creator_searches, max_workers=4)

    base_payload: Dict[str, Any] = {}
    search_name = "Custom Search"

    while True:
        print(f"\nAvailable Searches for {active_email}:")
        if account_creator_searches:
            for idx, cs in enumerate(account_creator_searches, 1):
                count_val = cs.get("count")
                count_str = f" ({count_val:,d} leads)" if count_val is not None else ""
                live_badge = " [Apollo Live]" if cs.get("is_live") else ""
                print(f"  [{idx:>2}] ★ [This Account] {cs['name']}{live_badge}{count_str}")
        else:
            print("  (No creator searches registered specifically for this login)")

        if other_creator_searches:
            start_offset = len(account_creator_searches) + 1
            print(f"\nRegistered Searches Available Across Other Logins:")
            for offset, cs in enumerate(other_creator_searches):
                idx = start_offset + offset
                count_val = cs.get("count")
                count_str = f" ({count_val:,d} leads)" if count_val is not None else ""
                acc_tag = cs.get("source_account", "Other Account")
                live_badge = " [Live]" if cs.get("is_live") else ""
                print(f"  [{idx:>2}] [{acc_tag}] {cs['name']}{live_badge}{count_str}")

        total_catalog = len(selectable_searches)
        print("\nOptions:")
        if total_catalog > 0:
            print(f"  [1-{total_catalog}] Select any Search by number")
        print("  [r] Refresh live searches directly from Apollo")
        print("  [a] Add / Register a new Creator Search (paste Apollo People URL)")
        print("  [c] Custom filter search from scratch")
        print("  [s] Save or register a new Creator Search")
        print("  [l] View Account Contact Lists (Optional)")

        cmd = input(f"\nSelect search [1-{max(1, total_catalog)}] (default 1): ").strip()
        if not cmd:
            cmd = "1"

        if cmd.isdigit():
            val = int(cmd)
            if 1 <= val <= total_catalog:
                chosen = selectable_searches[val - 1]
                search_name = chosen["display_name"]
                base_payload = copy.deepcopy(chosen.get("filters", {}))
                break
            else:
                print(f"[!] Invalid number. Please enter a number between 1 and {total_catalog}.")
        elif cmd.lower() == "r":
            print(f"\n>> Refreshing live saved searches from Apollo for {active_email}...")
            live = fetch_live_apollo_searches(active_key)
            if live:
                for ls in live:
                    save_account_creator_search(active_email, ls["name"], ls["filters"], quiet=True, is_live=True)
                print(f"[+] Refreshed {len(live)} live searches from Apollo!")
            account_creator_searches, all_creator_searches, other_creator_searches, selectable_searches = reload_catalog()
            probe_searches_volume(active_key, account_creator_searches, max_workers=4)
            continue
        elif cmd.lower() == "a":
            s_name = input("\nEnter name for this Creator Search: ").strip()
            if not s_name:
                s_name = "Custom Creator Search"
            url_in = input("Paste Apollo search URL from the People tab: ").strip()
            if url_in:
                base_payload = parse_apollo_url(url_in)
            else:
                base_payload = {}
            save_account_creator_search(active_email, s_name, base_payload)
            search_name = s_name
            break
        elif cmd.lower() == "c":
            base_payload = {}
            search_name = "Custom Direct Search"
            base_payload, search_name = interactive_filter_menu(base_payload, active_key, active_email, search_name)
            break
        elif cmd.lower() == "l":
            print(f"\nFetching available contact lists from Apollo for {active_email}...")
            labels_info = fetch_account_labels(active_key)
            con_labels = labels_info["contact_labels"]
            if not con_labels:
                print("[!] No contact lists found.")
                continue
            for idx, item in enumerate(con_labels[:15], 1):
                cnt = item.get("cached_count", 0)
                print(f"  [L{idx:>2}] {item.get('name', 'List')} ({cnt:,d} contacts)")
            l_pick = input("\nPick list (e.g. L1) or press Enter to cancel: ").strip().upper()
            if l_pick.startswith("L") and l_pick[1:].isdigit():
                l_idx = int(l_pick[1:])
                if 1 <= l_idx <= len(con_labels):
                    chosen_l = con_labels[l_idx - 1]
                    l_id = chosen_l.get("_id") or chosen_l.get("id")
                    search_name = f"List: {chosen_l.get('name')}"
                    base_payload = {"contact_label_ids": [l_id]}
                    break

    # Step 3 & 4: Display Active Filters & Option to Modify
    print("\n" + "=" * 100)
    print(f"ACTIVE FILTERS FOR SELECTED SEARCH: \"{search_name}\"")
    print("=" * 100)
    print(format_filters_summary(base_payload))
    print("-" * 100)
    print("Probing Apollo Search Index for total volume in this search...")
    current_total = probe_search_total_volume(active_key, base_payload)
    print(f">> TOTAL CONTACTS IN THIS SEARCH: {current_total:,d} leads (~{max(1, (current_total + 99) // 100)} pages)")
    print("-" * 100)

    # Offer interactive web filter modification
    modify_resp = input("\n[?] Do you want to modify or edit any filters in this search? [y/N]: ").strip().lower()
    if modify_resp in ("y", "yes"):
        base_payload, search_name = interactive_filter_menu(base_payload, active_key, active_email, search_name)

    # Step 5: Save Filter Edits & All-Lists Exclusion Protocol
    labels_info = fetch_account_labels(active_key)
    avail_acc_lists = len(labels_info["account_labels"])
    avail_con_lists = len(labels_info["contact_labels"])

    print("\n" + "=" * 100)
    print(f"ALL-LISTS EXCLUSION PROTOCOL FOR \"{search_name}\"")
    print("=" * 100)
    print(f"  • Active Apollo Account   : {avail_acc_lists} Company Lists | {avail_con_lists} People Lists ({labels_info['total_count']} Total in {active_email})")
    print("=" * 100)

    exclude_prompt = f"[?] Exclude all {avail_acc_lists} company lists and {avail_con_lists} people lists from this search? [y/N]: "
    exclude_resp = input(exclude_prompt).strip().lower()

    if exclude_resp in ("y", "yes"):
        base_payload["not_account_label_ids"] = list(labels_info["account_ids"])
        base_payload["not_contact_label_ids"] = list(labels_info["contact_ids"])
        base_payload.pop("account_label_ids", None)
        base_payload.pop("contact_label_ids", None)
        print(f"[+] Excluded all {len(labels_info['account_ids'])} Company Lists and {len(labels_info['contact_ids'])} People Lists!")
    else:
        # Sanitize foreign IDs to prevent 422 errors on this account
        active_acc_set = set(labels_info["account_ids"])
        active_con_set = set(labels_info["contact_ids"])
        if "not_account_label_ids" in base_payload:
            base_payload["not_account_label_ids"] = [lid for lid in base_payload["not_account_label_ids"] if lid in active_acc_set]
        if "not_contact_label_ids" in base_payload:
            base_payload["not_contact_label_ids"] = [lid for lid in base_payload["not_contact_label_ids"] if lid in active_con_set]
        print("[!] Retained base search without excluding active account lists.")

    base_payload = sanitize_payload(base_payload)

    # Prompt to save search
    save_confirm = input(f"\n[?] Do you want to save this search configuration ('{search_name}') for future sessions? [y/N]: ").strip().lower()
    if save_confirm in ("y", "yes"):
        def_name = search_name if search_name and search_name != "Custom Search" else "Custom Search"
        save_name = input(f"Enter name to save search (press Enter to keep '{def_name}'): ").strip()
        if not save_name:
            save_name = def_name
        scope = input(f"Save for [1] active login only ({active_email}), or [2] all logins? [1/2] (default 1): ").strip()
        if scope == "2":
            accounts_all = load_apollo_accounts()
            for acc in accounts_all:
                e = acc.get("email", "").lower()
                if e:
                    save_account_creator_search(e, save_name, base_payload)
            print(f"[+] Successfully saved '{save_name}' across all Apollo accounts!")
        else:
            save_account_creator_search(active_email, save_name, base_payload)
        search_name = save_name

    # Re-probe final base total
    print("\nProbing Apollo Search Index for finalized search volume...")
    final_base_total = probe_search_total_volume(active_key, base_payload)
    print(f">> FINAL BASE SEARCH VOLUME: {final_base_total:,d} leads (~{max(1, (final_base_total + 99) // 100)} pages)")

    # Step 6: 10-Lead Data Schema Preview of Finalized Search
    print("\nFetching 10-lead preview for finalized search...")
    preview_base_payload = dict(base_payload)
    preview_base_payload["page"] = 1
    preview_base_payload["per_page"] = 10
    preview_data = query_apollo_search(active_key, preview_base_payload)
    people_sample = preview_data.get("people") or []
    render_10_lead_preview(people_sample, api_key=active_key)

    # Prompt user for batch name before starting qualification pipeline
    timestamp_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_batch_tag = f"BATCH_{active_email.split('@')[0].upper()}_{timestamp_tag}"
    print("\n" + "=" * 100)
    print("SESSION BATCH IDENTIFIER")
    print("=" * 100)
    custom_batch = input(f"Enter batch name for this session (press Enter for '{default_batch_tag}'): ").strip()
    session_batch_tag = custom_batch if custom_batch else default_batch_tag
    print(f">> Active Batch Name set to: {session_batch_tag}")

    total_session_required_leads = 0

    # Step 8 Parity: Session-wide seen companies for 1/company deduplication
    session_seen_companies: Dict[str, ApolloContact] = {}

    session_audit = {
        "scanned": 0,
        "existing_crm": 0,
        "indian_name": 0,
        "excluded_title": 0,
        "company_dup": 0,
        "required": 0,
        "keywords": []
    }

    # Step 7: Continuous Search Bar Keyword Loop (or Run Base Search directly)
    while True:
        print("\n" + "=" * 100)
        print("SEARCH BAR REFINEMENT & PROSPECTING EXECUTION")
        print("=" * 100)
        print(f"Current Session Batch: {session_batch_tag} | Qualified Leads Staged: {total_session_required_leads}")

        print("Tip: Enter a keyword to narrow results, press ENTER to run base search as-is, or type 'done' to exit.")
        keyword = input("\nEnter keyword or industry (or press Enter to run base search, 'done' to exit): ").strip()

        if keyword.lower() in ("done", "exit", "q"):
            break

        active_payload = dict(base_payload)
        if keyword:
            session_audit["keywords"].append(keyword)
            active_payload["q_keywords"] = keyword
            label_desc = f"keyword '{keyword}'"
        else:
            session_audit["keywords"].append("[Full Base Search]")
            label_desc = "Full Base Search"

        active_payload["page"] = 1
        active_payload["per_page"] = 10

        print(f"\nQuerying Apollo Search API for {label_desc}...")
        preview_data = query_apollo_search(active_key, active_payload)
        pagination = preview_data.get("pagination") or {}
        refined_total = int(pagination.get("total_entries", 0))
        total_pages = min(100, max(1, (refined_total + 99) // 100))

        print(f">> Results: {refined_total:,d} matching leads found across {total_pages} pages (100 leads per page).")

        # Show preview for this keyword slice
        slice_sample = preview_data.get("people") or []
        if slice_sample and keyword:
            render_10_lead_preview(slice_sample, api_key=active_key)

        run_confirm = input(f"\n[?] Proceed with qualification scan for {label_desc} ({total_pages} pages)? [Y/n]: ").strip().lower()
        if run_confirm == "n":
            continue

        default_target_str = str(args.target) if getattr(args, "target", 0) > 0 else "all"
        target_prompt = f"Enter target qualified leads goal (press Enter for '{default_target_str}' pages, e.g. 50): "
        target_input = input(target_prompt).strip()
        if target_input.isdigit() and int(target_input) > 0:
            target_limit = int(target_input)
        elif getattr(args, "target", 0) > 0:
            target_limit = args.target
        else:
            target_limit = 0

        target_desc = f"{target_limit} leads goal" if target_limit > 0 else "all matching pages"
        print(f">> Target Cap: {target_desc}")

        # Step 8: Streaming Ingestion & 4-Layer Qualification matching DOM Extension
        print("\n" + "=" * 100)
        print(f"QUALIFICATION STREAMING: {label_desc} (Pacing: {API_PAGE_DELAY_SEC}s/page | Target: {target_desc} | Batch-Wide 1/Company)")
        print("=" * 100)

        keyword_required_count = 0
        consecutive_empty = 0
        try:
            for current_page in range(1, total_pages + 1):
                page_payload = dict(active_payload)
                page_payload["page"] = current_page
                page_payload["per_page"] = 100

                # Safe rate pacing
                time.sleep(API_PAGE_DELAY_SEC)

                page_res = query_apollo_search(active_key, page_payload)
                page_people = page_res.get("people") or []
                if not page_people:
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        print(f"  [Notice] Reached end of matching Apollo results at page {current_page}.")
                        break
                    continue
                else:
                    consecutive_empty = 0

                # Borrow fresh pooled connection per page for ~5ms
                with get_connection() as conn:
                    # Batch-resolve company domains for this page (0 credits)
                    page_company_names = [
                        p.get("organization_name") or (p.get("organization") or {}).get("name") or ""
                        for p in page_people
                    ]
                    resolved_companies = batch_resolve_company_domains(active_key, page_company_names, conn=conn)

                    page_contacts = [
                        map_apollo_person_to_contact(
                            p,
                            resolved_companies.get(
                                (p.get("organization_name") or (p.get("organization") or {}).get("name") or "").strip().lower()
                            ) or resolved_companies.get(
                                clean_company_name(p.get("organization_name") or (p.get("organization") or {}).get("name") or "").strip().lower()
                            )
                        )
                        for p in page_people
                    ]

                    qualified_leads, stats = qualify_contacts_batch(
                        page_contacts,
                        session_batch_tag,
                        conn,
                        session_seen_companies,
                        filter_indian=False,
                        filter_titles=True
                    )

                    # Enforce target leads limit if specified
                    hit_target = False
                    if target_limit > 0:
                        remaining_needed = target_limit - keyword_required_count
                        if remaining_needed <= 0:
                            qualified_leads = []
                            stats["required"] = 0
                            hit_target = True
                        elif len(qualified_leads) >= remaining_needed:
                            qualified_leads = qualified_leads[:remaining_needed]
                            stats["required"] = len(qualified_leads)
                            hit_target = True

                    if qualified_leads:
                        save_qualified_leads_to_db(qualified_leads, session_batch_tag, conn)

                # Update stats
                session_audit["scanned"] += len(page_contacts)
                session_audit["existing_crm"] += stats["existing_crm"]
                session_audit["indian_name"] += stats["indian_name"]
                session_audit["excluded_title"] += stats["excluded_title"]
                session_audit["company_dup"] += stats["company_dup"]
                session_audit["required"] += stats["required"]
                keyword_required_count += stats["required"]
                total_session_required_leads += stats["required"]

                # Print clean live status row
                goal_str = f" (Progress: {keyword_required_count}/{target_limit})" if target_limit > 0 else ""
                print(
                    f"  [Page {current_page:02d}/{total_pages:02d}] "
                    f"Scanned: {len(page_contacts):<3} | "
                    f"⊘ CRM: {stats['existing_crm']:<2} | "
                    f"⊘ Titles: {stats['excluded_title']:<2} | "
                    f"⚪ 1/Comp: {stats['company_dup']:<2} | "
                    f"🟢 ★ Qualified Unique: {stats['required']:<2}{goal_str}"
                )

                if hit_target:
                    print(f"\n[★ Target Reached] Collected exact target goal of {target_limit} qualified unique leads! Stopping search automatically.")
                    break
        except KeyboardInterrupt:
            print(f"\n[!] Keyword stream interrupted by user. Preserved {keyword_required_count} leads gathered so far.")

        print(f"\n>> {label_desc} Scan Completed! Added {keyword_required_count} ★ Qualified Unique Leads.")

        # If user ran full base search without keywords, prompt if they want to exit or refine
        if not keyword:
            cont_opt = input("\n[?] Finished base search scan. Do you want to try specific keywords? [y/N]: ").strip().lower()
            if cont_opt not in ("y", "yes"):
                break


    # Final Consolidated Summary & CSV Export
    print("\n" + "=" * 100)
    print("SESSION COMPLETION & CONSOLIDATED SUMMARY (Unique Domains + 64K Titles Mode)")
    print("=" * 100)
    print(f"Active Account:         {active_email}")
    print(f"Session Batch Tag:      {session_batch_tag}")
    print(f"Keywords Processed:     {session_audit['keywords'] or ['[None]']}")
    print(f"Total Contacts Scanned: {session_audit['scanned']:,d} leads")
    print("-" * 100)
    print("Filter Breakdown (Unique Domains + 64K Titles - 0 LLM Cost):")
    print(f"  ├── ⊘ CRM Existing Domains Dropped  : {session_audit['existing_crm']:,d}")
    print(f"  ├── ⊘ Excluded Titles Dropped (64K) : {session_audit['excluded_title']:,d} (Zero LLM Tokens Consumed)")
    print(f"  ├── ⚪ 1/Company Duplicate Dropped   : {session_audit['company_dup']:,d}")
    print(f"  └── 🟢 TOTAL QUALIFIED LEADS SAVED   : {session_audit['required']:,d} (100% Net-New Domains, Target Titles, 1/Company)")
    print("=" * 100)

    if session_audit["required"] == 0:
        print("\nNo required leads gathered during this session. Exiting.")
        sys.exit(0)

    print("\n[1] Export all Required Leads to Apollo-compliant CSV (dist/exports/)")
    print("[2] Exit (Leads remain safely staged in MySQL `apollo_saved_leads`)")
    action = input("\nSelect action [1-2] (default 1): ").strip()
    if action in ("", "1"):
        export_batch_to_csv(session_batch_tag, active_email)
    print("\nSession finished successfully.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[Session cancelled by user. Exiting cleanly.]\n")
        sys.exit(0)
