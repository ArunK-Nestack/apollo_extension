#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Send Enriched Leads to MillionVerifier (Bulk Verification & Categorization)
==========================================================================
1. Selects enriched login / batch (same flow as Clean & Export).
2. Formats leads into 75-column Apollo contact schema.
3. Applies 14-step sales-ready cleaning & post-enrichment CRM guardrails
   (removes duplicate emails and holding/parent corporate domains).
4. Uploads cleaned leads to MillionVerifier Bulk API with all columns preserved.
5. Polls verification progress in real-time until completion.
6. Categorizes results into Good, Bad, and Risky datasets with all 75 columns intact.
7. Prompts user to download / save the verified files directly to Downloads or Exports.
"""

from __future__ import annotations

import os
import sys
import re
import csv
import json
import time
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd

# Ensure workspace root and millionverifier_agent_step1 are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MV_DIR = PROJECT_ROOT / "millionverifier_agent_step1"

for p in [str(PROJECT_ROOT), str(MV_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.api import get_connection, extract_root_domain
from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row
from scripts.clean_enriched_export import (
    fetch_enriched_logins_summary,
    fetch_batches_for_login,
    fetch_enriched_leads_for_login,
    clean_apollo_dataframe,
    get_default_downloads_dir,
)

# MillionVerifier agent imports
from app.config import settings
from app.models import VerificationJob
from app.services.verifier import VerificationService
from app.services.categorizer import categorize_results


def ensure_millionverifier_api_key() -> str:
    """Check and ensure MillionVerifier API key is configured."""
    key = os.getenv("MILLIONVERIFIER_API_KEY", "").strip() or settings.api_key
    if not key or key == "replace_with_your_real_api_key":
        print("\n" + "!" * 80)
        print(" ERROR: MILLIONVERIFIER_API_KEY is not configured in .env!")
        print("!" * 80)
        print("Please add your MillionVerifier API key to .env:")
        print("  MILLIONVERIFIER_API_KEY=your_key_here")
        key_input = input("\nOr paste your MillionVerifier API key here to continue: ").strip()
        if key_input:
            os.environ["MILLIONVERIFIER_API_KEY"] = key_input
            return key_input
        raise RuntimeError("MillionVerifier API key is required to run verification.")
    return key


def send_to_millionverifier_action(conn, table_name: str = "all") -> None:
    """
    Interactive CLI action:
    Select enriched leads by login/batch, run 14-step cleaning & CRM guardrails,
    upload to MillionVerifier, monitor progress, categorize, and prompt download.
    """
    ensure_millionverifier_api_key()

    print("\n" + "=" * 95)
    scope_tag = f"Table: `{table_name}`" if table_name != "all" else "All Tables (Apollo + Enrich.so)"
    print(f"        SEND ENRICHED LEADS TO MILLIONVERIFIER (BULK VERIFICATION) [{scope_tag}]")
    print("=" * 95)

    # 1. Discover and select enriched login
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

    # 2. Discover and select batch
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

    # 3. CRM Guardrail & Deduplication Configuration
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

    if len(df_clean) == 0:
        print("\n[NOTICE] No valid leads remaining after cleaning & CRM deduplication.")
        return

    print(f"\n  ✓ Cleaned Leads Ready for MillionVerifier: {len(df_clean):,d} records (All {len(df_clean.columns)} columns preserved)")

    # 4. Prepare temporary CSV and row mapping for MillionVerifier
    cache_dir = PROJECT_ROOT / "scratch" / "millionverifier_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    results_dir = cache_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    acc_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", selected_acc_name if selected_login != "__ALL__" else "All_Accounts").strip("_")
    batch_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", selected_batch if selected_batch and selected_batch != "__ALL__" else "All_Batches").strip("_")
    default_base_stem = f"{acc_slug}_{batch_slug}"

    print("\n" + "-" * 95)
    print("OUTPUT FILE NAMING:")
    print(f"  Specify a file name for the outputs (Good, Bad, Risky CSVs).")
    custom_name_in = input(f"Enter File Name for this verification batch (default: '{default_base_stem}'): ").strip()
    if custom_name_in:
        base_stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", custom_name_in).strip("_")
    else:
        base_stem = default_base_stem

    prep_csv_path = cache_dir / f"{base_stem}_{timestamp_str}_prepared.csv"

    # Save prepared CSV
    df_clean.to_csv(prep_csv_path, index=False, encoding="utf-8-sig")

    # Build email to original row dictionary to preserve ALL 75+ columns
    email_to_orig: Dict[str, Dict[str, str]] = {}
    original_headers = list(df_clean.columns)
    for _, row in df_clean.iterrows():
        em = str(row.get("Email", "")).strip().lower()
        if em:
            email_to_orig[em] = {k: ("" if pd.isna(v) else str(v)) for k, v in row.to_dict().items()}

    # 5. Upload & Verify via MillionVerifier Bulk API
    print("\n" + "=" * 95)
    print("                     MILLIONVERIFIER BULK VERIFICATION")
    print("=" * 95)
    print(f"  • Uploading {len(df_clean):,d} contacts to MillionVerifier...")
    print(f"  • Bulk Endpoint: {settings.bulk_base_url}")
    print("  • Starting verification job...")

    verifier = VerificationService()

    def progress_callback(job: VerificationJob) -> None:
        est = f"{job.estimated_time_sec}s remaining" if job.estimated_time_sec is not None else "calculating..."
        pct = job.percent if job.percent is not None else 0
        ver = job.verified if job.verified is not None else 0
        tot = job.total_rows if job.total_rows is not None else len(df_clean)
        print(
            f"    -> [Job #{job.file_id}] Status: {job.status.upper():<10} | "
            f"Progress: {pct:>3}% ({ver:,d}/{tot:,d} verified) | {est}",
            flush=True,
        )

    t_mv = time.perf_counter()
    try:
        job, downloaded_csv = verifier.verify_file(
            clean_csv_path=prep_csv_path,
            download_dir=results_dir,
            on_progress=progress_callback,
        )
        print(f"\n  ✓ MillionVerifier Job #{job.file_id} completed successfully in {time.perf_counter() - t_mv:.2f}s!")
    except Exception as exc:
        print(f"\n[ERROR] MillionVerifier verification failed: {exc}")
        return

    # 6. Categorize Results (Good, Bad, Risky) with ALL 75 columns preserved
    temp_categorized_dir = cache_dir / f"{base_stem}_categorized"
    temp_categorized_dir.mkdir(parents=True, exist_ok=True)

    print("\n  Categorizing leads into Good, Bad, and Risky datasets...")
    cat_summary = categorize_results(
        csv_path=downloaded_csv,
        output_dir=temp_categorized_dir,
        base_filename=base_stem,
        total_input_count=len(df_clean),
        email_to_original_row=email_to_orig,
        original_headers=original_headers,
    )

    # 7. As requested: "Once it is done, it will ask us to download it."
    downloads_dir = Path(get_default_downloads_dir())
    project_exports_dir = PROJECT_ROOT / "exports"

    default_downloads_folder = downloads_dir / f"{base_stem}_verified_{timestamp_str}"
    project_exports_folder = project_exports_dir / f"{base_stem}_verified_{timestamp_str}"

    print("\n" + "=" * 95)
    print("                      DELIVERY & FRESHSALES CRM SYNC")
    print("=" * 95)
    print("  Verification is complete! Categorization results:")
    print(f"  🟢 Good / Deliverable Leads : {cat_summary.good_count:,d} (Ready for outreach/CRM)")
    print(f"  🔴 Bad / Bounce Leads        : {cat_summary.bad_count:,d} (Filtered out)")
    print(f"  🟡 Risky / Catch-All Leads   : {cat_summary.risky_count:,d} (Separated)")
    print("-" * 95)
    print("  Choose how you would like to deliver these leads:")
    print("  [1] Push directly to Freshsales CRM & Save CSVs to Downloads (Recommended)")
    print("      -> Auto-merges tags (never overwrites), updates non-empty fields only,")
    print("         filters 33 foreign TLDs, and saves verified CSVs in your Downloads folder.")
    print("  [2] Push directly to Freshsales CRM only (Skip local Downloads folder)")
    print("      -> Maps leads directly to Freshsales CRM and keeps archive in project exports.")
    print("  [3] Download / Save verified CSV files only (Skip CRM sync for now)")
    print("      -> Saves Good/Bad/Risky CSVs locally (you can sync to CRM later via Manage Batches [F]).")
    print("=" * 95)

    delivery_choice = input("\nSelect delivery option [1/2/3, default 1]: ").strip()
    if not delivery_choice:
        delivery_choice = "1"

    if delivery_choice == "2":
        target_dir = project_exports_folder
    elif delivery_choice == "3":
        print(f"\nSelect save destination:")
        print(f"  [1] Downloads folder -> {default_downloads_folder}")
        print(f"  [2] Project exports  -> {project_exports_folder}")
        print(f"  [3] Custom directory path")
        dest_sub = input("Destination [1/2/3, default 1]: ").strip()
        if dest_sub == "2":
            target_dir = project_exports_folder
        elif dest_sub == "3":
            custom = input("Enter custom directory path: ").strip().strip('\'"')
            target_dir = Path(custom) if custom else default_downloads_folder
        else:
            target_dir = default_downloads_folder
    else:
        target_dir = default_downloads_folder

    target_dir.mkdir(parents=True, exist_ok=True)

    # Copy files to user's selected destination
    dest_good = target_dir / cat_summary.good_file.name
    dest_bad = target_dir / cat_summary.bad_file.name
    dest_risky = target_dir / cat_summary.risky_file.name

    shutil.copy2(cat_summary.good_file, dest_good)
    shutil.copy2(cat_summary.bad_file, dest_bad)
    shutil.copy2(cat_summary.risky_file, dest_risky)

    # If saving to Downloads or exports, copy Good file directly to root of Downloads for 1-click convenience
    direct_good_copy = downloads_dir / cat_summary.good_file.name
    if delivery_choice in ("1", "3") and target_dir != downloads_dir:
        try:
            shutil.copy2(cat_summary.good_file, direct_good_copy)
        except Exception:
            pass

    # Push to Freshsales CRM if Option 1 or 2
    crm_sync_result = None
    if delivery_choice in ("1", "2"):
        print("\n" + "-" * 95)
        print("FRESHSALES CRM TAG SETUP:")
        print("  Existing contact tags will NEVER be replaced; this tag will be added to them.")
        default_tag = f"{base_stem}-verified"
        tag_in = input(f"Enter Freshsales Tag to apply (default: '{default_tag}'): ").strip()
        tag_to_apply = tag_in if tag_in else default_tag

        try:
            from scripts.freshsales_bridge import sync_good_file_to_freshsales
            crm_sync_result = sync_good_file_to_freshsales(
                good_csv_path=dest_good,
                custom_tag=tag_to_apply,
            )
        except Exception as e:
            print(f"\n[Warning] Freshsales CRM sync encountered an error: {e}")

    # Save summary JSON
    summary_data = {
        "account": label,
        "batch": batch_label,
        "verified_at": datetime.now().isoformat(),
        "job_id": job.file_id,
        "metrics": {
            "total_fetched": stats["initial_count"],
            "leads_with_emails": stats["valid_email_count"],
            "crm_emails_blocked": stats.get("crm_emails_removed", 0),
            "crm_domains_blocked": stats.get("crm_domains_removed", 0),
            "sent_to_millionverifier": len(df_clean),
            "good_count": cat_summary.good_count,
            "bad_count": cat_summary.bad_count,
            "risky_count": cat_summary.risky_count,
            "good_deliverable_rate": f"{(cat_summary.good_count / max(1, len(df_clean))) * 100:.1f}%",
        },
        "files": {
            "good_leads_csv": str(dest_good),
            "bad_leads_csv": str(dest_bad),
            "risky_leads_csv": str(dest_risky),
            "downloads_root_shortcut": str(direct_good_copy),
        },
    }
    summary_path = target_dir / f"{base_stem}_verification_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    # Clean temporary scratch files
    try:
        if prep_csv_path.exists():
            prep_csv_path.unlink()
        if downloaded_csv.exists():
            downloaded_csv.unlink()
        shutil.rmtree(temp_categorized_dir, ignore_errors=True)
    except Exception:
        pass

    good_pct = (cat_summary.good_count / max(1, len(df_clean))) * 100
    bad_pct = (cat_summary.bad_count / max(1, len(df_clean))) * 100
    risky_pct = (cat_summary.risky_count / max(1, len(df_clean))) * 100

    print("\n" + "=" * 95)
    print("                 MILLIONVERIFIER FINAL VERIFICATION SUMMARY")
    print("=" * 95)
    print(f"  • Selected Login:                  {label}")
    print(f"  • Selected Batch:                  {batch_label}")
    print(f"  • Total Enriched Leads Fetched:    {stats['initial_count']:,d}")
    if stats.get("crm_emails_removed", 0) > 0:
        print(f"  • CRM Duplicate Emails Blocked:    {stats['crm_emails_removed']:,d}")
    if stats.get("crm_domains_removed", 0) > 0:
        print(f"  • CRM Corporate Domains Blocked:   {stats['crm_domains_removed']:,d} (Franchise/Parent holding repeats)")
    print(f"  • Net-New Leads Sent to Verifier:  {len(df_clean):,d}")
    print("-" * 95)
    print(f"  🟢 GOOD / DELIVERABLE LEADS:       {cat_summary.good_count:,d} ({good_pct:.1f}%) [READY FOR OUTREACH]")
    print(f"  🔴 BAD / BOUNCE LEADS:             {cat_summary.bad_count:,d} ({bad_pct:.1f}%) [FILTERED OUT]")
    print(f"  🟡 RISKY / CATCH-ALL LEADS:        {cat_summary.risky_count:,d} ({risky_pct:.1f}%) [SEPARATED]")
    print("-" * 95)
    print(f"  • All Original Columns Preserved:  YES ({len(original_headers)} columns)")
    print(f"  • Verified Files Saved To:         {os.path.abspath(target_dir)}")
    if delivery_choice in ("1", "3"):
        print(f"  • Direct Downloads Good Copy:      {os.path.abspath(direct_good_copy)}")
    if crm_sync_result:
        print(f"  • Freshsales CRM Sync:             {crm_sync_result.get('status', 'completed').upper()}")
        print(f"    - Contacts Created:              {crm_sync_result.get('created', 0):,d}")
        print(f"    - Contacts Updated (Non-over.):  {crm_sync_result.get('updated', 0):,d}")
        print(f"    - Country TLDs Excluded:         {crm_sync_result.get('tld_blocked', 0):,d} (33 country TLD blocklist)")
        print(f"    - Freshsales Tag Applied:        '{crm_sync_result.get('tag', '')}'")
        if crm_sync_result.get('audit_file'):
            print(f"    - Audit CSV Report:              {crm_sync_result.get('audit_file')}")
    print("=" * 95 + "\n")

    # Optional: open folder in Windows Explorer
    open_folder = input("Open destination folder in Windows Explorer? [Y/n, default Y]: ").strip()
    if not open_folder or open_folder.lower().startswith("y"):
        try:
            os.startfile(str(target_dir))
        except Exception:
            pass


def main():
    table_arg = "apollo_saved_leads"
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--table", "-t") and i < len(sys.argv):
            val = sys.argv[i].strip().lower()
            if "enrich" in val:
                table_arg = "enrich_saved_leads"
            elif "all" in val:
                table_arg = "all"
        elif "enrich" in arg.lower() and not arg.endswith(".py"):
            table_arg = "enrich_saved_leads"

    with get_connection() as conn:
        send_to_millionverifier_action(conn, table_name=table_arg)


if __name__ == "__main__":
    main()
