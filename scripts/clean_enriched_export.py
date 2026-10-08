#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enriched Apollo Leads Cleaning & Sales-Ready Export
===================================================
1. Discovers all enriched Apollo accounts/logins in the database.
2. Formats enriched leads into official 75-column Apollo contact export schema.
3. Applies standard data cleaning & enrichment rules:
   - Clean & lowercase email; remove blanks/nans
   - Hierarchy priority mapping (Owner, Founder, C-level, VP, Director, etc.)
   - Deduplicate by Email, keeping highest-priority contact
   - Fill blank Last Name with First Name
   - Create 'Account' column from email domain
   - Create 'Hierarchy' column from Lists
   - Copy '# Employees' to 'Employee 2' and 'Industry' to 'Industry 2'
   - Empty Revenue cells if revenue has >11 digits
4. Saves directly to user's Windows Downloads directory (or custom path).
"""

import os
import re
import csv
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

import pandas as pd

from backend.api import get_connection, extract_root_domain
from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row


# -------------------------------------------------------------
# PRIORITY MAPPING (Lower number = higher priority)
# -------------------------------------------------------------
PRIORITY_MAP = {
    "owner": 1,
    "founder": 2,
    "co-founder": 2,
    "co founder": 2,
    "president": 3,
    "chief executive officer": 4,
    "ceo": 4,
    "chief operating officer": 5,
    "coo": 5,
    "chief financial officer": 6,
    "cfo": 6,
    "chief technology officer": 6,
    "cto": 6,
    "chief information officer": 6,
    "cio": 6,
    "chief revenue officer": 6,
    "cro": 6,
    "chief marketing officer": 6,
    "cmo": 6,
    "c-level": 6,
    "vice president": 7,
    "vp": 7,
    "head": 8,
    "managing director": 9,
    "director": 9,
    "principal": 10,
    "managing partner": 10,
    "partner": 10,
    "manager": 10,
    "chief": 10,
}


def get_lead_priority(row) -> int:
    """Assign priority rank based on Owner column or Job Title keyword match."""
    job_title = str(row.get("Job Title", "") or row.get("Title", "")).lower().strip()
    owner = str(row.get("Owner", "") or row.get("Contact Owner", "")).lower().strip()

    if owner and owner != "nan":
        return 1

    # Check longer/more specific keywords first (e.g. 'vice president' before 'president')
    for keyword in sorted(PRIORITY_MAP.keys(), key=len, reverse=True):
        if keyword in job_title:
            return PRIORITY_MAP[keyword]

    return 999


def clean_apollo_dataframe(
    df: pd.DataFrame,
    conn=None,
    filter_crm_emails: bool = True,
    filter_crm_domains: bool = True,
    dedup_accounts: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Execute complete 14-step sales-ready cleaning & post-enrichment CRM guardrail pipeline.
    Eliminates franchise/parent corporate domain repeats and enforces net-new account purity.
    """
    initial_count = len(df)
    crm_emails_removed = 0
    crm_domains_removed = 0
    account_duplicates_removed = 0

    # 1. Standardize column names
    df.columns = df.columns.str.strip()

    # Align alternate names for Title and Owner
    if "Title" in df.columns and "Job Title" not in df.columns:
        df["Job Title"] = df["Title"]
    elif "Job Title" in df.columns and "Title" not in df.columns:
        df["Title"] = df["Job Title"]

    if "Contact Owner" in df.columns and "Owner" not in df.columns:
        df["Owner"] = df["Contact Owner"]
    elif "Owner" in df.columns and "Contact Owner" not in df.columns:
        df["Contact Owner"] = df["Owner"]

    # 2. Ensure required columns exist
    required_cols = [
        "Email",
        "Job Title",
        "Owner",
        "Last Name",
        "First Name",
        "Lists",
        "# Employees",
        "Industry"
    ]
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""

    # 3. Clean Email column & drop empty / nan
    df["Email"] = df["Email"].astype(str).str.strip().str.lower()
    df_valid_emails = df[
        df["Email"].notna() &
        (df["Email"] != "") &
        (df["Email"] != "nan")
    ].copy()
    valid_email_count = len(df_valid_emails)

    # 3a. Post-Enrichment Master CRM Exact Email Cross-Check
    if conn is not None and filter_crm_emails and len(df_valid_emails) > 0:
        try:
            unique_emails = list(set(df_valid_emails["Email"].dropna()))
            matched_crm_emails = set()
            chunk_size = 500
            with conn.cursor() as cur:
                for i in range(0, len(unique_emails), chunk_size):
                    chunk = unique_emails[i:i + chunk_size]
                    fmt = ",".join(["%s"] * len(chunk))
                    cur.execute(f"SELECT DISTINCT LOWER(email) FROM emails WHERE email IN ({fmt})", chunk)
                    for r in cur.fetchall():
                        if r and r[0]:
                            matched_crm_emails.add(str(r[0]).strip().lower())
            if matched_crm_emails:
                before_email_len = len(df_valid_emails)
                df_valid_emails = df_valid_emails[~df_valid_emails["Email"].isin(matched_crm_emails)].copy()
                crm_emails_removed = before_email_len - len(df_valid_emails)
        except Exception as ex:
            print(f"[Notice] Post-enrichment CRM email cross-check warning: {ex}")

    # 3b. Post-Enrichment Master CRM Corporate/Parent Domain Cross-Check
    # (Fixes the Franchise/Holding company blindspot where search domain differed from actual corporate email domain)
    if conn is not None and filter_crm_domains and len(df_valid_emails) > 0:
        try:
            def extract_lead_domains(row):
                doms = set()
                email = str(row.get("Email", "")).strip().lower()
                if "@" in email:
                    dom_part = email.split("@")[-1].strip()
                    root_e = extract_root_domain(dom_part)
                    if root_e:
                        doms.add(root_e)
                for col_name in ("Company Website", "Website", "Website Link", "Company Domain", "Account"):
                    if col_name in row and row[col_name]:
                        root_w = extract_root_domain(str(row[col_name]).strip())
                        if root_w:
                            doms.add(root_w)
                return doms

            row_domains_map = {}
            all_candidate_domains = set()
            for idx, row in df_valid_emails.iterrows():
                d_set = extract_lead_domains(row)
                row_domains_map[idx] = d_set
                all_candidate_domains.update(d_set)

            matched_crm_domains = set()
            cand_dom_list = list(all_candidate_domains)
            chunk_size = 500
            with conn.cursor() as cur:
                for i in range(0, len(cand_dom_list), chunk_size):
                    chunk = cand_dom_list[i:i + chunk_size]
                    fmt = ",".join(["%s"] * len(chunk))
                    cur.execute(f"SELECT DISTINCT LOWER(domain) FROM emails WHERE domain IN ({fmt})", chunk)
                    for r in cur.fetchall():
                        if r and r[0]:
                            matched_crm_domains.add(str(r[0]).strip().lower())

            if matched_crm_domains:
                keep_indices = [
                    idx for idx in df_valid_emails.index
                    if not (row_domains_map.get(idx, set()) & matched_crm_domains)
                ]
                before_dom_len = len(df_valid_emails)
                df_valid_emails = df_valid_emails.loc[keep_indices].copy()
                crm_domains_removed = before_dom_len - len(df_valid_emails)
        except Exception as ex:
            print(f"[Notice] Post-enrichment CRM domain cross-check warning: {ex}")

    # 4. Assign priority
    df_valid_emails["priority"] = df_valid_emails.apply(get_lead_priority, axis=1)

    # 5. Sort by priority ascending (highest ranking roles first: Owner > Founder > President > CEO...)
    df_sorted = df_valid_emails.sort_values(
        by=["priority", "Email"],
        ascending=[True, True]
    )

    # 6. Deduplicate by Email (keep first = highest priority)
    df_final = df_sorted.drop_duplicates(
        subset=["Email"],
        keep="first"
    ).copy()
    duplicates_removed = len(df_valid_emails) - len(df_final)

    # 7. Fill Last Name with First Name if Last Name is blank
    mask = (
        df_final["Last Name"].isna() |
        (df_final["Last Name"].astype(str).str.strip() == "") |
        (df_final["Last Name"].astype(str).str.lower().str.strip() == "nan")
    )
    last_name_filled = int(mask.sum())
    df_final.loc[mask, "Last Name"] = df_final.loc[mask, "First Name"]
    df_final.loc[mask, "First Name"] = ""

    # 8. Create Account column from Email domain
    df_final["Account"] = (
        df_final["Email"]
        .astype(str)
        .str.strip()
        .str.split("@")
        .str[-1]
    )

    # 8b. Account Density Deduplication (Optional: 1 contact per company/account)
    if dedup_accounts and len(df_final) > 0:
        before_acc_len = len(df_final)
        df_final = df_final.drop_duplicates(
            subset=["Account"],
            keep="first"
        ).copy()
        account_duplicates_removed = before_acc_len - len(df_final)

    # 9. Create Hierarchy column from Lists
    df_final["Hierarchy"] = (
        df_final["Lists"]
        .astype(str)
        .str.split("-")
        .str[0]
        .str.strip()
    )
    df_final.loc[df_final["Hierarchy"].astype(str).str.lower() == "nan", "Hierarchy"] = ""

    # 10. Copy Employee and Industry columns
    df_final["Employee 2"] = df_final["# Employees"]
    df_final["Industry 2"] = df_final["Industry"]

    # 11. Empty Revenue cells if revenue has more than 11 digits
    revenue_cols = [col for col in df_final.columns if "revenue" in col.lower()]
    truncated_revenue = 0
    for col in revenue_cols:
        revenue_digit_count = (
            df_final[col]
            .astype(str)
            .str.replace(r"\D", "", regex=True)
            .str.len()
        )
        over_11 = revenue_digit_count > 11
        truncated_revenue += int(over_11.sum())
        df_final.loc[over_11, col] = ""

    # 12. Drop helper priority column
    df_final.drop(columns=["priority"], inplace=True, errors="ignore")

    stats = {
        "initial_count": initial_count,
        "valid_email_count": valid_email_count,
        "crm_emails_removed": crm_emails_removed,
        "crm_domains_removed": crm_domains_removed,
        "duplicates_removed": duplicates_removed,
        "account_duplicates_removed": account_duplicates_removed,
        "final_count": len(df_final),
        "last_name_filled": last_name_filled,
        "revenue_cols": revenue_cols,
        "truncated_revenue": truncated_revenue,
    }
    return df_final, stats


