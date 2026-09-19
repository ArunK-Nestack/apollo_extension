#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freshsales CRM Bridge for MillionVerifier Good Leads
=====================================================
1. Discovers verified 'Good' lead files produced by MillionVerifier.
2. Tracks processed vs unprocessed files in config/freshsales_synced_batches.json.
3. Prompts user for custom tag name before sync (safely appending to existing tags).
4. Invokes freshsales_agent BatchProcessor:
   - Layer 1: In-file de-duplication
   - Layer 2: 33-TLD foreign domain filtering (.uk, .ca, .de, .fr, etc.)
   - Layer 3: CRM pre-lookup & non-overwrite delta resolution (never overwrites existing data)
   - Layer 4: Non-destructive tag merging (appends custom tag to all existing contact tags)
   - Layer 5: Freshsales Bulk API dispatch in batches of 100 with polling
5. Logs metrics to SQLite (crm_automation.db), generates audit CSVs, and updates sync status.
"""

from __future__ import annotations

import os
import sys
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FS_AGENT_DIR = PROJECT_ROOT / "freshsales_agent"

# Add paths to sys.path
for p in [str(PROJECT_ROOT), str(FS_AGENT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

load_dotenv()

# Also load freshsales_agent/.env if present
fs_env = FS_AGENT_DIR / ".env"
if fs_env.exists():
    load_dotenv(fs_env)

from scripts.clean_enriched_export import get_default_downloads_dir

LEDGER_PATH = PROJECT_ROOT / "config" / "freshsales_synced_batches.json"


# =====================================================================
# 1. SYNC LEDGER MANAGEMENT
# =====================================================================

def get_freshsales_ledger() -> Dict[str, Any]:
    """Load the persistent sync ledger tracking which files have been pushed to Freshsales."""
    if not LEDGER_PATH.exists():
        return {}
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_freshsales_ledger(ledger: Dict[str, Any]) -> None:
    """Save the updated sync ledger."""
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)


def record_batch_sync(
    file_stem: str,
    tag: str,
    total_leads: int,
    created: int,
    updated: int,
    tld_blocked: int,
    audit_file: str,
    file_path: str = ""
) -> None:
    """Record successful sync metrics in ledger."""
    ledger = get_freshsales_ledger()
    ledger[file_stem.lower()] = {
        "file_stem": file_stem,
        "file_path": file_path,
        "tag": tag,
        "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_leads": total_leads,
        "created": created,
        "updated": updated,
        "tld_blocked": tld_blocked,
        "audit_file": audit_file,
    }
    save_freshsales_ledger(ledger)


# =====================================================================
# 2. DISCOVERY OF MILLIONVERIFIER GOOD FILES
# =====================================================================

def discover_verified_good_batches(search_dirs: Optional[List[Path]] = None) -> List[Dict[str, Any]]:
    """
    Search exports/ and Downloads for MillionVerifier Good leads files (*_good_*.csv).
    Cross-references with ledger to determine if they are Unprocessed or Synced.
    """
    ledger = get_freshsales_ledger()
    dirs_to_search = search_dirs if search_dirs is not None else [
        PROJECT_ROOT / "exports",
        Path(get_default_downloads_dir()),
    ]

    discovered: List[Dict[str, Any]] = []
    seen_paths = set()

    for sdir in dirs_to_search:
        if not sdir.exists() or not sdir.is_dir():
            continue

        # Look for direct *_good_*.csv and subdirectories
        candidates = list(sdir.glob("*_good_*.csv"))
        # Also check subdirectories created by MillionVerifier
        for sub in sdir.iterdir():
            if sub.is_dir() and "verified" in sub.name.lower():
                candidates.extend(sub.glob("*_good_*.csv"))

        for file_path in candidates:
            if not file_path.is_file():
                continue
            resolved_str = str(file_path.resolve()).lower()
            if resolved_str in seen_paths:
                continue
            seen_paths.add(resolved_str)

            file_stem = file_path.stem
            mod_time = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

            # Quick row count estimate without heavy parsing
            lead_count = 0
            try:
                with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
                    lead_count = max(0, sum(1 for _ in f) - 1)
            except Exception:
                pass

            sync_info = ledger.get(file_stem.lower())
            is_synced = sync_info is not None

            discovered.append({
                "file_path": file_path,
                "file_name": file_path.name,
                "file_stem": file_stem,
                "lead_count": lead_count,
                "mod_time": mod_time,
                "is_synced": is_synced,
                "sync_tag": sync_info.get("tag", "") if sync_info else "",
                "synced_at": sync_info.get("synced_at", "") if sync_info else "",
                "created_count": sync_info.get("created", 0) if sync_info else 0,
                "updated_count": sync_info.get("updated", 0) if sync_info else 0,
            })

    # Sort: unprocessed files first, then newest files
    discovered.sort(key=lambda x: (x["is_synced"], x["mod_time"]), reverse=False)
    # Re-sort so is_synced=False comes first, then by mod_time descending
    discovered.sort(key=lambda x: (not x["is_synced"], x["mod_time"]), reverse=True)
    return discovered


# =====================================================================
# 3. DIRECT FRESHSALES CRM SYNC ENGINE
# =====================================================================

def sync_good_file_to_freshsales(
    good_csv_path: Path,
    custom_tag: Optional[str] = None,
    default_owner_id: Optional[str] = None,
    reports_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Execute the full Freshsales Agent pipeline on a verified Good CSV:
    1. In-file deduplication.
    2. 33-TLD domain filtering (.uk, .ca, .de, .fr, etc.).
    3. CRM pre-lookup & non-overwrite delta updates (preserves existing notes/phones).
    4. Non-destructive tag merging (appends custom_tag to existing tags).
    5. Freshsales Bulk API execution & polling.
    6. SQLite database metrics logging and audit report generation.
    """
    # Import freshsales_agent components inside function to avoid namespace pollution
    from app.config import settings as fs_settings  # type: ignore[import-not-found]
    from app.services.batch_processor import BatchProcessor  # type: ignore[import-not-found]

    print("\n" + "=" * 95)
    print(f"        FRESHSALES CRM AGENT: SYNCING VERIFIED LEADS")
    print(f"        File: {good_csv_path.name}")
    print("=" * 95)

    fs_settings.require_api_key()

    target_reports_dir = reports_dir or (PROJECT_ROOT / "exports" / "freshsales_reports")
    target_reports_dir.mkdir(parents=True, exist_ok=True)

    processor = BatchProcessor(auto_delete_input=False)
    result = processor.process_file(
        file_path=good_csv_path,
        reports_dir=target_reports_dir,
        custom_tag=custom_tag,
        default_owner_id=default_owner_id,
    )

    # Record in persistent ledger
    record_batch_sync(
        file_stem=good_csv_path.stem,
        tag=result.tag_applied,
        total_leads=result.total_input_rows,
        created=result.freshly_created_count,
        updated=result.updated_in_crm_count,
        tld_blocked=result.tld_filtered_count,
        audit_file=result.report_file_path or "",
        file_path=str(good_csv_path),
    )

    print("\n" + "=" * 95)
    print("                      FRESHSALES CRM SYNC SUMMARY")
    print("=" * 95)
    print(f"  • Source File         : {good_csv_path.name}")
    print(f"  • Freshsales Tag Used : '{result.tag_applied}'")
    print(f"  • Total Input Leads   : {result.total_input_rows}")
    print(f"  • Foreign TLDs Blocked: {result.tld_filtered_count} (.uk, .ca, .de, etc.)")
    print(f"  • Freshly Created     : {result.freshly_created_count} new contacts")
    print(f"  • Non-Overwrite Update: {result.updated_in_crm_count} existing contacts (tag merged)")
    print(f"  • Errors / Failures   : {result.failed_errors_count}")
    print(f"  • Audit CSV Report    : {result.report_file_path}")
    print("=" * 95)

    return {
        "status": result.status,
        "tag": result.tag_applied,
        "created": result.freshly_created_count,
        "updated": result.updated_in_crm_count,
        "tld_blocked": result.tld_filtered_count,
        "failed": result.failed_errors_count,
        "total_sent": result.contacts_sent_to_crm,
        "audit_file": result.report_file_path or "",
    }


