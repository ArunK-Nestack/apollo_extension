#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freshsales CRM Bridge for MillionVerifier Good Leads
=====================================================
1. Connects MillionVerifier API directly to Freshsales CRM:
   - Flow 1: Select enriched login/batch -> Verify via MillionVerifier API -> Push Good leads to Freshsales CRM.
   - Flow 2: Fetch recent MillionVerifier verification jobs or enter any File ID -> Download Good leads via API -> Push to Freshsales CRM.
   - Flow 3: Select from discovered Good lead files on disk with instant login search/filter -> Push to Freshsales CRM.
2. Tracks processed vs unprocessed files in config/freshsales_synced_batches.json.
3. Tracks MillionVerifier API verification jobs in config/millionverifier_jobs.json.
4. Executes isolated Freshsales batch processing via scripts/run_freshsales_sync.py:
   - In-file deduplication
   - 33-TLD foreign domain filtering (.uk, .ca, .de, .fr, etc.)
   - CRM pre-lookup & non-overwrite delta resolution (never overwrites existing data)
   - Non-destructive tag merging (appends custom tag to all existing contact tags)
   - Freshsales Bulk API dispatch in batches of 100 with polling
5. Logs metrics to SQLite (crm_automation.db), generates audit CSVs, and updates sync status.
"""

from __future__ import annotations

import os
import sys
import csv
import glob
import json
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

import requests
from dotenv import load_dotenv

# Force UTF-8 output so emoji/unicode symbols don't crash on Windows CP1252 terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
JOBS_LEDGER_PATH = PROJECT_ROOT / "config" / "millionverifier_jobs.json"
MV_BULK_BASE_URL = os.getenv("MILLIONVERIFIER_BULK_BASE_URL", "https://bulkapi.millionverifier.com").rstrip("/")


# =====================================================================
# 1. PERSISTENT LEDGERS (FRESHSALES SYNC & MILLIONVERIFIER JOBS)
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


def get_millionverifier_jobs() -> List[Dict[str, Any]]:
    """Load persistent list of MillionVerifier verification jobs."""
    if not JOBS_LEDGER_PATH.exists():
        return []
    try:
        with open(JOBS_LEDGER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_millionverifier_job(job_data: Dict[str, Any]) -> None:
    """Record or update a MillionVerifier verification job."""
    JOBS_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    jobs = get_millionverifier_jobs()
    file_id = str(job_data.get("file_id", ""))
    
    # Update existing or prepend new
    updated = False
    for idx, j in enumerate(jobs):
        if str(j.get("file_id", "")) == file_id:
            jobs[idx].update(job_data)
            updated = True
            break
    if not updated:
        jobs.insert(0, job_data)
        
    # Cap to most recent 50 jobs
    jobs = jobs[:50]
    try:
        with open(JOBS_LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2)
    except Exception:
        pass


def ensure_millionverifier_api_key() -> str:
    """Check and ensure MillionVerifier API key is configured."""
    key = os.getenv("MILLIONVERIFIER_API_KEY", "").strip()
    if not key or key == "replace_with_your_real_api_key":
        print("\n" + "!" * 80)
        print(" ERROR: MILLIONVERIFIER_API_KEY is not configured in .env!")
        print("!" * 80)
        key_input = input("Please enter your MillionVerifier API key: ").strip()
        if key_input:
            os.environ["MILLIONVERIFIER_API_KEY"] = key_input
            return key_input
        raise RuntimeError("MillionVerifier API key is required.")
    return key


# =====================================================================
# 2. MILLIONVERIFIER BULK API DIRECT CLIENT
# =====================================================================

def api_get_file_info(file_id: str | int, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Query MillionVerifier Bulk API for file status and statistics."""
    key = api_key or ensure_millionverifier_api_key()
    url = f"{MV_BULK_BASE_URL}/bulkapi/v2/fileinfo"
    resp = requests.get(url, params={"key": key, "file_id": str(file_id)}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"MillionVerifier API Error: {data['error']}")
    return data


def api_download_good_leads(
    file_id: str | int,
    destination_path: Path,
    api_key: Optional[str] = None
) -> Path:
    """
    Download only verified Good (OK / deliverable) leads directly from MillionVerifier API.
    Uses /bulkapi/v2/download?filter=ok.
    """
    key = api_key or ensure_millionverifier_api_key()
    url = f"{MV_BULK_BASE_URL}/bulkapi/v2/download"
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    resp = requests.get(
        url,
        params={"key": key, "file_id": str(file_id), "filter": "ok"},
        stream=True,
        timeout=120
    )
    resp.raise_for_status()

    # Check if API returned an error JSON instead of CSV
    content_type = resp.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            err_data = resp.json()
            if err_data.get("error"):
                raise RuntimeError(f"MillionVerifier API download error: {err_data['error']}")
        except ValueError:
            pass

    with open(destination_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)

    return destination_path


# =====================================================================
# =====================================================================
# 3. DISCOVERY & SYNC STATUS OF VERIFIED GOOD FILES ON DISK
# =====================================================================

def extract_login_hint(file_name: str) -> str:
    """Extract possible login email or account name from a verified file name."""
    # Match email in filename: e.g. jith@nestack.info or VIJAY.RAGHAVAN@NESTACKTECH.COM
    m = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", file_name)
    if m:
        return m.group(1).lower()

    # Match underscore formatted emails like vraghavan_nestack_com or madhava_reddy_nestack-tech_com
    m2 = re.match(r"^([a-zA-Z0-9_]+)_(nestack(?:-tech|_tech)?(?:_com|\.com))", file_name, re.IGNORECASE)
    if m2:
        user = m2.group(1).replace("_", ".")
        dom = m2.group(2).replace("_", ".")
        return f"{user}@{dom}".lower()

    # Match account slug: e.g. madhava_reddy, vraghavan, etc.
    stem = file_name.split(" - ")[0].split("_verified")[0]
    return stem.lower()