def load_canonical_accounts_map() -> Tuple[Dict[str, Tuple[str, str]], List[Dict[str, Any]]]:
    """
    Load canonical accounts from config/apollo_accounts.json.
    Returns:
      1. lookup_map: lowercase email/identifier -> (canonical_email, canonical_name)
      2. raw_accounts: full list of account dicts
    """
    accounts_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "apollo_accounts.json",
    )
    lookup: Dict[str, Tuple[str, str]] = {}
    accounts = []
    if os.path.exists(accounts_file):
        try:
            with open(accounts_file, "r", encoding="utf-8") as f:
                accounts = json.load(f)
                for acc in accounts:
                    name = str(acc.get("name", "")).strip()
                    email = str(acc.get("email", "")).strip()
                    if email:
                        canonical_email = email.upper()
                        canonical_name = name or canonical_email
                        lookup[canonical_email.lower()] = (canonical_email, canonical_name)
                        lookup[canonical_email] = (canonical_email, canonical_name)
        except Exception as e:
            print(f"[Notice] Could not load canonical accounts from {accounts_file}: {e}")
    return lookup, accounts


def resolve_canonical_account(login_id: str, account_name: str = "", batch: str = "") -> Tuple[str, str]:
    """
    Map any login/account string (even if lowercase or shorthand) to official uppercase
    account in apollo_accounts.json.
    """
    lookup, accounts = load_canonical_accounts_map()

    clean_login = (login_id or "").strip()
    clean_name = (account_name or "").strip()
    clean_batch = (batch or "").strip().lower()

    # 1. Direct email match (case-insensitive)
    if clean_login.lower() in lookup:
        return lookup[clean_login.lower()]
    if clean_name.lower() in lookup:
        return lookup[clean_name.lower()]

    # 2. Domain / prefix matching from batch or login name if it matches an account's email domain
    for acc in accounts:
        acc_email = str(acc.get("email", "")).strip().upper()
        acc_name = str(acc.get("name", "")).strip()
        if not acc_email:
            continue
        acc_user, _, acc_domain = acc_email.partition("@")
        acc_domain_slug = acc_domain.replace(".", "_").lower()
        acc_user_clean = acc_user.replace(".", "").replace("_", "").lower()

        if acc_domain_slug and acc_domain_slug in clean_batch:
            if (acc_user.lower() in clean_login.lower() or 
                acc_user.lower() in clean_batch or 
                acc_user_clean in clean_batch.replace(".", "").replace("_", "") or
                acc_name.lower() == clean_name.lower() or
                acc_name.lower() == clean_login.lower()):
                return (acc_email, acc_name)

    # 3. Unique account name match (e.g. 'Recruiting', 'Vijay' if unambiguous)
    matched_by_name = [
        acc for acc in accounts 
        if str(acc.get("name", "")).strip().lower() == clean_login.lower() 
        or str(acc.get("name", "")).strip().lower() == clean_name.lower()
    ]
    if len(matched_by_name) == 1:
        acc = matched_by_name[0]
        return (str(acc.get("email", "")).strip().upper(), str(acc.get("name", "")).strip())

    # Fallback: if login_id looks like an email, uppercase it
    if "@" in clean_login:
        return (clean_login.upper(), clean_name or clean_login.upper())

    return (clean_login or "Unknown", clean_name or clean_login or "Unknown")


