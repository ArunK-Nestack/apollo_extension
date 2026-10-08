"""
Database Migration Script: Add `cycle` column to `apollo_saved_leads` and `enrich_saved_leads`
=============================================================================================
Safely adds `cycle VARCHAR(64) DEFAULT ''` with indexes.
Backfills existing rows based on `created_at` and live Apollo account billing cycle windows.
"""

import sys
import os
import json
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

sys.path.insert(0, ".")
from backend.api import get_connection

REPORT_PATH = os.path.join("config", "apollo_live_account_report.json")


def get_account_cycles():
    """Load the 19 Apollo accounts and build a map of renewal dates."""
    if not os.path.exists(REPORT_PATH):
        return {}
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    acc_map = {}
    for acc in data.get("accounts", []):
        email = acc.get("email", "").strip().lower()
        exp_str = acc.get("expiry_utc")
        if email and exp_str:
            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            acc_map[email] = {
                "name": acc.get("name", ""),
                "renewal_day": exp_dt.day,
                "expiry_dt": exp_dt,
            }
    return acc_map


def compute_lead_cycle(lead_dt: datetime, renewal_day: int) -> str:
    """Compute the monthly cycle window (e.g. 'sep 20 - oct 20') for a lead's created_at."""
    if lead_dt.tzinfo is None:
        lead_dt = lead_dt.replace(tzinfo=timezone.utc)

    # If lead day >= renewal day, cycle is [current_month renewal_day, next_month renewal_day]
    # If lead day < renewal day, cycle is [prev_month renewal_day, current_month renewal_day]
    try:
        if lead_dt.day >= renewal_day:
            start_dt = lead_dt.replace(day=renewal_day, hour=0, minute=0, second=0, microsecond=0)
            end_dt = start_dt + relativedelta(months=1)
        else:
            end_dt = lead_dt.replace(day=renewal_day, hour=0, minute=0, second=0, microsecond=0)
            start_dt = end_dt - relativedelta(months=1)
    except ValueError:
        # Handle end of month day adjustments (e.g. Feb 30 -> Feb 28)
        start_dt = lead_dt.replace(day=1) - relativedelta(months=1)
        end_dt = lead_dt.replace(day=1)

    return f"{start_dt.strftime('%b %d').lower()} - {end_dt.strftime('%b %d').lower()}"