# =====================================================================
# 4. MANAGE BATCHES INTERACTIVE ACTION (OPTION [F])
# =====================================================================

def freshsales_agent_menu_action(conn) -> None:
    """
    Interactive CLI menu action for Option [F] in manage_batches.py:
    1. Lists all MillionVerifier Good files and shows unprocessed vs synced.
    2. Allows selecting any batch to push to Freshsales.
    3. Prompts for custom tag name before syncing.
    4. Executes non-overwrite sync and records metrics.
    """
    print("\n" + "=" * 95)
    print("             FRESHSALES CRM AGENT: SYNC VERIFIED GOOD LEADS")
    print("=" * 95)
    print("  • Automatically detects Good leads files produced by MillionVerifier.")
    print("  • Prevents overwriting existing Freshsales phone numbers, titles, or notes.")
    print("  • Preserves all existing tags and appends your custom batch tag.")
    print("  • Automatically excludes 33 foreign country domains (.uk, .ca, .de, etc.).")
    print("=" * 95)

    batches = discover_verified_good_batches()
    if not batches:
        print("\n[Notice] No MillionVerifier Good lead files (*_good_*.csv) found in exports/ or Downloads.")
        print("Run Option [8] (Send to MillionVerifier) first to verify an enriched batch.")
        return

    unprocessed_count = sum(1 for b in batches if not b["is_synced"])
    print(f"\nDiscovered {len(batches)} Verified Lead Files ({unprocessed_count} Unprocessed in Freshsales CRM):")
    print("-" * 95)
    print(f"{'#':<3} | {'Verified File / Batch Name':<42} | {'Good Leads':<11} | {'Status in Freshsales'}")
    print("-" * 95)

    for idx, b in enumerate(batches, 1):
        f_name = b["file_name"]
        leads_str = f"{b['lead_count']:,d} leads"
        if b["is_synced"]:
            status_str = f"🟢 Synced (Tag: '{b['sync_tag']}')"
        else:
            status_str = "⚪ UNPROCESSED (Ready to Sync)"
        print(f"[{idx:<2}] | {f_name[:40]:<42} | {leads_str:<11} | {status_str}")

    print("-" * 95)
    print("Options:")
    print(f"  [1-{len(batches)}] Select any verified file by number to sync into Freshsales CRM")
    if unprocessed_count > 1:
        print("  [u] Sync ALL Unprocessed files sequentially")
    print("  [q] Back to main menu")

    choice = input(f"\nSelect option [1-{len(batches)}, u, or q]: ").strip()
    if choice.lower() in ("q", "back", "exit", ""):
        return

    targets_to_sync: List[Dict[str, Any]] = []
    if choice.lower() == "u" and unprocessed_count > 0:
        targets_to_sync = [b for b in batches if not b["is_synced"]]
    elif choice.isdigit() and 1 <= int(choice) <= len(batches):
        targets_to_sync = [batches[int(choice) - 1]]
    else:
        print("[!] Invalid selection.")
        return

    for target in targets_to_sync:
        print(f"\nSelected: '{target['file_name']}' ({target['lead_count']} leads)")
        
        # Suggest clean default tag
        default_tag = target["file_stem"].replace("_good", "").replace("_verified", "")
        # Remove trailing timestamp digits if any
        default_tag = re.sub(r"_\d{8}_\d{6}$", "", default_tag)
        default_tag = f"{default_tag}_{datetime.now().strftime('%b-%Y')}"

        print(f"\nFreshsales CRM Tag Assignment:")
        print("  (This tag will be safely appended to contacts without deleting existing tags)")
        tag_input = input(f"Enter Tag Name for Freshsales (default: '{default_tag}'): ").strip()
        custom_tag = tag_input if tag_input else default_tag

        confirm = input(f"\nReady to push {target['lead_count']} leads into Freshsales with tag '{custom_tag}'? [Y/n, default Y]: ").strip().lower()
        if confirm in ("n", "no"):
            print("Skipped.")
            continue

        try:
            sync_good_file_to_freshsales(
                good_csv_path=target["file_path"],
                custom_tag=custom_tag,
            )
        except Exception as exc:
            print(f"\n[ERROR] Failed to sync to Freshsales: {exc}")


if __name__ == "__main__":
    freshsales_agent_menu_action(None)