def fetch_enriched_logins_summary(conn, table_name: str = "all") -> List[Dict[str, Any]]:
    """Query MySQL database for all logins/accounts that have enriched leads across active/all tables."""
    if table_name == "apollo_saved_leads":
        target_tables = [("apollo_saved_leads", "Apollo")]
    elif table_name == "enrich_saved_leads":
        target_tables = [("enrich_saved_leads", "Enrich.so")]
    else:
        target_tables = [("apollo_saved_leads", "Apollo"), ("enrich_saved_leads", "Enrich.so")]

    combined: Dict[Tuple[str, str], Dict[str, Any]] = {}
    with conn.cursor() as cur:
        for tbl, label in target_tables:
            try:
                cur.execute(f"""
                    SELECT 
                        COALESCE(NULLIF(e.login_email, ''), NULLIF(l.account_used, ''), 'Unknown') AS login_id,
                        COALESCE(NULLIF(e.account_name, ''), NULLIF(l.account_used, ''), 'Unknown') AS account_name,
                        COUNT(DISTINCT l.id) AS total_enriched,
                        COUNT(DISTINCT CASE WHEN l.email IS NOT NULL AND l.email != '' AND l.email != 'nan' THEN l.id END) AS verified_emails,
                        GROUP_CONCAT(DISTINCT l.batch ORDER BY l.batch SEPARATOR ', ') AS batches
                    FROM `{tbl}` l
                    LEFT JOIN batch_enrichment_ledger e 
                      ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                    WHERE (l.enriched_at IS NOT NULL OR (l.email IS NOT NULL AND l.email != ''))
                    GROUP BY login_id, account_name;
                """)
                rows = cur.fetchall()
                for r in rows:
                    raw_login = str(r[0] or "Unknown")
                    raw_name = str(r[1] or "Unknown")
                    raw_batches = str(r[4] or "")

                    canonical_login, canonical_name = resolve_canonical_account(raw_login, raw_name, raw_batches)
                    key = (canonical_login, canonical_name)
                    if key not in combined:
                        combined[key] = {
                            "login_id": key[0],
                            "account_name": key[1],
                            "total_enriched": 0,
                            "verified_emails": 0,
                            "batches": set(),
                        }
                    combined[key]["total_enriched"] += int(r[2] or 0)
                    combined[key]["verified_emails"] += int(r[3] or 0)
                    if r[4]:
                        for b in str(r[4]).split(", "):
                            if b.strip():
                                b_tag = f"{b.strip()} [{label}]" if len(target_tables) > 1 else b.strip()
                                combined[key]["batches"].add(b_tag)
            except Exception as ex:
                print(f"[Notice] Could not query enriched logins for table `{tbl}`: {ex}")

    logins = []
    for key, data in combined.items():
        if data["total_enriched"] > 0:
            logins.append({
                "login_id": data["login_id"],
                "account_name": data["account_name"],
                "total_enriched": data["total_enriched"],
                "verified_emails": data["verified_emails"],
                "batches": ", ".join(sorted(data["batches"])),
            })
    logins.sort(key=lambda x: x["verified_emails"], reverse=True)
    return logins