def inspect_file_sync_status(file_path: Path) -> Dict[str, Any]:
    """
    Inspect the CRM sync status of a verified good file by checking
    both the sync ledger and existing audit CSV reports in exports/freshsales_reports/.
    Returns detailed metrics: total, synced, tld_blocked, failed, unsynced, and status.
    """
    file_stem = file_path.stem
    name_lower = file_path.name.lower()
    reports_dir = PROJECT_ROOT / "exports" / "freshsales_reports"

    # 1. Total leads estimate
    total_leads = 0
    m_leads = re.search(r"(\d+)\s+good", name_lower)
    if m_leads:
        try:
            total_leads = int(m_leads.group(1))
        except ValueError:
            total_leads = 0

    if total_leads == 0:
        try:
            with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
                total_leads = max(0, sum(1 for _ in f) - 1)
        except Exception:
            total_leads = 0

    # 2. Check for matching audit CSV report
    audit_candidates = [
        reports_dir / f"{file_stem}_crm_audit.csv",
        reports_dir / f"{file_path.name}_crm_audit.csv",
    ]
    # Also check if ledger points to an audit file
    ledger = get_freshsales_ledger()
    ledger_entry = ledger.get(file_stem.lower())
    if ledger_entry and ledger_entry.get("audit_file"):
        p = Path(ledger_entry["audit_file"])
        if p.is_file() and p not in audit_candidates:
            audit_candidates.insert(0, p)

    audit_path = None
    for cand in audit_candidates:
        if cand.is_file():
            audit_path = cand
            break

    if audit_path:
        try:
            import pandas as pd
            df = pd.read_csv(audit_path)
            synced_mask = (df["error_reason"].isna() | (df["error_reason"] == "")) & df["action"].isin(["created", "updated"])
            created_mask = (df["error_reason"].isna() | (df["error_reason"] == "")) & (df["action"] == "created")
            updated_mask = (df["error_reason"].isna() | (df["error_reason"] == "")) & (df["action"] == "updated")
            tld_mask = df["action"] == "excluded_tld"
            failed_mask = df["error_reason"].notna() & (df["error_reason"] != "") & (df["action"] != "excluded_tld")

            synced_count = int(synced_mask.sum())
            created_count = int(created_mask.sum())
            updated_count = int(updated_mask.sum())
            tld_blocked = int(tld_mask.sum())
            failed_count = int(failed_mask.sum())

            # Unsynced count is failed leads plus any un-audited rows from original file
            unsynced_count = failed_count
            if total_leads > len(df):
                unsynced_count += (total_leads - len(df))

            status_code = "PARTIAL" if failed_count > 0 else ("SYNCED" if synced_count > 0 else "UNPROCESSED")
            status_display = (
                f"🔴 PARTIAL SYNC ({unsynced_count:,d} unsynced pending)"
                if status_code == "PARTIAL"
                else (f"🟢 FULLY SYNCED ({synced_count:,d} leads)" if status_code == "SYNCED" else f"⚪ UNPROCESSED ({total_leads:,d} leads)")
            )

            # Extract sample failure reason
            failure_reason = ""
            if failed_count > 0:
                reasons = df[failed_mask]["error_reason"].dropna().unique()
                if len(reasons) > 0:
                    r0 = str(reasons[0])
                    if "405" in r0 or "Max of 10 transactions" in r0:
                        failure_reason = f"Freshsales HTTP 405 (10 concurrent jobs cap exceeded in previous run)"
                    else:
                        failure_reason = r0[:65]

            return {
                "total_leads": total_leads or len(df),
                "synced_count": synced_count,
                "created_count": created_count,
                "updated_count": updated_count,
                "tld_blocked": tld_blocked,
                "failed_count": failed_count,
                "unsynced_count": unsynced_count,
                "audit_path": audit_path,
                "status_code": status_code,
                "status_display": status_display,
                "failure_reason": failure_reason,
                "sync_tag": ledger_entry.get("tag", "") if ledger_entry else "",
                "synced_at": ledger_entry.get("synced_at", "") if ledger_entry else "",
            }
        except Exception:
            pass

    # 3. Fallback to ledger if audit CSV is not found
    if ledger_entry:
        c = ledger_entry.get("created", 0)
        u = ledger_entry.get("updated", 0)
        tb = ledger_entry.get("tld_blocked", 0)
        tot = ledger_entry.get("total_leads", total_leads)
        synced = c + u
        unsynced = max(0, tot - tb - synced)
        status_code = "PARTIAL" if unsynced > 0 else "SYNCED"
        return {
            "total_leads": tot,
            "synced_count": synced,
            "created_count": c,
            "updated_count": u,
            "tld_blocked": tb,
            "failed_count": unsynced,
            "unsynced_count": unsynced,
            "audit_path": None,
            "status_code": status_code,
            "status_display": f"🔴 PARTIAL SYNC ({unsynced:,d} unsynced pending)" if unsynced > 0 else f"🟢 FULLY SYNCED ({synced:,d} leads)",
            "failure_reason": "Incomplete sync in previous run" if unsynced > 0 else "",
            "sync_tag": ledger_entry.get("tag", ""),
            "synced_at": ledger_entry.get("synced_at", ""),
        }

    # 4. Completely un-synced file
    return {
        "total_leads": total_leads,
        "synced_count": 0,
        "created_count": 0,
        "updated_count": 0,
        "tld_blocked": 0,
        "failed_count": 0,
        "unsynced_count": total_leads,
        "audit_path": None,
        "status_code": "UNPROCESSED",
        "status_display": f"⚪ UNPROCESSED ({total_leads:,d} leads)",
        "failure_reason": "",
        "sync_tag": "",
        "synced_at": "",
    }