def migrate():
    print("=" * 75)
    print(">>> MIGRATING `apollo_saved_leads` & `enrich_saved_leads` FOR CYCLES")
    print("=" * 75)

    tables = ["apollo_saved_leads", "enrich_saved_leads"]

    with get_connection() as conn:
        with conn.cursor() as cur:
            for tbl in tables:
                print(f"\n[1] Checking schema for `{tbl}`...")
                cur.execute(f"DESCRIBE `{tbl}`;")
                existing_cols = set(r[0].lower() for r in cur.fetchall())

                if "cycle" not in existing_cols:
                    print(f"  + Adding `cycle` column to `{tbl}`...")
                    cur.execute(f"ALTER TABLE `{tbl}` ADD COLUMN `cycle` VARCHAR(64) NOT NULL DEFAULT '' AFTER `account_used`;")
                    print(f"  ✓ Added `cycle` to `{tbl}`")
                else:
                    print(f"  • `cycle` already exists in `{tbl}`")

                # Check indexes
                cur.execute(f"SHOW INDEX FROM `{tbl}`;")
                indexes = set(r[2] for r in cur.fetchall())
                if "idx_cycle" not in indexes:
                    cur.execute(f"ALTER TABLE `{tbl}` ADD INDEX `idx_cycle` (`cycle`);")
                    print(f"  ✓ Added index `idx_cycle` to `{tbl}`")
                if "idx_account_cycle" not in indexes:
                    cur.execute(f"ALTER TABLE `{tbl}` ADD INDEX `idx_account_cycle` (`account_used`, `cycle`);")
                    print(f"  ✓ Added index `idx_account_cycle` to `{tbl}`")

            conn.commit()

        # [2] Backfill existing rows with their appropriate cycle
        print("\n[2] Backfilling historical leads with cycle tags...")
        acc_map = get_account_cycles()
        print(f"  Loaded renewal profiles for {len(acc_map)} accounts.")

        with conn.cursor() as cur:
            # Check unpopulated rows in apollo_saved_leads
            cur.execute("SELECT COUNT(*) FROM `apollo_saved_leads` WHERE `cycle` = '' OR `cycle` IS NULL;")
            unassigned_count = cur.fetchone()[0]
            print(f"  Found {unassigned_count:,d} leads in `apollo_saved_leads` needing cycle assignment.")

            if unassigned_count > 0:
                # Group by account_used
                cur.execute("SELECT DISTINCT `account_used` FROM `apollo_saved_leads` WHERE `cycle` = '' OR `cycle` IS NULL;")
                distinct_accs = [r[0] for r in cur.fetchall()]

                for raw_acc in distinct_accs:
                    acc_lower = (raw_acc or "").strip().lower()
                    info = acc_map.get(acc_lower)

                    # Try matching by name if account_used is a name instead of email
                    if not info:
                        for em, data in acc_map.items():
                            if data["name"].lower() == acc_lower or acc_lower in em:
                                info = data
                                break

                    renewal_day = info["renewal_day"] if info else 20  # Default renewal day = 20th if unknown

                    # Fetch rows for this account
                    cur.execute(
                        "SELECT `id`, `created_at` FROM `apollo_saved_leads` "
                        "WHERE (`account_used` = %s OR (%s = '' AND `account_used` = '')) "
                        "AND (`cycle` = '' OR `cycle` IS NULL);",
                        (raw_acc, raw_acc),
                    )
                    rows = cur.fetchall()

                    # Batch updates by cycle string
                    updates_by_cycle = {}
                    for row_id, created_at in rows:
                        lead_dt = created_at if isinstance(created_at, datetime) else datetime.now(timezone.utc)
                        c_tag = compute_lead_cycle(lead_dt, renewal_day)
                        updates_by_cycle.setdefault(c_tag, []).append(row_id)

                    for c_tag, id_list in updates_by_cycle.items():
                        # Update in chunks of 5000
                        chunk_size = 5000
                        for i in range(0, len(id_list), chunk_size):
                            chunk = id_list[i : i + chunk_size]
                            fmt_ids = ",".join(str(cid) for cid in chunk)
                            cur.execute(f"UPDATE `apollo_saved_leads` SET `cycle` = %s WHERE `id` IN ({fmt_ids});", (c_tag,))
                    
                    conn.commit()
                    print(f"  ✓ Processed '{raw_acc}': {len(rows):,d} leads tagged across {list(updates_by_cycle.keys())}")

            # Also backfill enrich_saved_leads if any rows
            cur.execute("SELECT COUNT(*) FROM `enrich_saved_leads` WHERE `cycle` = '' OR `cycle` IS NULL;")
            enrich_unassigned = cur.fetchone()[0]
            if enrich_unassigned > 0:
                print(f"\n  Backfilling {enrich_unassigned:,d} rows in `enrich_saved_leads`...")
                cur.execute("SELECT `id`, `account_used`, `created_at` FROM `enrich_saved_leads` WHERE `cycle` = '' OR `cycle` IS NULL;")
                rows = cur.fetchall()
                for row_id, raw_acc, created_at in rows:
                    acc_lower = (raw_acc or "").strip().lower()
                    info = acc_map.get(acc_lower)
                    renewal_day = info["renewal_day"] if info else 20
                    lead_dt = created_at if isinstance(created_at, datetime) else datetime.now(timezone.utc)
                    c_tag = compute_lead_cycle(lead_dt, renewal_day)
                    cur.execute("UPDATE `enrich_saved_leads` SET `cycle` = %s WHERE `id` = %s;", (c_tag, row_id))
                conn.commit()
                print("  ✓ `enrich_saved_leads` backfill complete.")

    print("\n" + "=" * 75)
    print("MIGRATION & BACKFILL COMPLETED SUCCESSFULLY!")
    print("=" * 75)


if __name__ == "__main__":
    migrate()