def fetch_batches_for_login(conn, login_id: str, account_name: str = "", table_name: str = "all") -> List[Dict[str, Any]]:
    """Fetch all distinct batches belonging to this login with lead & email counts across active/all tables."""
    if table_name == "apollo_saved_leads":
        target_tables = [("apollo_saved_leads", "Apollo")]
    elif table_name == "enrich_saved_leads":
        target_tables = [("enrich_saved_leads", "Enrich.so")]
    else:
        target_tables = [("apollo_saved_leads", "Apollo"), ("enrich_saved_leads", "Enrich.so")]

    batch_rows = []
    with conn.cursor() as cur:
        for tbl, label in target_tables:
            try:
                if login_id == "__ALL__":
                    sql = f"""
                        SELECT 
                            l.batch,
                            COUNT(DISTINCT l.id) AS total_enriched,
                            COUNT(DISTINCT CASE WHEN l.email IS NOT NULL AND l.email != '' AND l.email != 'nan' THEN l.id END) AS verified_emails,
                            MAX(l.created_at) AS last_added
                        FROM `{tbl}` l
                        WHERE (l.enriched_at IS NOT NULL OR (l.email IS NOT NULL AND l.email != ''))
                        GROUP BY l.batch
                        ORDER BY MAX(l.created_at) DESC;
                    """
                    cur.execute(sql)
                else:
                    if "@" in login_id:
                        match_condition = """
                            (
                                LOWER(COALESCE(e.login_email, '')) = LOWER(%s)
                                OR LOWER(COALESCE(l.account_used, '')) = LOWER(%s)
                            )
                        """
                        match_params = (login_id, login_id)
                    else:
                        match_condition = """
                            (
                                LOWER(COALESCE(e.login_email, '')) = LOWER(%s)
                                OR LOWER(COALESCE(e.account_name, '')) = LOWER(%s)
                                OR LOWER(COALESCE(l.account_used, '')) = LOWER(%s)
                            )
                        """
                        match_params = (login_id, account_name or login_id, account_name or login_id)

                    sql = f"""
                        SELECT 
                            l.batch,
                            COUNT(DISTINCT l.id) AS total_enriched,
                            COUNT(DISTINCT CASE WHEN l.email IS NOT NULL AND l.email != '' AND l.email != 'nan' THEN l.id END) AS verified_emails,
                            MAX(l.created_at) AS last_added
                        FROM `{tbl}` l
                        LEFT JOIN batch_enrichment_ledger e 
                          ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                        WHERE (l.enriched_at IS NOT NULL OR (l.email IS NOT NULL AND l.email != ''))
                          AND {match_condition}
                        GROUP BY l.batch
                        ORDER BY MAX(l.created_at) DESC;
                    """
                    cur.execute(sql, match_params)

                for r in cur.fetchall():
                    batch_rows.append({
                        "batch": str(r[0] or "unnamed"),
                        "source_table": tbl,
                        "source_label": label,
                        "total_enriched": int(r[1] or 0),
                        "verified_emails": int(r[2] or 0),
                        "last_added": str(r[3])[:16] if r[3] else "N/A",
                    })
            except Exception as ex:
                print(f"[Notice] Could not fetch batches from `{tbl}`: {ex}")

    batch_rows.sort(key=lambda x: x["verified_emails"], reverse=True)
    return batch_rows