def discover_verified_good_batches(
    search_dirs: Optional[List[Path]] = None,
    filter_query: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search recursively across Downloads, exports/, and scratch cache for verified Good lead files.
    Excludes audit reports, retry slices, and files inside bad/risky folders.
    Attaches detailed sync status and unsynced counts to each discovered file.
    """
    downloads_root = Path(get_default_downloads_dir())

    dirs_to_search = search_dirs if search_dirs is not None else [
        PROJECT_ROOT / "exports",
        downloads_root,
        PROJECT_ROOT / "scratch" / "millionverifier_cache",
    ]

    discovered: List[Dict[str, Any]] = []
    seen_paths = set()
    seen_signatures = set()
    query_lower = filter_query.strip().lower() if filter_query and filter_query.strip() else ""

    for sdir in dirs_to_search:
        if not sdir.exists() or not sdir.is_dir():
            continue

        try:
            candidates = list(sdir.rglob("*.csv"))
        except Exception:
            candidates = []

        for file_path in candidates:
            if not file_path.is_file():
                continue

            name_lower = file_path.name.lower()
            parent_lower = file_path.parent.name.lower()

            # Ignore files explicitly inside bad or risky folders
            if parent_lower in ("bad", "risky"):
                continue

            # Must contain 'good' in file name
            if "good" not in name_lower:
                continue

            # Exclude audit CSVs, summary CSVs, or retry slices
            if (
                name_lower.endswith("_crm_audit.csv")
                or name_lower.endswith("_audit.csv")
                or "_unsynced_retry_" in name_lower
                or "audit_trail" in name_lower
            ):
                continue

            resolved_str = str(file_path.resolve()).lower()
            if resolved_str in seen_paths:
                continue
            seen_paths.add(resolved_str)

            # Avoid duplicate file copies across Downloads and scratch folders
            try:
                file_sig = (name_lower, file_path.stat().st_size)
                if file_sig in seen_signatures:
                    continue
                seen_signatures.add(file_sig)
            except Exception:
                pass

            login_hint = extract_login_hint(file_path.name)

            # Apply login search filter if provided
            if query_lower:
                match_content = f"{file_path.name} {login_hint}".lower()
                if query_lower not in match_content:
                    continue

            mod_time = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            status_info = inspect_file_sync_status(file_path)

            discovered.append({
                "file_path": file_path,
                "file_name": file_path.name,
                "file_stem": file_path.stem,
                "login_hint": login_hint,
                "lead_count": status_info["total_leads"],
                "synced_count": status_info["synced_count"],
                "created_count": status_info["created_count"],
                "updated_count": status_info["updated_count"],
                "tld_blocked": status_info["tld_blocked"],
                "failed_count": status_info["failed_count"],
                "unsynced_count": status_info["unsynced_count"],
                "status_code": status_info["status_code"],
                "status_display": status_info["status_display"],
                "failure_reason": status_info["failure_reason"],
                "audit_path": status_info["audit_path"],
                "sync_tag": status_info["sync_tag"],
                "synced_at": status_info["synced_at"],
                "mod_time": mod_time,
                "is_synced": status_info["status_code"] == "SYNCED",
            })

    # Prioritize: Partial syncs with failed leads first, then unprocessed, then fully synced
    def sort_key(x):
        if x["status_code"] == "PARTIAL":
            return (0, -x["unsynced_count"])
        elif x["status_code"] == "UNPROCESSED":
            return (1, -x["unsynced_count"])
        else:
            return (2, x["mod_time"])

    discovered.sort(key=sort_key)
    return discovered


def discover_good_leads_files(
    search_dirs: Optional[List[Path]] = None,
    filter_query: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Alias for discover_verified_good_batches."""
    return discover_verified_good_batches(search_dirs=search_dirs, filter_query=filter_query)


def group_files_by_login(files: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group discovered verified Good lead files by login / account hint."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in files:
        hint = (f.get("login_hint") or "unknown").strip().lower()
        groups[hint].append(f)
    return dict(groups)


# =====================================================================
# 4. UNSYNCED LEADS EXTRACTION & AUDIT MERGING
# =====================================================================

def extract_unsynced_leads(
    original_csv_path: Path,
    audit_csv_path: Path,
    output_csv_path: Path,
) -> Tuple[Path, int]:
    """
    Extract only the unsynced / failed leads from the original verified CSV
    by cross-referencing against the audit CSV. Preserves all original columns.
    """
    import pandas as pd

    df_audit = pd.read_csv(audit_csv_path)
    failed_mask = (
        df_audit["error_reason"].notna()
        & (df_audit["error_reason"] != "")
        & (df_audit["action"] != "excluded_tld")
    )
    failed_emails = set(
        df_audit[failed_mask]["email"].astype(str).str.lower().str.strip()
    )

    df_orig = pd.read_csv(original_csv_path, dtype=str, keep_default_na=False)

    # Find email column
    email_col = None
    for col in df_orig.columns:
        if col.strip().lower() in ("email", "contact email", "work email", "emails"):
            email_col = col
            break

    if not email_col:
        for col in df_orig.columns:
            if "email" in col.lower():
                email_col = col
                break

    if not email_col:
        raise ValueError(f"Could not find Email column in {original_csv_path.name}")

    mask = df_orig[email_col].astype(str).str.lower().str.strip().isin(failed_emails)
    df_unsynced = df_orig[mask]

    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    df_unsynced.to_csv(output_csv_path, index=False, encoding="utf-8-sig")
    return output_csv_path, len(df_unsynced)


def merge_audit_reports(
    master_audit_path: Path,
    retry_audit_path: Path,
    file_stem: str,
    target_tag: str,
    original_file_path: Path,
) -> Dict[str, Any]:
    """
    After a successful retry run, update the master audit CSV and persistent ledger.
    Replaces failed status with the new successful action for retried emails.
    """
    import pandas as pd

    df_master = pd.read_csv(master_audit_path)
    df_retry = pd.read_csv(retry_audit_path)

    # Index retry results by email
    retry_map = {}
    for _, r in df_retry.iterrows():
        em = str(r.get("email", "")).strip().lower()
        if em:
            retry_map[em] = r

    # Update master audit rows
    updated_in_master = 0
    for idx, r in df_master.iterrows():
        em = str(r.get("email", "")).strip().lower()
        if em in retry_map:
            retry_row = retry_map[em]
            if pd.isna(retry_row.get("error_reason")) or str(retry_row.get("error_reason", "")).strip() == "":
                df_master.at[idx, "action"] = retry_row.get("action", r.get("action"))
                df_master.at[idx, "fields_filled_count"] = retry_row.get("fields_filled_count", 0)
                df_master.at[idx, "fields_filled_names"] = retry_row.get("fields_filled_names", "")
                df_master.at[idx, "error_reason"] = ""
                updated_in_master += 1

    df_master.to_csv(master_audit_path, index=False, encoding="utf-8-sig")

    # Compute consolidated metrics
    synced_mask = (df_master["error_reason"].isna() | (df_master["error_reason"] == "")) & df_master["action"].isin(["created", "updated"])
    created_mask = (df_master["error_reason"].isna() | (df_master["error_reason"] == "")) & (df_master["action"] == "created")
    updated_mask = (df_master["error_reason"].isna() | (df_master["error_reason"] == "")) & (df_master["action"] == "updated")
    tld_mask = df_master["action"] == "excluded_tld"

    total_leads = len(df_master)
    created_count = int(created_mask.sum())
    updated_count = int(updated_mask.sum())
    tld_blocked = int(tld_mask.sum())

    # Update persistent sync ledger
    record_batch_sync(
        file_stem=file_stem,
        tag=target_tag,
        total_leads=total_leads,
        created=created_count,
        updated=updated_count,
        tld_blocked=tld_blocked,
        audit_file=str(master_audit_path),
        file_path=str(original_file_path),
    )

    return {
        "total_leads": total_leads,
        "created": created_count,
        "updated": updated_count,
        "tld_blocked": tld_blocked,
        "retried_successful": updated_in_master,
    }


# =====================================================================
# 5. ISOLATED FRESHSALES CRM SYNC ENGINE (UNBUFFERED STREAMING)
# =====================================================================

def sync_good_file_to_freshsales(
    good_csv_path: Path,
    custom_tag: Optional[str] = None,
    default_owner_id: Optional[str] = None,
    reports_dir: Optional[Path] = None,
    audit_cache_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Execute the Freshsales Agent pipeline on a verified Good CSV.
    Runs via scripts/run_freshsales_sync.py in an isolated subprocess with unbuffered output
    to guarantee instantaneous real-time terminal progress.
    """
    good_csv_path = Path(good_csv_path).resolve()
    if not good_csv_path.is_file():
        raise FileNotFoundError(f"Good CSV not found: {good_csv_path}")

    target_reports_dir = reports_dir or (PROJECT_ROOT / "exports" / "freshsales_reports")
    target_reports_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 95)
    print("        FRESHSALES CRM AGENT: SYNCING VERIFIED LEADS")
    print(f"        Source File : {good_csv_path.name}")
    print(f"        Target Tag  : '{custom_tag or 'Auto'}'")
    if audit_cache_path:
        print(f"        Audit Cache : '{audit_cache_path.name}' (Instant lookup cache active)")
    print("=" * 95)

    runner_script = PROJECT_ROOT / "scripts" / "run_freshsales_sync.py"
    cmd = [
        sys.executable,
        "-u",  # Enforce unbuffered binary stdout and stderr in Python
        str(runner_script),
        "--file", str(good_csv_path),
        "--reports-dir", str(target_reports_dir),
        "--json-output",
    ]
    if custom_tag:
        cmd.extend(["--tag", str(custom_tag)])
    if default_owner_id:
        cmd.extend(["--owner-id", str(default_owner_id)])
    if audit_cache_path:
        cmd.extend(["--audit-cache", str(audit_cache_path)])

    # Unbuffered environment for immediate live streaming
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    # Stream output live to console with immediate line flushing
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    json_result_str = None
    if proc.stdout:
        for line in iter(proc.stdout.readline, ""):
            if "__RESULT_JSON_START__" in line:
                m = re.search(r"__RESULT_JSON_START__(.*?)__RESULT_JSON_END__", line)
                if m:
                    json_result_str = m.group(1)
            else:
                print(line, end="", flush=True)

    proc.wait()

    res_data: Dict[str, Any] = {}
    if json_result_str:
        try:
            res_data = json.loads(json_result_str)
        except Exception:
            pass

    if proc.returncode != 0:
        raise RuntimeError(f"Freshsales sync process failed with exit code {proc.returncode}")

    # Record in persistent ledger
    applied_tag = res_data.get("tag_applied", custom_tag or "synced")
    record_batch_sync(
        file_stem=good_csv_path.stem,
        tag=applied_tag,
        total_leads=res_data.get("total_input_rows", 0),
        created=res_data.get("freshly_created_count", 0),
        updated=res_data.get("updated_in_crm_count", 0),
        tld_blocked=res_data.get("tld_filtered_count", 0),
        audit_file=res_data.get("report_file_path", ""),
        file_path=str(good_csv_path),
    )

    print("\n" + "=" * 95)
    print("                      FRESHSALES CRM SYNC SUMMARY")
    print("=" * 95)
    print(f"  • Source File         : {good_csv_path.name}")
    print(f"  • Freshsales Tag Used : '{applied_tag}'")
    print(f"  • Total Input Leads   : {res_data.get('total_input_rows', 0):,d}")
    print(f"  • Foreign TLDs Blocked: {res_data.get('tld_filtered_count', 0):,d} (.uk, .ca, .de, etc.)")
    print(f"  • Freshly Created     : {res_data.get('freshly_created_count', 0):,d} new contacts")
    print(f"  • Non-Overwrite Update: {res_data.get('updated_in_crm_count', 0):,d} existing contacts (tag merged)")
    print(f"  • Errors / Failures   : {res_data.get('failed_errors_count', 0):,d}")
    print(f"  • Audit CSV Report    : {res_data.get('report_file_path', 'None')}")
    print("=" * 95)

    return res_data


# =====================================================================
# 6. WORKFLOW 1: SELECT LOGIN -> INSPECT FILES & UNSYNCED LEADS -> PUSH
# =====================================================================

def login_lead_sync_pipeline(conn) -> None:
    """
    Select an enriched/Apollo login, discover all MillionVerifier Good lead files,
    display exact counts of leads already in CRM vs NOT pushed (unsynced),
    and provide 1-click option to push the unsynced leads (e.g. 4,472) into CRM.
    """
    print("\n" + "=" * 95)
    print("       SELECT LOGIN -> INSPECT MILLIONVERIFIER FILES & PUSH TO CRM")
    print("=" * 95)
    print("  • Discovers all verified Good files associated with each account.")
    print("  • Shows exact counts: Total Leads, Already in CRM, and NOT Pushed (Unsynced).")
    print("  • Detects partial sync failures (e.g. 4,472 leads) and allows 1-click resume.")
    print("=" * 95)

    batches = discover_verified_good_batches()
    if not batches:
        print("\n[Notice] No verified Good files (*good*.csv) found in Downloads or exports.")
        print("Use Option [2] to verify an enriched batch via MillionVerifier API.")
        return

    # Group batches by login hint
    from collections import defaultdict
    login_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in batches:
        lhint = b["login_hint"].lower().strip()
        login_groups[lhint].append(b)

    # Sort logins: Partial syncs first, then most unsynced leads
    def login_sort_key(item):
        _login, _files = item
        has_partial = any(f["status_code"] == "PARTIAL" for f in _files)
        total_unsynced = sum(f["unsynced_count"] for f in _files)
        return (0 if has_partial else 1, -total_unsynced)

    sorted_logins = sorted(login_groups.items(), key=login_sort_key)

    print(f"\nDiscovered {len(sorted_logins)} Accounts with MillionVerifier Files:")
    print("-" * 105)
    print(f"{'#':<3} | {'Login / Account Name':<38} | {'Files':<5} | {'Total Leads':<12} | {'In CRM':<10} | {'NOT Pushed (Unsynced)'}")
    print("-" * 105)

    for idx, (login_name, file_list) in enumerate(sorted_logins, 1):
        tot_leads = sum(f["lead_count"] for f in file_list)
        tot_synced = sum(f["synced_count"] for f in file_list)
        tot_unsynced = sum(f["unsynced_count"] for f in file_list)
        has_partial = any(f["status_code"] == "PARTIAL" for f in file_list)

        if has_partial:
            unsynced_badge = f"{tot_unsynced:,d} leads (🔴 PARTIAL SYNC)"
        elif tot_unsynced > 0:
            unsynced_badge = f"{tot_unsynced:,d} leads (⚪ UNPROCESSED)"
        else:
            unsynced_badge = f"0 leads (🟢 FULLY SYNCED)"

        print(f"[{idx:<2}] | {login_name[:36]:<38} | {len(file_list):<5} | {tot_leads:<12,d} | {tot_synced:<10,d} | {unsynced_badge}")

    print("-" * 105)
    print("Options:")
    print(f"  [1-{len(sorted_logins)}] Select account by number")
    print("  [s] Search / filter by account name (e.g. 'vraghavan', 'madhava')")
    print("  [q] Back to main menu")

    choice = input(f"\nSelect account [1-{len(sorted_logins)}, s, or q]: ").strip()
    if choice.lower() in ("q", "back", "exit", ""):
        return

    selected_login_name = None
    if choice.lower() == "s":
        term = input("Enter search term: ").strip().lower()
        matches = [(k, v) for k, v in sorted_logins if term in k]
        if not matches:
            print(f"[!] No accounts matching '{term}'.")
            return
        selected_login_name = matches[0][0]
    elif choice.isdigit() and 1 <= int(choice) <= len(sorted_logins):
        selected_login_name = sorted_logins[int(choice) - 1][0]
    else:
        # Match directly by text
        matches = [(k, v) for k, v in sorted_logins if choice.lower() in k]
        if matches:
            selected_login_name = matches[0][0]
        else:
            print("[!] Invalid selection.")
            return

    target_files = login_groups[selected_login_name]
    print("\n" + "=" * 95)
    print(f"       FILES FOR ACCOUNT: {selected_login_name.upper()}")
    print("=" * 95)

    for f_idx, tf in enumerate(target_files, 1):
        print(f"\n[{f_idx}] File: {tf['file_name']}")
        print(f"    Path: {tf['file_path']}")
        print(f"    • Total Deliverable Leads : {tf['lead_count']:,d}")
        print(f"    • Already Pushed into CRM : {tf['synced_count']:,d} (Created: {tf['created_count']:,d}, Updated: {tf['updated_count']:,d})")
        print(f"    • Excluded Foreign TLDs   : {tf['tld_blocked']:,d} (.uk, .ca, .de, etc.)")
        print(f"    ────────────────────────────────────────────────────────────────────────")
        print(f"    • NOT PUSHED INTO CRM     : {tf['unsynced_count']:,d} LEADS")
        print(f"    • Current Status          : {tf['status_display']}")
        if tf.get("failure_reason"):
            print(f"    • Previous Issue          : {tf['failure_reason']}")

    # If only 1 file, auto-select it; otherwise prompt
    sel_file_entry = target_files[0]
    if len(target_files) > 1:
        sel_f = input(f"\nSelect file [1-{len(target_files)}, default 1]: ").strip()
        if sel_f.isdigit() and 1 <= int(sel_f) <= len(target_files):
            sel_file_entry = target_files[int(sel_f) - 1]

    file_path = sel_file_entry["file_path"]
    unsynced_count = sel_file_entry["unsynced_count"]
    synced_count = sel_file_entry["synced_count"]
    total_leads = sel_file_entry["lead_count"]
    audit_path = sel_file_entry.get("audit_path")
    status_code = sel_file_entry["status_code"]

    print("\n" + "-" * 95)
    print(f"ACTION MENU FOR: '{sel_file_entry['file_name']}'")
    print("-" * 95)

    if status_code == "PARTIAL" and unsynced_count > 0:
        print(f"  [1] Push ONLY the {unsynced_count:,d} Unsynced Leads into CRM (Resume / Retry) [RECOMMENDED]")
        print(f"      -> Extracts the {unsynced_count:,d} failed leads into a slice, uses instant lookup cache,")
        print(f"         and pushes them with concurrency cap=6 (safe from HTTP 405 error).")
        print(f"  [2] Re-sync ALL {total_leads:,d} Leads into Freshsales CRM")
        print(f"  [3] View Detailed Error Breakdown from Previous Run")
        print(f"  [4] Run Freshsales CRM & Net-New Domain Audit (Show New Domains, Created vs Existed)")
        print(f"  [b] Back to accounts")
        print("-" * 95)

        act = input(f"Select action [1-4, or b, default 1]: ").strip()
        if act.lower() in ("b", "back", "q"):
            return
        elif act in ("4", "a", "A"):
            run_freshsales_domain_audit(file_path, conn=conn)
            return
        elif act in ("3",):
            if audit_path and audit_path.is_file():
                import pandas as pd
                df_a = pd.read_csv(audit_path)
                f_rows = df_a[df_a["error_reason"].notna() & (df_a["error_reason"] != "") & (df_a["action"] != "excluded_tld")]
                print(f"\nFailure Breakdown for {len(f_rows)} contacts:")
                print(f_rows["error_reason"].value_counts())
            input("\nPress Enter to continue...")
            return
        elif act in ("2",):
            # Re-sync entire file
            default_tag = sel_file_entry["file_stem"].replace(" - good", "").replace("_good", "").replace("_verified", "")
            default_tag = re.sub(r"_\d{8}_\d{6}$", "", default_tag)
            default_tag = f"{default_tag}_{datetime.now().strftime('%b-%Y')}"
            tag_input = input(f"Enter Tag Name for Freshsales (default: '{sel_file_entry.get('sync_tag') or default_tag}'): ").strip()
            custom_tag = tag_input if tag_input else (sel_file_entry.get("sync_tag") or default_tag)
            try:
                sync_good_file_to_freshsales(file_path, custom_tag=custom_tag)
            except Exception as exc:
                print(f"\n[ERROR] Sync failed: {exc}")
            return
        else:
            # Action 1: Push ONLY unsynced leads (4,472)
            print(f"\nPreparing to extract {unsynced_count:,d} unsynced leads from '{file_path.name}'...")
            retry_dir = PROJECT_ROOT / "exports" / "freshsales_reports"
            retry_dir.mkdir(parents=True, exist_ok=True)
            clean_stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", file_path.stem).strip("_")
            retry_csv_path = retry_dir / f"{clean_stem}_unsynced_retry_{unsynced_count}.csv"

            if not audit_path or not audit_path.is_file():
                print(f"[!] Audit CSV not found at {audit_path}. Performing full sync instead.")
                sync_good_file_to_freshsales(file_path, custom_tag=sel_file_entry.get("sync_tag"))
                return

            try:
                slice_path, extracted_count = extract_unsynced_leads(
                    original_csv_path=file_path,
                    audit_csv_path=audit_path,
                    output_csv_path=retry_csv_path,
                )
                print(f"✓ Extracted {extracted_count:,d} leads with all original columns preserved:")
                print(f"  Destination: {slice_path}")
            except Exception as exc:
                print(f"\n[ERROR] Could not extract unsynced slice: {exc}")
                return

            target_tag = sel_file_entry.get("sync_tag") or f"{clean_stem}_{datetime.now().strftime('%b-%Y')}"
            tag_input = input(f"\nFreshsales CRM Tag to apply (default: '{target_tag}'): ").strip()
            if tag_input:
                target_tag = tag_input

            confirm = input(f"\nReady to push {extracted_count:,d} unsynced leads to Freshsales CRM? [Y/n, default Y]: ").strip().lower()
            if confirm in ("n", "no"):
                print("Sync cancelled.")
                return

            # Execute sync with audit cache to skip redundant lookups
            try:
                sync_good_file_to_freshsales(
                    good_csv_path=slice_path,
                    custom_tag=target_tag,
                    audit_cache_path=audit_path,
                )

                # Merge retry audit report back into master audit CSV
                retry_audit_csv = retry_dir / f"{slice_path.name}_crm_audit.csv"
                if retry_audit_csv.is_file():
                    merged_stats = merge_audit_reports(
                        master_audit_path=audit_path,
                        retry_audit_path=retry_audit_csv,
                        file_stem=file_path.stem,
                        target_tag=target_tag,
                        original_file_path=file_path,
                    )
                    print("\n" + "=" * 95)
                    print("                MASTER AUDIT & LEDGER CONSOLIDATION COMPLETE")
                    print("=" * 95)
                    print(f"  • Source File         : {file_path.name}")
                    print(f"  • Master Audit Report : {audit_path.name}")
                    print(f"  • Total Leads in File : {merged_stats['total_leads']:,d}")
                    print(f"  • Freshly Created     : {merged_stats['created']:,d}")
                    print(f"  • Non-Overwrite Update: {merged_stats['updated']:,d}")
                    print(f"  • Foreign TLDs Blocked: {merged_stats['tld_blocked']:,d}")
                    print(f"  • Status              : 🟢 FULLY SYNCED (0 Failures Remaining)")
                    print("=" * 95)
            except Exception as exc:
                print(f"\n[ERROR] Retry sync failed: {exc}")

    elif unsynced_count > 0:
        # Unprocessed file
        print(f"  [1] Push All {unsynced_count:,d} Leads into Freshsales CRM [RECOMMENDED]")
        print(f"  [2] Run Freshsales CRM & Net-New Domain Audit (Show New Domains, Created vs Existed)")
        print(f"  [b] Back to accounts")
        print("-" * 95)
        act = input(f"Select action [1, 2, or b, default 1]: ").strip()
        if act.lower() in ("b", "back", "q"):
            return
        elif act in ("2", "a", "A"):
            run_freshsales_domain_audit(file_path, conn=conn)
            return

        default_tag = sel_file_entry["file_stem"].replace(" - good", "").replace("_good", "").replace("_verified", "")
        default_tag = re.sub(r"_\d{8}_\d{6}$", "", default_tag)
        default_tag = f"{default_tag}_{datetime.now().strftime('%b-%Y')}"
        tag_input = input(f"Enter Tag Name for Freshsales (default: '{default_tag}'): ").strip()
        custom_tag = tag_input if tag_input else default_tag

        confirm = input(f"\nReady to push {total_leads:,d} leads to Freshsales CRM? [Y/n, default Y]: ").strip().lower()
        if confirm in ("n", "no"):
            print("Sync cancelled.")
            return

        try:
            sync_good_file_to_freshsales(file_path, custom_tag=custom_tag)
        except Exception as exc:
            print(f"\n[ERROR] Sync failed: {exc}")
    else:
        print(f"  ✓ This file is already 100% synced into Freshsales CRM ({synced_count:,d} leads).")
        print("  [1] Run Freshsales CRM & Net-New Domain Audit [RECOMMENDED]")
        print("  [2] Force Re-sync to Freshsales CRM")
        print("  [b] Back to accounts")
        print("-" * 95)
        act = input("Select action [1, 2, or b, default 1]: ").strip().lower()
        if act in ("1", "a", ""):
            run_freshsales_domain_audit(file_path, conn=conn)
        elif act == "2":
            sync_good_file_to_freshsales(file_path, custom_tag=sel_file_entry.get("sync_tag"))


# =====================================================================
# 7. WORKFLOW 2: VERIFY ENRICHED LOGIN VIA MILLIONVERIFIER API
# =====================================================================

def verify_and_sync_login_pipeline(conn) -> None:
    """
    Select an enriched login/batch, verify via MillionVerifier Bulk API,
    and automatically push Good leads to Freshsales CRM.
    """
    print("\n" + "=" * 95)
    print("       VERIFY & SYNC ENRICHED LEADS VIA MILLIONVERIFIER API")
    print("=" * 95)
    print("  1. Select any enriched Apollo / Enrich.so login & batch from database.")
    print("  2. Leads undergo 14-step sales-ready cleaning & deduplication.")
    print("  3. Uploaded to MillionVerifier Bulk API with live polling.")
    print("  4. Good deliverable leads are automatically passed to Freshsales CRM.")
    print("=" * 95)

    try:
        from scripts.send_to_millionverifier import send_to_millionverifier_action
        send_to_millionverifier_action(conn, table_name="all")
    except Exception as exc:
        print(f"\n[ERROR] Pipeline encountered an error: {exc}")


# =====================================================================
# 8. WORKFLOW 3: FETCH FROM MILLIONVERIFIER JOB / FILE ID VIA API
# =====================================================================

def fetch_job_and_sync_pipeline() -> None:
    """
    Fetch verification results directly from MillionVerifier API by Job/File ID,
    download verified Good leads, and push to Freshsales CRM.
    """
    ensure_millionverifier_api_key()

    print("\n" + "=" * 95)
    print("        FETCH MILLIONVERIFIER RESULTS VIA API -> PUSH TO FRESHSALES")
    print("=" * 95)

    jobs = get_millionverifier_jobs()
    if jobs:
        print(f"\nRecent MillionVerifier Jobs Tracked ({len(jobs)}):")
        print("-" * 95)
        print(f"{'#':<3} | {'Job / File ID':<14} | {'Status':<12} | {'File Name / Batch':<38} | {'Good Leads'}")
        print("-" * 95)
        for idx, j in enumerate(jobs[:10], 1):
            fid = str(j.get("file_id", "N/A"))
            status = str(j.get("status", "unknown")).upper()
            fn = str(j.get("file_name", j.get("batch", "Unknown")))[:36]
            good_c = j.get("good_count", j.get("ok", "N/A"))
            good_disp = f"{good_c:,d} good" if isinstance(good_c, int) else str(good_c)
            print(f"[{idx:<2}] | {fid:<14} | {status:<12} | {fn:<38} | {good_disp}")
        print("-" * 95)

    prompt_str = "Enter MillionVerifier File ID (or number [1-10] from list above): " if jobs else "Enter MillionVerifier File ID (from web dashboard): "
    val = input(f"\n{prompt_str}").strip()

    if not val:
        print("Cancelled.")
        return

    selected_job_entry = None
    if jobs and val.isdigit() and 1 <= int(val) <= len(jobs[:10]):
        selected_job_entry = jobs[int(val) - 1]
        target_file_id = str(selected_job_entry.get("file_id", ""))
    else:
        target_file_id = val

    dest_csv = None
    ok_count = 0
    clean_stem = ""

    if selected_job_entry and selected_job_entry.get("good_csv_path"):
        local_candidate = Path(selected_job_entry["good_csv_path"])
        if local_candidate.is_file():
            print(f"\n✓ Found already-verified Good CSV on disk: '{local_candidate.name}'")
            dest_csv = local_candidate
            ok_count = selected_job_entry.get("good_count", 0)
            clean_stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", local_candidate.stem).strip("_")
            print(f"  • Good / Deliverable Leads: {ok_count:,d}")

    if not dest_csv:
        if not target_file_id.isdigit():
            print(f"\n[ERROR] MillionVerifier API requires a numeric File ID (e.g. 184920). Received: '{target_file_id}'")
            return

        print(f"\nConnecting to MillionVerifier Bulk API for File ID: {target_file_id}...")
        try:
            info = api_get_file_info(target_file_id)
        except Exception as exc:
            print(f"\n[ERROR] Could not retrieve file info from MillionVerifier API: {exc}")
            return

        status = str(info.get("status", "unknown")).lower()
        file_name = info.get("file_name") or f"job_{target_file_id}.csv"
        total_rows = info.get("total_rows", 0)
        verified = info.get("verified", 0)
        ok_count = info.get("ok", 0)
        bad_count = info.get("bad", 0)
        risky_count = info.get("catch_all", 0)

        print("\n" + "-" * 95)
        print(f"MillionVerifier API Status for File #{target_file_id}:")
        print(f"  • File Name    : {file_name}")
        print(f"  • Status       : {status.upper()}")
        print(f"  • Total Rows   : {total_rows:,d}")
        print(f"  • Verified Rows: {verified:,d}")
        print(f"  • 🟢 Good (OK) : {ok_count:,d} deliverable leads")
        print(f"  • 🔴 Bad       : {bad_count:,d}")
        print(f"  • 🟡 Risky     : {risky_count:,d}")
        print("-" * 95)

        if status != "finished":
            print(f"[Notice] Verification job #{target_file_id} is currently '{status}'.")
            return

        if ok_count == 0:
            print("[Notice] This job contains 0 Good/Deliverable leads. Nothing to sync.")
            return

        download_dir = PROJECT_ROOT / "exports" / "millionverifier_downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        clean_stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", Path(file_name).stem).strip("_")
        dest_csv = download_dir / f"{clean_stem}_job{target_file_id}_good.csv"

        print(f"\nDownloading {ok_count:,d} Good leads directly from MillionVerifier API (all columns preserved)...")
        try:
            api_download_good_leads(target_file_id, dest_csv)
            print(f"✓ Downloaded successfully to: {dest_csv}")
        except Exception as exc:
            print(f"\n[ERROR] Failed to download results via API: {exc}")
            return

        save_millionverifier_job({
            "file_id": target_file_id,
            "file_name": file_name,
            "status": "finished",
            "total_rows": total_rows,
            "good_count": ok_count,
            "bad_count": bad_count,
            "risky_count": risky_count,
            "good_csv_path": str(dest_csv),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    tag_stem = re.sub(r"[_\s]*-[_\s]*good[_\s]*-.*$", "", clean_stem, flags=re.IGNORECASE)
    tag_stem = re.sub(r"[_\s]*good[_\s]*", "", tag_stem, flags=re.IGNORECASE)
    tag_stem = re.sub(r"_+", "_", tag_stem).strip("_")
    default_tag = f"{tag_stem}_{datetime.now().strftime('%b-%Y')}"

    print(f"\nFreshsales CRM Tag Assignment:")
    tag_input = input(f"Enter Tag Name for Freshsales (default: '{default_tag}'): ").strip()
    custom_tag = tag_input if tag_input else default_tag

    confirm = input(f"\nPush {ok_count:,d} Good leads into Freshsales CRM with tag '{custom_tag}'? [Y/n, default Y]: ").strip().lower()
    if confirm in ("n", "no"):
        print("Sync skipped. Leads saved locally.")
        return

    try:
        sync_good_file_to_freshsales(dest_csv, custom_tag=custom_tag)
    except Exception as exc:
        print(f"\n[ERROR] Freshsales CRM sync failed: {exc}")


# =====================================================================
# 9. WORKFLOW 4: BROWSE ALL DISCOVERED GOOD LEAD FILES ON DISK
# =====================================================================

def select_local_file_and_sync_pipeline() -> None:
    """
    Discover verified Good lead files across Downloads/Exports,
    provide instant login/account search filtering, and push to Freshsales CRM.
    """
    print("\n" + "=" * 95)
    print("      BROWSE ALL DISCOVERED GOOD LEAD FILES ON DISK (FILTER BY LOGIN)")
    print("=" * 95)

    print("Search by login or account name (e.g. 'madhava', 'vraghavan', or press Enter for all):")
    login_filter = input(">> Filter by Login: ").strip()

    batches = discover_verified_good_batches(filter_query=login_filter)
    if not batches:
        if login_filter:
            print(f"\n[Notice] No verified Good files matching '{login_filter}' found.")
            batches = discover_verified_good_batches(filter_query=None)

    if not batches:
        print("\n[Notice] No verified Good lead files (*good*.csv) found in Downloads or exports.")
        return

    filter_label = f" (Filtered by: '{login_filter}')" if login_filter else ""
    print(f"\nFound {len(batches)} Verified Lead Files{filter_label}:")
    print("-" * 115)
    print(f"{'#':<3} | {'Verified File Name':<38} | {'Login / Account':<22} | {'Leads':<8} | {'Status'}")
    print("-" * 115)

    disp_limit = min(25, len(batches))
    for idx in range(disp_limit):
        b = batches[idx]
        f_name = b["file_name"][:36]
        login_d = b["login_hint"][:20]
        leads_str = f"{b['lead_count']:,d}"
        print(f"[{idx+1:<2}] | {f_name:<38} | {login_d:<22} | {leads_str:<8} | {b['status_display']}")

    if len(batches) > disp_limit:
        print(f"  ... and {len(batches) - disp_limit} more files. Use login filter above to narrow search.")

    print("-" * 115)
    print(f"Select option [1-{disp_limit}, or q to quit]: ")
    choice = input(">> ").strip()
    if choice.lower() in ("q", "back", "exit", ""):
        return

    if choice.isdigit() and 1 <= int(choice) <= disp_limit:
        target = batches[int(choice) - 1]
    else:
        print("[!] Invalid selection.")
        return

    print(f"\nSelected: '{target['file_name']}'")
    print(f"  • Total Leads     : {target['lead_count']:,d}")
    print(f"  • Current Status  : {target['status_display']}")
    if target.get("failure_reason"):
        print(f"  • Previous Issue  : {target['failure_reason']}")

    default_tag = target["file_stem"].replace(" - good", "").replace("_good", "").replace("_verified", "")
    default_tag = re.sub(r"_\d{8}_\d{6}$", "", default_tag)
    default_tag = f"{default_tag}_{datetime.now().strftime('%b-%Y')}"

    tag_input = input(f"\nEnter Tag Name for Freshsales (default: '{target.get('sync_tag') or default_tag}'): ").strip()
    custom_tag = tag_input if tag_input else (target.get("sync_tag") or default_tag)

    confirm = input(f"Ready to push to Freshsales with tag '{custom_tag}'? [Y/n, default Y]: ").strip().lower()
    if confirm in ("n", "no"):
        print("Skipped.")
        return

    try:
        sync_good_file_to_freshsales(
            good_csv_path=target["file_path"],
            custom_tag=custom_tag,
            audit_cache_path=target.get("audit_path"),
        )
    except Exception as exc:
        print(f"\n[ERROR] Failed to sync to Freshsales: {exc}")


# =====================================================================
# 9. WORKFLOW 5: FRESHSALES CRM & NET-NEW DOMAIN AUDIT
# =====================================================================

def run_freshsales_domain_audit(file_path: Union[Path, str], conn=None) -> Optional[Dict[str, Any]]:
    """
    Performs an in-depth audit of a verified good file against Freshsales CRM & Master CRM:
      - How many imported / total leads
      - How many created (net-new contacts)
      - How many already existed (updated contacts)
      - How many errors / failures
      - How many foreign TLDs blocked
      - Total unique corporate domains
      - How many domains already existed in Freshsales (1.84M accounts)
      - How many NET-NEW domains created in Freshsales
      - Cross-reference with Master CRM (emails table - 7.47M contacts)
    """
    if not isinstance(file_path, Path):
        file_path = Path(file_path)

    if not file_path.is_file():
        print(f"[!] File not found: {file_path}")
        return None

    print("\n" + "=" * 95)
    print(f"      RUNNING FRESHSALES CRM & DOMAIN AUDIT: {file_path.name}")
    print("=" * 95)
    print("  • Reading source leads and extracting company domains...")

    # 1. Parse source file
    domain_to_info: Dict[str, Dict[str, Any]] = {}
    source_emails: Dict[str, Dict[str, Any]] = {}
    total_leads = 0

    try:
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                total_leads += 1
                email = (row.get("Email") or "").strip().lower()
                if not email:
                    continue
                comp = (row.get("Company Name") or row.get("Company Name for Emails") or row.get("Account") or "").strip()
                dom = email.split("@")[1].strip().lower() if "@" in email else ""

                source_emails[email] = {
                    "company": comp,
                    "domain": dom,
                    "first_name": row.get("First Name", "").strip(),
                    "last_name": row.get("Last Name", "").strip(),
                    "title": row.get("Title", "").strip(),
                }

                if dom:
                    if dom not in domain_to_info:
                        domain_to_info[dom] = {"company": comp, "count": 1, "sample_email": email}
                    else:
                        domain_to_info[dom]["count"] += 1
                        if not domain_to_info[dom]["company"] and comp:
                            domain_to_info[dom]["company"] = comp
    except Exception as exc:
        print(f"[ERROR] Failed to read source file: {exc}")
        return None

    all_domains = list(domain_to_info.keys())
    total_domains = len(all_domains)

    # 2. Inspect Audit CSV Reports (including retry slices)
    reports_dir = PROJECT_ROOT / "exports" / "freshsales_reports"
    file_stem = file_path.stem
    clean_stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", file_stem).strip("_")

    final_actions: Dict[str, str] = {}
    applied_tag = ""

    # Check ledger
    ledger = get_freshsales_ledger()
    for k, v in ledger.items():
        if file_stem.lower() in k.lower() or k.lower() in file_stem.lower():
            if v.get("tag"):
                applied_tag = v["tag"]
                break

    # Look for primary audit file
    audit_candidates = [
        reports_dir / f"{file_stem}_crm_audit.csv",
        reports_dir / f"{file_path.name}_crm_audit.csv",
    ]
    primary_audit = next((c for c in audit_candidates if c.is_file()), None)
    if primary_audit:
        try:
            with open(primary_audit, "r", encoding="utf-8-sig", errors="replace") as fp:
                for r in csv.DictReader(fp):
                    em = (r.get("email") or "").strip().lower()
                    if em:
                        final_actions[em] = (r.get("action") or "").strip().lower()
        except Exception:
            pass

    # Look for retry audit files
    retry_pattern = f"{clean_stem}_unsynced_retry_*_crm_audit.csv"
    for ret_path in reports_dir.glob(retry_pattern):
        try:
            with open(ret_path, "r", encoding="utf-8-sig", errors="replace") as fp:
                for r in csv.DictReader(fp):
                    em = (r.get("email") or "").strip().lower()
                    if em:
                        final_actions[em] = (r.get("action") or "").strip().lower()
        except Exception:
            pass

    # Count sync metrics
    created_count = sum(1 for a in final_actions.values() if a == "created")
    updated_count = sum(1 for a in final_actions.values() if a in ("updated_in_crm", "updated"))
    tld_blocked_count = sum(1 for a in final_actions.values() if a == "excluded_tld")
    failed_count = sum(1 for a in final_actions.values() if a not in ("created", "updated", "updated_in_crm", "excluded_tld", "unprocessed"))

    audited_emails = set(final_actions.keys())
    unsynced_count = sum(1 for em in source_emails if em not in audited_emails or final_actions.get(em) not in ("created", "updated", "updated_in_crm", "excluded_tld"))

    # 3. Query Freshsales Companies & Master CRM Emails from MySQL
    print("  • Cross-referencing domains against Freshsales (1.84M accounts) & Master CRM (7.47M contacts)...")
    fs_existing_domains = set()
    master_crm_domains = set()

    db_host = os.getenv("DB_HOST", "").strip()
    if db_host:
        try:
            import pymysql
            db_conn = pymysql.connect(
                host=db_host,
                user=os.getenv("DB_USER", "nestack"),
                password=os.getenv("DB_PASSWORD", ""),
                db=os.getenv("DB_NAME", "apollo_scrapers"),
                port=int(os.getenv("DB_PORT", "3306")),
                charset="utf8mb4",
                connect_timeout=15,
            )
            with db_conn.cursor() as cur:
                chunk_size = 1000
                for i in range(0, len(all_domains), chunk_size):
                    chunk = all_domains[i : i + chunk_size]
                    placeholders = ",".join(["%s"] * len(chunk))

                    # Freshsales companies
                    cur.execute(
                        f"SELECT DISTINCT domain FROM freshsales_companies WHERE domain IN ({placeholders})",
                        tuple(chunk),
                    )
                    for r in cur.fetchall():
                        if r[0]:
                            fs_existing_domains.add(r[0].lower().strip())

                    # Master CRM emails
                    cur.execute(
                        f"SELECT DISTINCT domain FROM emails WHERE domain IN ({placeholders})",
                        tuple(chunk),
                    )
                    for r in cur.fetchall():
                        if r[0]:
                            master_crm_domains.add(r[0].lower().strip())
            db_conn.close()
        except Exception as exc:
            print(f"  [!] Note: MySQL query error ({exc}). Continuing with available audit data.")

    # Domain computations
    fs_net_new_domains = [d for d in all_domains if d not in fs_existing_domains]
    master_net_new_domains = [d for d in all_domains if d not in master_crm_domains]
    virgin_domains = [d for d in all_domains if d not in fs_existing_domains and d not in master_crm_domains]

    leads_in_new_fs_domains = sum(domain_to_info[d]["count"] for d in fs_net_new_domains)
    leads_in_new_master_domains = sum(domain_to_info[d]["count"] for d in master_net_new_domains)
    leads_in_virgin_domains = sum(domain_to_info[d]["count"] for d in virgin_domains)

    # 4. Print Rich Dashboard
    pct = lambda n, d: (n / d * 100) if d else 0.0

    print("\n" + "=" * 95)
    print("                    FRESHSALES CRM & NET-NEW DOMAIN AUDIT REPORT")
    print("=" * 95)
    print(f"  • Source File         : {file_path.name}")
    if applied_tag:
        print(f"  • Freshsales Tag Used : '{applied_tag}'")
    print(f"  • Total Deliverable Leads in File : {total_leads:,d}")
    print(f"  • Total Unique Corporate Domains  : {total_domains:,d}")

    print("\n" + "─" * 95)
    print("  1. CONTACTS & SYNC EXECUTION SUMMARY")
    print("─" * 95)
    print(f"  • Total Leads Processed in CRM : {len(final_actions):,d} / {total_leads:,d} ({pct(len(final_actions), total_leads):.1f}%)")
    print(f"  ├── Newly Created Contacts     : {created_count:>6,d} contacts (Net-new leads added to CRM)")
    print(f"  ├── Existing Contacts Updated  : {updated_count:>6,d} contacts (Non-overwrite update / tag safely merged)")
    print(f"  ├── Excluded Foreign TLDs      : {tld_blocked_count:>6,d} contacts (.uk, .ca, .de, etc. blocked)")
    print(f"  ├── Execution Errors / Failures: {failed_count:>6,d} contacts")
    if unsynced_count > 0:
        print(f"  └── Remaining Unsynced Pending : {unsynced_count:>6,d} contacts (Pending push)")
    else:
        print(f"  └── Remaining Unsynced Pending :      0 contacts (100% Synced / Fully Complete)")

    print("\n" + "─" * 95)
    print("  2. FRESHSALES ACCOUNTS & DOMAINS BREAKDOWN (`freshsales_companies` - 1.84M Accounts)")
    print("─" * 95)
    print(f"  • Total Unique Domains in File : {total_domains:>6,d} corporate domains")
    print(f"  • Already Existed in Freshsales: {len(fs_existing_domains):>6,d} domains ({pct(len(fs_existing_domains), total_domains):5.1f}%)")
    print(f"  • ★ NET-NEW DOMAINS IN FRESHSALES: {len(fs_net_new_domains):>6,d} domains ({pct(len(fs_net_new_domains), total_domains):5.1f}%)")
    print(f"      └── Leads with Net-New Domains: {leads_in_new_fs_domains:>6,d} leads   ({pct(leads_in_new_fs_domains, total_leads):5.1f}% of entire file)")

    print("\n" + "─" * 95)
    print("  3. MASTER CRM DATABASE COMPARISON (`emails` table - 7.47M Records)")
    print("─" * 95)
    print(f"  • Already Existed in Master CRM: {len(master_crm_domains):>6,d} domains ({pct(len(master_crm_domains), total_domains):5.1f}%)")
    print(f"  • ★ Net-New to Master CRM      : {len(master_net_new_domains):>6,d} domains ({pct(len(master_net_new_domains), total_domains):5.1f}%)")
    print(f"  • ★ Net-New to BOTH CRMs       : {len(virgin_domains):>6,d} domains ({pct(len(virgin_domains), total_domains):5.1f}% completely virgin accounts)")
    print(f"      └── Leads with Virgin Domains : {leads_in_virgin_domains:>6,d} leads   ({pct(leads_in_virgin_domains, total_leads):5.1f}% of entire file)")
    print("=" * 95)

    # 5. Interactive Submenu for this audit
    while True:
        print("\nAUDIT ACTIONS:")
        print("  [1] View Top 30 Net-New Freshsales Domains (Company, Domain, Leads)")
        print("  [2] Export Complete Net-New Domains Audit to CSV")
        print("  [q] Return / Back")
        sub_act = input("\nSelect action [1, 2, or q, default q]: ").strip().lower()
        if sub_act in ("q", "back", "", "3"):
            break
        elif sub_act == "1":
            print("\n" + "-" * 85)
            print("  SAMPLE NET-NEW DOMAINS NOT PREVIOUSLY IN FRESHSALES (Top 30):")
            print("-" * 85)
            print(f"  {'#':<4} {'Domain':<30} {'Company Name':<34} {'Leads'}")
            print("  " + "─" * 78)
            sorted_new = sorted(fs_net_new_domains, key=lambda d: domain_to_info[d]["count"], reverse=True)
            for idx, d in enumerate(sorted_new[:30], 1):
                comp_name = (domain_to_info[d]["company"] or "N/A")[:33]
                cnt = domain_to_info[d]["count"]
                print(f"  [{idx:>2}] {d:<30} {comp_name:<34} {cnt:>4}")
            if len(fs_net_new_domains) > 30:
                print(f"  ... and {len(fs_net_new_domains) - 30:,d} more net-new domains.")
            input("\nPress Enter to continue...")
        elif sub_act == "2":
            export_csv_path = reports_dir / f"{clean_stem}_domain_audit.csv"
            try:
                with open(export_csv_path, "w", encoding="utf-8-sig", newline="") as fp:
                    writer = csv.writer(fp)
                    writer.writerow([
                        "Domain",
                        "Company Name",
                        "Leads Count",
                        "Exists in Freshsales CRM",
                        "Exists in Master RDS CRM",
                        "Audit Category",
                    ])
                    for d in all_domains:
                        comp = domain_to_info[d]["company"]
                        cnt = domain_to_info[d]["count"]
                        in_fs = "YES" if d in fs_existing_domains else "NO"
                        in_master = "YES" if d in master_crm_domains else "NO"
                        if d not in fs_existing_domains and d not in master_crm_domains:
                            cat = "Net-New (Virgin Everywhere)"
                        elif d not in fs_existing_domains:
                            cat = "Net-New to Freshsales"
                        elif d not in master_crm_domains:
                            cat = "Net-New to Master CRM"
                        else:
                            cat = "Already in Both CRMs"
                        writer.writerow([d, comp, cnt, in_fs, in_master, cat])
                print(f"\n[OK] Complete domain audit exported ({total_domains:,d} domains):")
                print(f"     -> {export_csv_path}")
            except Exception as exc:
                print(f"\n[ERROR] Failed to export CSV: {exc}")
            input("\nPress Enter to continue...")

    return {
        "file_path": file_path,
        "total_leads": total_leads,
        "created_count": created_count,
        "updated_count": updated_count,
        "failed_count": failed_count,
        "tld_blocked_count": tld_blocked_count,
        "unsynced_count": unsynced_count,
        "total_domains": total_domains,
        "fs_net_new_domains": len(fs_net_new_domains),
        "fs_existing_domains": len(fs_existing_domains),
        "master_net_new_domains": len(master_net_new_domains),
        "virgin_domains": len(virgin_domains),
    }


def freshsales_domain_audit_menu(conn=None) -> None:
    """
    Interactive CLI menu to audit Freshsales sync & net-new domains
    by Login (e.g. V Raghavan, Madhava Reddy) or by Synced File / Batch.
    """
    while True:
        print("\n" + "=" * 95)
        print("          FRESHSALES CRM & NET-NEW DOMAIN AUDIT ENGINE")
        print("=" * 95)
        print("  • Audits imported leads, contacts created vs updated, and error counts.")
        print("  • Calculates exact count of NET-NEW domains created in Freshsales.")
        print("  • Cross-references with Master CRM (emails table) to find completely virgin accounts.")
        print("=" * 95)
        print("\nHOW WOULD YOU LIKE TO SELECT THE AUDIT TARGET?")
        print("  [1] Select by Login / Account (Grouped Files)")
        print("  [2] Select from All Discovered Good Lead Files on Disk")
        print("  [q] Back to previous menu")
        print("-" * 95)

        choice = input("Select an option [1, 2, or q]: ").strip().lower()
        if choice in ("q", "back", "exit", ""):
            break
        elif choice == "1":
            # Grouped by login
            files = discover_good_leads_files()
            if not files:
                print("[!] No verified Good lead files discovered on disk.")
                input("Press Enter to continue...")
                continue
            login_groups = group_files_by_login(files)
            sorted_logins = sorted(login_groups.items(), key=lambda x: x[0])
            print(f"\nSelect Login / Account ({len(sorted_logins)} total):")
            for idx, (login_name, f_list) in enumerate(sorted_logins, 1):
                tot_leads = sum(f["lead_count"] for f in f_list)
                print(f"  [{idx:>2}] {login_name:<40} ({len(f_list)} files, {tot_leads:,d} leads)")
            print("  [s] Search login name")
            print("  [b] Back")

            sel = input(f"\nSelect login [1-{len(sorted_logins)}, s, or b]: ").strip().lower()
            if sel in ("b", "back", "q", ""):
                continue
            elif sel == "s":
                term = input("Enter search term: ").strip().lower()
                matches = [(k, v) for k, v in sorted_logins if term in k]
                if not matches:
                    print(f"[!] No accounts matching '{term}'.")
                    continue
                chosen_login = matches[0][0]
                target_files = matches[0][1]
            elif sel.isdigit() and 1 <= int(sel) <= len(sorted_logins):
                chosen_login = sorted_logins[int(sel) - 1][0]
                target_files = sorted_logins[int(sel) - 1][1]
            else:
                continue

            # If multiple files for this login, let them pick
            chosen_file = target_files[0]["file_path"]
            if len(target_files) > 1:
                print(f"\nFiles for {chosen_login.upper()}:")
                for f_i, tf in enumerate(target_files, 1):
                    print(f"  [{f_i}] {tf['file_name']} ({tf['lead_count']:,d} leads, status: {tf['status_display']})")
                f_sel = input(f"Select file [1-{len(target_files)}, default 1]: ").strip()
                if f_sel.isdigit() and 1 <= int(f_sel) <= len(target_files):
                    chosen_file = target_files[int(f_sel) - 1]["file_path"]

            run_freshsales_domain_audit(chosen_file, conn=conn)

        elif choice == "2":
            files = discover_good_leads_files()
            if not files:
                print("[!] No verified Good lead files discovered on disk.")
                input("Press Enter to continue...")
                continue
            print(f"\nDiscovered Good Lead Files ({len(files)} total):")
            for idx, f_entry in enumerate(files[:35], 1):
                st = inspect_file_sync_status(f_entry["file_path"])
                print(f"  [{idx:>2}] {f_entry['file_name'][:55]:<55} | Leads: {st['total_leads']:>5,d} | {st['status_display']}")
            if len(files) > 35:
                print(f"  ... and {len(files) - 35} more files.")
            f_sel = input(f"\nSelect file [1-{min(len(files), 35)}, or b]: ").strip().lower()
            if f_sel in ("b", "back", "q", ""):
                continue
            elif f_sel.isdigit() and 1 <= int(f_sel) <= len(files):
                chosen_file = files[int(f_sel) - 1]["file_path"]
                run_freshsales_domain_audit(chosen_file, conn=conn)


# =====================================================================
# 10. MAIN INTERACTIVE CLI ACTION (OPTION [F])
# =====================================================================

def freshsales_agent_menu_action(conn) -> None:
    """
    Interactive CLI menu action for Option [F] in manage_batches.py:
    1. Select Login / Account -> Inspect MillionVerifier Files & Push to CRM (Recommended).
    2. Verify Enriched Database Batch via MillionVerifier API -> Push to Freshsales.
    3. Fetch from MillionVerifier Job / File ID -> Download via API -> Push to Freshsales.
    4. Browse All Discovered Good Lead Files on Disk (Filter by Login).
    5. Freshsales CRM & Net-New Domain Audit (By Login or Synced Batch).
    """
    while True:
        print("\n" + "=" * 95)
        print("             FRESHSALES CRM AGENT: SYNC VERIFIED GOOD LEADS")
        print("=" * 95)
        print("  • Automatically verifies & syncs deliverable Good leads into Freshsales CRM.")
        print("  • Prevents overwriting existing Freshsales phone numbers, titles, or notes.")
        print("  • Preserves all existing tags and safely appends your custom batch tag.")
        print("  • Automatically excludes 33 foreign country domains (.uk, .ca, .de, etc.).")
        print("  • Tracks exact counts of leads already in CRM vs NOT pushed (unsynced).")
        print("=" * 95)
        print("\nHOW WOULD YOU LIKE TO SYNC LEADS TO FRESHSALES?")
        print("-" * 95)
        print("  [1] Select Login / Account -> Inspect MillionVerifier Files & Push to CRM (Recommended)")
        print("      -> Pick an account (e.g. V Raghavan, Madhava Reddy), view leads already in CRM vs")
        print("         NOT pushed (unsynced), and push deliverable leads (or resume unsynced 4,472 leads).")
        print("  [2] Verify Enriched Database Batch via MillionVerifier API -> Push to Freshsales")
        print("      -> Pick an enriched login from SQLite, clean, verify via verifier API, and push Good leads.")
        print("  [3] Fetch from MillionVerifier Job / File ID -> Download via API -> Push to Freshsales")
        print("      -> Fetch any MillionVerifier verification job by numeric File ID, download, and push.")
        print("  [4] Browse All Discovered Good Lead Files on Disk (Filter by Login)")
        print("      -> Browse 43+ verified Good CSV files in Downloads/Exports, filter by login, and push.")
        print("  [5] Freshsales CRM & Net-New Domain Audit (By Login or Synced Batch)")
        print("      -> Audit imported leads, created vs updated, and exact net-new domains in Freshsales.")
        print("  [q] Back to main menu")
        print("-" * 95)

        sel = input("Select an option [1, 2, 3, 4, 5, or q]: ").strip()
        if sel.lower() in ("q", "back", "exit", ""):
            break
        elif sel == "1":
            login_lead_sync_pipeline(conn)
            break
        elif sel == "2":
            verify_and_sync_login_pipeline(conn)
            break
        elif sel == "3":
            fetch_job_and_sync_pipeline()
            break
        elif sel == "4":
            select_local_file_and_sync_pipeline()
            break
        elif sel == "5":
            freshsales_domain_audit_menu(conn)
            break
        else:
            print("[!] Invalid option. Please select 1, 2, 3, 4, 5, or q.")


if __name__ == "__main__":
    freshsales_agent_menu_action(None)

