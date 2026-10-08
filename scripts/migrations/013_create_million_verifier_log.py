"""
Migration 013: Create million_verifier_log table and backfill historical verification runs.
"""

import os
import sys
import re
import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api import (
    get_connection,
    ensure_million_verifier_log_table,
    record_million_verifier_log,
)


def extract_cycle_from_string(text: str) -> str:
    """Extract standard cycle pattern like 'sep 03 - oct 03' from filename or batch text."""
    if not text:
        return ""
    m = re.search(r"([a-z]{3})[\s_]*(\d{1,2})[\s_]*(?:-|_|to|\s)+[\s_]*([a-z]{3})[\s_]*(\d{1,2})", text.lower())
    if m:
        return f"{m.group(1)} {int(m.group(2)):02d} - {m.group(3)} {int(m.group(4)):02d}"
    return ""


def migrate() -> None:
    print("\n" + "=" * 80)
    print(" Running Migration 013: Create `million_verifier_log` Table & Backfill")
    print("=" * 80)

    # 1. Ensure table exists in MySQL
    with get_connection() as conn:
        ensure_million_verifier_log_table(conn)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM `million_verifier_log`")
            columns = [row[0] for row in cur.fetchall()]
            cur.execute("TRUNCATE TABLE `million_verifier_log`")
        print(f"[OK] Table `million_verifier_log` created/truncated with {len(columns)} columns:")
        print("     " + ", ".join(columns))

    # 2. Load historical jobs and CRM syncs
    jobs_file = PROJECT_ROOT / "config" / "millionverifier_jobs.json"
    fs_file = PROJECT_ROOT / "config" / "freshsales_synced_batches.json"

    jobs = []
    if jobs_file.exists():
        try:
            with open(jobs_file, "r", encoding="utf-8") as f:
                jobs = json.load(f)
        except Exception as e:
            print(f"[WARNING] Could not read {jobs_file}: {e}")

    fs_batches = {}
    if fs_file.exists():
        try:
            with open(fs_file, "r", encoding="utf-8") as f:
                fs_batches = json.load(f)
        except Exception as e:
            print(f"[WARNING] Could not read {fs_file}: {e}")

    print(f"\n[INFO] Found {len(jobs)} historical jobs in {jobs_file.name}")
    print(f"[INFO] Found {len(fs_batches)} synced batches in {fs_file.name}")

    # 3. Backfill into database
    backfilled_count = 0
    with get_connection() as conn:
        for j in jobs:
            file_id = str(j.get("file_id") or j.get("job_id") or "").strip()
            fname = str(j.get("file_name") or j.get("filename") or "").strip()
            login = str(j.get("login") or "").strip()
            account_name = str(j.get("account_name") or "").strip()
            batch = str(j.get("batch") or "").strip()
            total_rows = int(j.get("total_rows") or 0)
            good_count = int(j.get("good_count") or 0)
            bad_count = int(j.get("bad_count") or 0)
            risky_count = int(j.get("risky_count") or 0)
            status = str(j.get("status") or "completed").strip()
            created_str = j.get("created_at")
            good_csv_path = str(j.get("good_csv_path") or "").strip()

            created_dt = None
            if created_str:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        created_dt = datetime.strptime(created_str, fmt)
                        break
                    except ValueError:
                        pass

            # Cycle extraction
            cycle = extract_cycle_from_string(fname) or extract_cycle_from_string(batch)
            if not cycle:
                if "aug_20" in fname.lower() or "aug_20" in batch.lower() or "vraghvan_nestacktech_com_sep" in batch.lower():
                    cycle = "aug 20 - sep 20"
                elif "aug_21" in fname.lower() or "madhava_reddy_sep" in batch.lower():
                    cycle = "aug 21 - sep 21"
                elif "aug_13" in fname.lower():
                    cycle = "aug 13 - sep 13"
                elif "sep" in fname.lower() or "sep" in batch.lower():
                    cycle = "aug - sep"

            # Check match against freshsales_synced_batches
            crm_created = 0
            crm_updated = 0
            crm_tag = ""
            file_stem = fname.replace(".csv", "").lower()

            for k, fs_info in fs_batches.items():
                k_lower = k.lower()
                fs_path = str(fs_info.get("file_path") or "")
                matched = False
                if file_stem and (file_stem in k_lower or k_lower in file_stem):
                    matched = True
                elif good_csv_path and fs_path and Path(fs_path).name == Path(good_csv_path).name:
                    matched = True

                if matched:
                    crm_created = int(fs_info.get("created") or 0)
                    crm_updated = int(fs_info.get("updated") or 0)
                    crm_tag = str(fs_info.get("tag") or "")
                    if not good_csv_path and fs_path:
                        good_csv_path = fs_path
                    # Also extract cycle from CRM tag if not found yet
                    if not cycle:
                        cycle = extract_cycle_from_string(crm_tag)
                    break

            # Discarded leads is risky (catch_all + unknown)
            discarded_leads = risky_count

            record_million_verifier_log(
                conn=conn,
                log_name=login,
                login_name=login,
                account_name=account_name,
                batch_name=batch,
                cycle=cycle,
                leads_entered=total_rows,
                good_leads=good_count,
                discarded_leads=discarded_leads,
                bad_leads=bad_count,
                catch_all_leads=0,
                risky_leads=risky_count,
                file_id=file_id,
                file_name=fname,
                status=status,
                crm_created=crm_created,
                crm_updated=crm_updated,
                crm_tag=crm_tag,
                file_path=good_csv_path,
                verified_at=created_dt,
                created_at=created_dt,
            )
            backfilled_count += 1

    print(f"\n[SUCCESS] Successfully backfilled {backfilled_count} verification records into `million_verifier_log`!")


if __name__ == "__main__":
    migrate()