def fetch_enriched_leads_for_login(
    conn, 
    login_id: str, 
    account_name: str = "",
    batch_name: Optional[str] = None,
    table_name: str = "all",
) -> List[Dict[str, Any]]:
    """Fetch all leads associated with the given login/account and optional batch that have verified emails."""
    if table_name == "apollo_saved_leads":
        target_tables = ["apollo_saved_leads"]
    elif table_name == "enrich_saved_leads":
        target_tables = ["enrich_saved_leads"]
    else:
        target_tables = ["apollo_saved_leads", "enrich_saved_leads"]

    all_leads = []
    with conn.cursor() as cur:
        for tbl in target_tables:
            try:
                query_conditions = ["(l.enriched_at IS NOT NULL OR (l.email IS NOT NULL AND l.email != ''))"]
                params = []

                if login_id != "__ALL__":
                    if "@" in login_id:
                        query_conditions.append("""
                            (
                                LOWER(COALESCE(e.login_email, '')) = LOWER(%s)
                                OR LOWER(COALESCE(l.account_used, '')) = LOWER(%s)
                            )
                        """)
                        params.extend([login_id, login_id])
                    else:
                        query_conditions.append("""
                            (
                                LOWER(COALESCE(e.login_email, '')) = LOWER(%s)
                                OR LOWER(COALESCE(e.account_name, '')) = LOWER(%s)
                                OR LOWER(COALESCE(l.account_used, '')) = LOWER(%s)
                            )
                        """)
                        params.extend([login_id, account_name or login_id, account_name or login_id])

                if batch_name and batch_name != "__ALL__":
                    query_conditions.append("l.batch = %s")
                    params.append(batch_name)

                where_clause = " AND ".join(query_conditions)

                query = f"""
                    SELECT l.*, COALESCE(e.login_email, l.account_used) AS resolved_login
                    FROM `{tbl}` l
                    LEFT JOIN batch_enrichment_ledger e 
                      ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                    WHERE {where_clause}
                    ORDER BY l.id ASC;
                """
                cur.execute(query, tuple(params))

                cols = [c[0] for c in cur.description]
                raw_rows = cur.fetchall()
                for r in raw_rows:
                    row_dict = dict(zip(cols, r))
                    row_dict["_source_table"] = tbl
                    all_leads.append(row_dict)
            except Exception as ex:
                print(f"[Notice] Could not fetch leads from `{tbl}`: {ex}")

    return all_leads


