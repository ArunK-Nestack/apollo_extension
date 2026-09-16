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

from backend.api import get_connection
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
    "ceo": 4,
    "coo": 5,
    "cfo": 6,
    "cto": 6,
    "vice president": 7,
    "vp": 7,
    "head": 8,
    "director": 9,
    "principal": 10,
    "manager": 10,
    "managing partner": 10,
    "partner": 10,
    "chief": 10,
}


def get_lead_priority(row) -> int:
    """Assign priority rank based on Owner column or Job Title keyword match."""
    job_title = str(row.get("Job Title", "") or row.get("Title", "")).lower().strip()
    owner = str(row.get("Owner", "") or row.get("Contact Owner", "")).lower().strip()

    if owner and owner != "nan":
        return 1

    for keyword, rank in PRIORITY_MAP.items():
        if keyword in job_title:
            return rank

    return 999


def clean_apollo_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Execute complete 12-step cleaning algorithm on Apollo leads DataFrame.
    """
    initial_count = len(df)

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

    # 4. Assign priority
    df_valid_emails["priority"] = df_valid_emails.apply(get_lead_priority, axis=1)

    # 5. Sort by Email + priority ascending
    df_sorted = df_valid_emails.sort_values(
        by=["Email", "priority"],
        ascending=[True, True]
    )

    # 6. Deduplicate by Email (keep first = highest priority)
    df_final = df_sorted.drop_duplicates(
        subset=["Email"],
        keep="first"
    ).copy()
    duplicates_removed = valid_email_count - len(df_final)

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
        "final_count": len(df_final),
        "duplicates_removed": duplicates_removed,
        "last_name_filled": last_name_filled,
        "revenue_cols": revenue_cols,
        "truncated_revenue": truncated_revenue,
    }
    return df_final, stats


def fetch_enriched_logins_summary(conn) -> List[Dict[str, Any]]:
    """Query MySQL database for all logins/accounts that have enriched leads."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 
                COALESCE(NULLIF(e.login_email, ''), NULLIF(l.account_used, ''), 'Unknown') AS login_id,
                COALESCE(NULLIF(e.account_name, ''), NULLIF(l.account_used, ''), 'Unknown') AS account_name,
                COUNT(DISTINCT l.id) AS total_enriched,
                COUNT(DISTINCT CASE WHEN l.email IS NOT NULL AND l.email != '' AND l.email != 'nan' THEN l.id END) AS verified_emails,
                GROUP_CONCAT(DISTINCT l.batch ORDER BY l.batch SEPARATOR ', ') AS batches
            FROM apollo_saved_leads l
            LEFT JOIN batch_enrichment_ledger e 
              ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
            WHERE (l.enriched_at IS NOT NULL OR (l.email IS NOT NULL AND l.email != ''))
            GROUP BY login_id, account_name
            HAVING total_enriched > 0
            ORDER BY verified_emails DESC;
        """)
        rows = cur.fetchall()

    logins = []
    for r in rows:
        logins.append({
            "login_id": r[0],
            "account_name": r[1],
            "total_enriched": r[2],
            "verified_emails": r[3],
            "batches": r[4] or "",
        })
    return logins


def fetch_batches_for_login(conn, login_id: str, account_name: str = "") -> List[Dict[str, Any]]:
    """Fetch all distinct batches belonging to this login with lead & email counts."""
    with conn.cursor() as cur:
        if login_id == "__ALL__":
            cur.execute("""
                SELECT 
                    l.batch,
                    COUNT(DISTINCT l.id) AS total_enriched,
                    COUNT(DISTINCT CASE WHEN l.email IS NOT NULL AND l.email != '' AND l.email != 'nan' THEN l.id END) AS verified_emails,
                    MAX(l.created_at) AS last_added
                FROM apollo_saved_leads l
                WHERE (l.enriched_at IS NOT NULL OR (l.email IS NOT NULL AND l.email != ''))
                GROUP BY l.batch
                ORDER BY MAX(l.created_at) DESC;
            """)
        else:
            cur.execute("""
                SELECT 
                    l.batch,
                    COUNT(DISTINCT l.id) AS total_enriched,
                    COUNT(DISTINCT CASE WHEN l.email IS NOT NULL AND l.email != '' AND l.email != 'nan' THEN l.id END) AS verified_emails,
                    MAX(l.created_at) AS last_added
                FROM apollo_saved_leads l
                LEFT JOIN batch_enrichment_ledger e 
                  ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                WHERE (l.enriched_at IS NOT NULL OR (l.email IS NOT NULL AND l.email != ''))
                  AND (
                      COALESCE(NULLIF(e.login_email, ''), NULLIF(l.account_used, ''), 'Unknown') = %s
                      OR COALESCE(NULLIF(e.account_name, ''), NULLIF(l.account_used, ''), 'Unknown') = %s
                      OR l.account_used = %s
                  )
                GROUP BY l.batch
                ORDER BY MAX(l.created_at) DESC;
            """, (login_id, account_name or login_id, account_name or login_id))

        rows = cur.fetchall()
        return [
            {
                "batch": str(r[0] or "unnamed"),
                "total_enriched": int(r[1] or 0),
                "verified_emails": int(r[2] or 0),
                "last_added": str(r[3])[:16] if r[3] else "N/A",
            }
            for r in rows
        ]


def fetch_enriched_leads_for_login(
    conn, 
    login_id: str, 
    account_name: str = "",
    batch_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Fetch all leads associated with the given login/account and optional batch that have verified emails."""
    with conn.cursor() as cur:
        query_conditions = ["(l.enriched_at IS NOT NULL OR (l.email IS NOT NULL AND l.email != ''))"]
        params = []

        if login_id != "__ALL__":
            query_conditions.append("""
                (
                    COALESCE(NULLIF(e.login_email, ''), NULLIF(l.account_used, ''), 'Unknown') = %s
                    OR COALESCE(NULLIF(e.account_name, ''), NULLIF(l.account_used, ''), 'Unknown') = %s
                    OR l.account_used = %s
                )
            """)
            params.extend([login_id, account_name or login_id, account_name or login_id])

        if batch_name and batch_name != "__ALL__":
            query_conditions.append("l.batch = %s")
            params.append(batch_name)

        where_clause = " AND ".join(query_conditions)

        query = f"""
            SELECT l.*, COALESCE(e.login_email, l.account_used) AS resolved_login
            FROM apollo_saved_leads l
            LEFT JOIN batch_enrichment_ledger e 
              ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
            WHERE {where_clause}
            ORDER BY l.id ASC;
        """
        cur.execute(query, tuple(params))

        cols = [c[0] for c in cur.description]
        raw_rows = cur.fetchall()
        return [dict(zip(cols, r)) for r in raw_rows]