def get_default_downloads_dir() -> str:
    """Return user's Windows Downloads directory, with fallback to exports/ folder."""
    home_dir = os.path.expanduser("~")
    downloads_dir = os.path.join(home_dir, "Downloads")
    if os.path.isdir(downloads_dir):
        return downloads_dir
    os.makedirs("exports", exist_ok=True)
    return os.path.abspath("exports")


def export_clean_enriched_login_action(conn, table_name: str = "all") -> None:
    """Interactive CLI menu option to select enriched login, clean data, and export to Downloads."""
    print("\n" + "=" * 95)
    scope_tag = f"Table: `{table_name}`" if table_name != "all" else "All Tables (Apollo + Enrich.so)"
    print(f"        CLEAN & EXPORT ENRICHED LEADS BY LOGIN (SALES-READY) [{scope_tag}]")
    print("=" * 95)

    logins = fetch_enriched_logins_summary(conn, table_name=table_name)
    if not logins and table_name != "all":
        print(f"\n[NOTICE] No enriched leads found in `{table_name}`. Checking all tables...")
        logins = fetch_enriched_logins_summary(conn, table_name="all")
        table_name = "all"

    if not logins:
        print("\n[NOTICE] No enriched leads found in `apollo_saved_leads` or `enrich_saved_leads`.")
        print("Run `python scripts/enrich_batch_interactive.py` first to enrich a batch.")
        return

    total_all_leads = sum(L["total_enriched"] for L in logins)
    total_all_verified = sum(L["verified_emails"] for L in logins)

    print(f"\n{'#':<4} | {'Enriched Login / Account':<35} | {'Enriched Leads':<15} | {'Verified Emails':<16} | {'Batches'}")
    print("-" * 95)
    for idx, L in enumerate(logins, 1):
        display_label = f"{L['login_id']} ({L['account_name']})" if L['login_id'] != L['account_name'] else L['login_id']
        batches_preview = L['batches'] if len(L['batches']) <= 32 else L['batches'][:29] + "..."
        print(f"[{idx:<2}] | {display_label:<35} | {L['total_enriched']:<15,d} | {L['verified_emails']:<16,d} | {batches_preview}")
    print(f"[A ] | {'[ALL ENRICHED LOGINS COMBINED]':<35} | {total_all_leads:<15,d} | {total_all_verified:<16,d} | (all batches)")
    print("=" * 95)

    while True:
        sel = input(f"\n>> Select Enriched Login Number [1-{len(logins)}] or 'A' for all: ").strip()
        if sel.upper() == "A":
            selected_login = "__ALL__"
            selected_acc_name = "All_Logins"
            label = "All Enriched Logins Combined"
            break
        elif sel.isdigit() and 1 <= int(sel) <= len(logins):
            picked = logins[int(sel) - 1]
            selected_login = picked["login_id"]
            selected_acc_name = picked["account_name"]
            label = f"{picked['login_id']} ({picked['account_name']})" if picked['login_id'] != picked['account_name'] else picked['login_id']
            break
        print("Invalid choice, please select a valid number or 'A'.")

    print(f"\n✓ Selected Login: '{label}'")

    # Discover and prompt for batch selection for this login
    batches = fetch_batches_for_login(conn, selected_login, selected_acc_name, table_name=table_name)
    if not batches:
        print(f"[NOTICE] No batches found with enriched leads for login '{label}'.")
        return

    total_batch_leads = sum(b["total_enriched"] for b in batches)
    total_batch_emails = sum(b["verified_emails"] for b in batches)

    print("\n" + "-" * 95)
    print(f"BATCH SELECTION FOR LOGIN: {label}")
    print("-" * 95)
    print(f"{'#':<4} | {'Batch Name':<38} | {'Source':<10} | {'Enriched Leads':<15} | {'Verified Emails':<16} | {'Last Added'}")
    print("-" * 95)
    print(f"[A ] | {'[ALL BATCHES COMBINED]':<38} | {'[All]':<10} | {total_batch_leads:<15,d} | {total_batch_emails:<16,d} | (all batches)")
    for b_idx, b in enumerate(batches, 1):
        b_name_disp = b['batch'] if len(b['batch']) <= 38 else b['batch'][:35] + "..."
        src_disp = f"[{b.get('source_label', 'Apollo')}]"
        print(f"[{b_idx:<2}] | {b_name_disp:<38} | {src_disp:<10} | {b['total_enriched']:<15,d} | {b['verified_emails']:<16,d} | {b['last_added']}")
    print("-" * 95)

    while True:
        b_sel = input(f"\n>> Select Batch [1-{len(batches)}] or 'A' for all batches (default A): ").strip()
        if not b_sel or b_sel.upper() == "A":
            selected_batch = "__ALL__"
            batch_label = "All Batches Combined"
            batch_target_table = table_name
            break
        elif b_sel.isdigit() and 1 <= int(b_sel) <= len(batches):
            picked_b = batches[int(b_sel) - 1]
            selected_batch = picked_b["batch"]
            batch_label = f"{picked_b['batch']} [{picked_b.get('source_label', 'Apollo')}]"
            batch_target_table = picked_b.get("source_table", table_name)
            break
        print("Invalid choice, please select a valid batch number or 'A'.")

    print(f"\n✓ Selected Batch: '{batch_label}'")
    print("  Fetching lead records and firmographic payloads from database...")

    t0 = time.perf_counter()
    lead_dicts = fetch_enriched_leads_for_login(conn, selected_login, selected_acc_name, batch_name=selected_batch, table_name=batch_target_table)
    if not lead_dicts:
        print(f"[NOTICE] No leads with emails found for batch '{batch_label}'.")
        return

    print(f"  ✓ Fetched {len(lead_dicts):,d} leads from database in {time.perf_counter() - t0:.2f}s.")
    print("  Formatting into 75-column Apollo contact schema...")

    t_fmt = time.perf_counter()
    export_rows = [format_apollo_lead_row(lead, account_email=selected_login) for lead in lead_dicts]
    df_raw = pd.DataFrame(export_rows, columns=APOLLO_75_HEADERS)
    print(f"  ✓ Formatted into DataFrame in {time.perf_counter() - t_fmt:.2f}s.")

    print("\n" + "=" * 95)
    print("           POST-ENRICHMENT CRM DEDUPLICATION & GUARDRAILS")
    print("=" * 95)
    print("  [1] Strict Net-New: Strip CRM Duplicate Emails & Corporate Parent Domains (Recommended)")
    print("      -> Resolves the 28% duplicate rate caused by franchise/parent corporate email domains.")
    print("  [2] Exact Email Check Only (Allow existing corporate domains if person is new)")
    print("  [3] Raw Leads (No CRM cross-checks)")
    print("-" * 95)
    crm_choice = input("Select CRM filter mode [1/2/3, default 1]: ").strip()
    filter_crm_domains = (crm_choice != "2" and crm_choice != "3")
    filter_crm_emails = (crm_choice != "3")

    print("\nACCOUNT DENSITY:")
    print("  [1] All Qualified Contacts (Keep all decision makers at net-new companies - Recommended)")
    print("  [2] Top 1 Decision Maker Per Company (Strictly 1 contact per company/account)")
    print("-" * 95)
    density_choice = input("Select Account Density [1/2, default 1]: ").strip()
    dedup_accounts = (density_choice == "2")

    print("\n[APPLYING 14-STEP SALES-READY CLEANING & CRM GUARDRAIL PIPELINE]")
    print("  • Standardizing columns & ensuring required headers")
    print("  • Cleaning emails & filtering blank / NaN")
    if filter_crm_emails:
        print("  • Querying 7.47M master CRM `emails` table for exact email matches...")
    if filter_crm_domains:
        print("  • Cross-checking revealed corporate email domains against 7.47M CRM domains...")
    print("  • Assigning hierarchy priority (Owner > Founder > C-Level > VP > Director > Manager)")
    print("  • Deduplicating by Email (preserving highest-ranking contact)")
    print("  • Filling blank Last Name with First Name")
    print("  • Creating 'Account' column from Email domain")
    if dedup_accounts:
        print("  • Enforcing Account Density: Keeping top decision maker per company")
    print("  • Creating 'Hierarchy' column from Lists")
    print("  • Copying Employee & Industry columns ('Employee 2', 'Industry 2')")
    print("  • Emptying Revenue cells exceeding 11 digits")

    t_clean = time.perf_counter()
    df_clean, stats = clean_apollo_dataframe(
        df_raw,
        conn=conn,
        filter_crm_emails=filter_crm_emails,
        filter_crm_domains=filter_crm_domains,
        dedup_accounts=dedup_accounts,
    )
    print(f"  ✓ Pipeline executed in {time.perf_counter() - t_clean:.2f}s.")

    # -------------------------------------------------------------
    # DOWNLOAD / SAVE SELECTION
    # -------------------------------------------------------------
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    acc_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", selected_acc_name if selected_login != "__ALL__" else "All_Accounts").strip("_")
    if selected_batch and selected_batch != "__ALL__":
        batch_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", selected_batch).strip("_")
        default_filename = f"{acc_slug}_{batch_slug}_cleaned_sales_leads_{timestamp_str}.csv"
    else:
        default_filename = f"{acc_slug}_cleaned_sales_leads_{timestamp_str}.csv"

    downloads_dir = get_default_downloads_dir()
    default_downloads_path = os.path.join(downloads_dir, default_filename)
    project_exports_path = os.path.abspath(os.path.join("exports", default_filename))

    print("\n" + "=" * 95)
    print("                      DOWNLOAD DESTINATION")
    print("=" * 95)
    print(f"  [1] Download to Windows Downloads folder (Recommended):")
    print(f"      -> {default_downloads_path}")
    print(f"  [2] Save to Project Exports folder:")
    print(f"      -> {project_exports_path}")
    print(f"  [3] Specify custom path")
    print("=" * 95)

    dest_choice = input("\nSelect destination [1/2/3] (Press Enter for Downloads): ").strip()
    if dest_choice == "2":
        out_path = project_exports_path
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    elif dest_choice == "3":
        custom = input("Enter custom file path: ").strip().strip('\'"')
        out_path = custom if custom else default_downloads_path
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    else:
        out_path = default_downloads_path
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Save final CSV with UTF-8 BOM for perfect Excel / spreadsheet compatibility
    df_clean.to_csv(out_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 95)
    print("                    CLEANING & EXPORT SUMMARY")
    print("=" * 95)
    print(f"  • Selected Login:                  {label}")
    print(f"  • Selected Batch:                  {batch_label}")
    print(f"  • Total Enriched Leads Fetched:    {stats['initial_count']:,d}")
    print(f"  • Leads with Valid Emails:         {stats['valid_email_count']:,d}")
    if stats.get("crm_emails_removed", 0) > 0:
        print(f"  • CRM Duplicate Emails Blocked:    {stats['crm_emails_removed']:,d}")
    if stats.get("crm_domains_removed", 0) > 0:
        print(f"  • CRM Corporate Domains Blocked:   {stats['crm_domains_removed']:,d} (Parent/Franchise Duplicates)")
    print(f"  • Email Intra-Duplicates Removed:  {stats['duplicates_removed']:,d}")
    if stats.get("account_duplicates_removed", 0) > 0:
        print(f"  • Secondary Account Contacts:      {stats['account_duplicates_removed']:,d} (Kept top 1 per account)")
    print(f"  • Final Cleaned Net-New Records:   {stats['final_count']:,d}")
    print(f"  • Blank Last Names Filled:         {stats['last_name_filled']:,d}")
    print(f"  • Revenue Cells Checked / Emptied: {stats['revenue_cols']} ({stats['truncated_revenue']} >11 digits emptied)")
    print(f"  • Added Columns:                   ['Account', 'Hierarchy', 'Employee 2', 'Industry 2']")
    print(f"  • Total Columns:                   {len(df_clean.columns)}")
    print(f"  • Saved File Path:                 {os.path.abspath(out_path)}")
    print("=" * 95 + "\n")