def get_default_downloads_dir() -> str:
    """Return user's Windows Downloads directory, with fallback to exports/ folder."""
    home_dir = os.path.expanduser("~")
    downloads_dir = os.path.join(home_dir, "Downloads")
    if os.path.isdir(downloads_dir):
        return downloads_dir
    os.makedirs("exports", exist_ok=True)
    return os.path.abspath("exports")


def export_clean_enriched_login_action(conn) -> None:
    """Interactive CLI menu option to select enriched login, clean data, and export to Downloads."""
    print("\n" + "=" * 95)
    print("        CLEAN & EXPORT ENRICHED APOLLO LEADS BY LOGIN (SALES-READY)")
    print("=" * 95)

    logins = fetch_enriched_logins_summary(conn)
    if not logins:
        print("\n[NOTICE] No enriched leads found in `apollo_saved_leads` or `batch_enrichment_ledger`.")
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
    batches = fetch_batches_for_login(conn, selected_login, selected_acc_name)
    if not batches:
        print(f"[NOTICE] No batches found with enriched leads for login '{label}'.")
        return

    total_batch_leads = sum(b["total_enriched"] for b in batches)
    total_batch_emails = sum(b["verified_emails"] for b in batches)

    print("\n" + "-" * 95)
    print(f"BATCH SELECTION FOR LOGIN: {label}")
    print("-" * 95)
    print(f"{'#':<4} | {'Batch Name':<42} | {'Enriched Leads':<15} | {'Verified Emails':<16} | {'Last Added'}")
    print("-" * 95)
    print(f"[A ] | {'[ALL BATCHES COMBINED]':<42} | {total_batch_leads:<15,d} | {total_batch_emails:<16,d} | (all batches)")
    for b_idx, b in enumerate(batches, 1):
        b_name_disp = b['batch'] if len(b['batch']) <= 42 else b['batch'][:39] + "..."
        print(f"[{b_idx:<2}] | {b_name_disp:<42} | {b['total_enriched']:<15,d} | {b['verified_emails']:<16,d} | {b['last_added']}")
    print("-" * 95)

    while True:
        b_sel = input(f"\n>> Select Batch [1-{len(batches)}] or 'A' for all batches (default A): ").strip()
        if not b_sel or b_sel.upper() == "A":
            selected_batch = "__ALL__"
            batch_label = "All Batches Combined"
            break
        elif b_sel.isdigit() and 1 <= int(b_sel) <= len(batches):
            picked_b = batches[int(b_sel) - 1]
            selected_batch = picked_b["batch"]
            batch_label = picked_b["batch"]
            break
        print("Invalid choice, please select a valid batch number or 'A'.")

    print(f"\n✓ Selected Batch: '{batch_label}'")
    print("  Fetching lead records and firmographic payloads from database...")

    t0 = time.perf_counter()
    lead_dicts = fetch_enriched_leads_for_login(conn, selected_login, selected_acc_name, batch_name=selected_batch)
    if not lead_dicts:
        print(f"[NOTICE] No leads with emails found for batch '{batch_label}'.")
        return

    print(f"  ✓ Fetched {len(lead_dicts):,d} leads from database in {time.perf_counter() - t0:.2f}s.")
    print("  Formatting into 75-column Apollo contact schema...")

    t_fmt = time.perf_counter()
    export_rows = [format_apollo_lead_row(lead, account_email=selected_login) for lead in lead_dicts]
    df_raw = pd.DataFrame(export_rows, columns=APOLLO_75_HEADERS)
    print(f"  ✓ Formatted into DataFrame in {time.perf_counter() - t_fmt:.2f}s.")

    print("\n[APPLYING 12-STEP SALES-READY CLEANING PIPELINE]")
    print("  • Standardizing columns & ensuring required headers")
    print("  • Cleaning emails & filtering blank / NaN")
    print("  • Assigning hierarchy priority (Owner > C-Level > VP > Director > Manager)")
    print("  • Deduplicating by Email (preserving highest-ranking contact)")
    print("  • Filling blank Last Name with First Name")
    print("  • Creating 'Account' column from Email domain")
    print("  • Creating 'Hierarchy' column from Lists")
    print("  • Copying Employee & Industry columns ('Employee 2', 'Industry 2')")
    print("  • Emptying Revenue cells exceeding 11 digits")

    t_clean = time.perf_counter()
    df_clean, stats = clean_apollo_dataframe(df_raw)
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
    print(f"  • Email Duplicates Removed:        {stats['duplicates_removed']:,d}")
    print(f"  • Final Cleaned Records:           {stats['final_count']:,d}")
    print(f"  • Blank Last Names Filled:         {stats['last_name_filled']:,d}")
    print(f"  • Revenue Cells Checked / Emptied: {stats['revenue_cols']} ({stats['truncated_revenue']} >11 digits emptied)")
    print(f"  • Added Columns:                   ['Account', 'Hierarchy', 'Employee 2', 'Industry 2']")
    print(f"  • Total Columns:                   {len(df_clean.columns)}")
    print(f"  • Saved File Path:                 {os.path.abspath(out_path)}")
    print("=" * 95 + "\n")
