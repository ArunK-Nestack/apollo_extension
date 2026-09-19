#!/usr/bin/env python3
"""
Enrich Companies Apollo Account List Exporter
=============================================
Lists and exports unique company names and domains captured from Enrich.so 
via the Enrich extension into Apollo Web-ready CSVs for 0-Credit Account Import.

Apollo Web Account Import Workflow:
1. Run this script to generate clean CSV of unique companies (Company Name + Domain).
2. Go to Apollo Web > Search > Accounts > "Import CSV".
3. Upload the generated CSV and assign it to a new Account List (e.g. "Enrich Target Companies").
4. Go to Apollo > Search > People > Filter by Account List = "Enrich Target Companies".
5. Apply Title filters (C-Level, VP, Director) and reveal verified contacts with 100% company accuracy.
"""

import os
import sys
import csv
from datetime import datetime

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import get_connection

EXPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `enrich_companies` (
                `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                `batch` VARCHAR(64) NOT NULL DEFAULT 'enrich_companies_1',
                `company_name` VARCHAR(255) NOT NULL DEFAULT '',
                `domain` VARCHAR(255) NOT NULL DEFAULT '',
                `website_link` VARCHAR(512) DEFAULT '',
                `crm_status` VARCHAR(64) DEFAULT 'net_new',
                `source` VARCHAR(64) DEFAULT 'enrich_extension',
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX `idx_batch` (`batch`),
                INDEX `idx_company_name` (`company_name`),
                INDEX `idx_domain` (`domain`),
                UNIQUE KEY `unique_batch_domain` (`batch`, `domain`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)


def fetch_company_batches(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT `batch`, 
                   COUNT(*) as total_companies,
                   SUM(CASE WHEN `crm_status` = 'net_new' THEN 1 ELSE 0 END) as net_new_count,
                   SUM(CASE WHEN `crm_status` = 'existing_in_crm' THEN 1 ELSE 0 END) as existing_count,
                   MIN(`created_at`) as started_at, 
                   MAX(`created_at`) as last_added
            FROM `enrich_companies`
            GROUP BY `batch`
            ORDER BY `id` DESC;
        """)
        rows = cur.fetchall()
        return [
            {
                "batch": r[0],
                "total": r[1],
                "net_new": int(r[2] or 0),
                "existing": int(r[3] or 0),
                "started_at": str(r[4]) if r[4] else "N/A",
                "last_added": str(r[5]) if r[5] else "N/A",
            }
            for r in rows
        ]


def display_batches(batches):
    print("\n" + "=" * 95)
    print(" 🏢 ENRICH.SO UNIQUE COMPANIES — BATCH SUMMARY FOR APOLLO ACCOUNT LISTS")
    print("=" * 95)
    if not batches:
        print(" [!] No company batches found in table `enrich_companies` yet.")
        print("     Activate the Enrich extension on Enrich.so to collect unique companies automatically.")
        print("=" * 95)
        return

    print(f" {'#':<3} | {'Batch Name':<30} | {'Total':<8} | {'Net-New (CRM)':<14} | {'In CRM':<8} | {'Last Added'}")
    print("-" * 95)
    total_all = sum(b["total"] for b in batches)
    total_net_new = sum(b["net_new"] for b in batches)

    for i, b in enumerate(batches, start=1):
        print(f" [{i:<1}] | {b['batch']:<30} | {b['total']:<8,d} | {b['net_new']:<14,d} | {b['existing']:<8,d} | {b['last_added']}")

    print("-" * 95)
    print(f" TOTAL ACROSS ALL BATCHES: {total_all:,d} Companies ({total_net_new:,d} Net-New)")
    print("=" * 95)


def export_companies_to_csv(conn, batch_name=None, net_new_only=True):
    with conn.cursor() as cur:
        if batch_name:
            if net_new_only:
                cur.execute("""
                    SELECT `company_name`, `domain`, `website_link`, `batch`, `crm_status`
                    FROM `enrich_companies`
                    WHERE `batch` = %s AND `crm_status` = 'net_new'
                    ORDER BY `id` ASC;
                """, (batch_name,))
            else:
                cur.execute("""
                    SELECT `company_name`, `domain`, `website_link`, `batch`, `crm_status`
                    FROM `enrich_companies`
                    WHERE `batch` = %s
                    ORDER BY `id` ASC;
                """, (batch_name,))
        else:
            # All batches deduplicated by domain
            if net_new_only:
                cur.execute("""
                    SELECT `company_name`, `domain`, `website_link`, MAX(`batch`), 'net_new'
                    FROM `enrich_companies`
                    WHERE `crm_status` = 'net_new'
                    GROUP BY `domain`
                    ORDER BY `company_name` ASC;
                """)
            else:
                cur.execute("""
                    SELECT `company_name`, `domain`, `website_link`, MAX(`batch`), MAX(`crm_status`)
                    FROM `enrich_companies`
                    GROUP BY `domain`
                    ORDER BY `company_name` ASC;
                """)

        rows = cur.fetchall()

    if not rows:
        print("\n[!] No matching companies found to export.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = batch_name or "all_batches"
    mode_str = "net_new" if net_new_only else "all"
    filename = f"apollo_accounts_{tag}_{mode_str}_{stamp}.csv"
    filepath = os.path.join(EXPORTS_DIR, filename)

    headers = ["Company Name", "Website", "Domain", "Batch", "CRM Status"]

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in rows:
            c_name = r[0] or ""
            domain = r[1] or ""
            web = r[2] or (f"https://{domain}" if domain else "")
            batch = r[3] or ""
            status = r[4] or ""
            writer.writerow([c_name, web, domain, batch, status])

    print(f"\n[✓ SUCCESS] Exported {len(rows):,d} unique companies to:")
    print(f"    -> {filepath}")
    print(f"\n[NEXT STEPS IN APOLLO WEB (0 CREDITS)]: ")
    print(f"  1. Go to Apollo.io -> Search -> Companies / Accounts")
    print(f"  2. Click 'Import' -> 'Import CSV'")
    print(f"  3. Drag and drop this CSV file: {filename}")
    print(f"  4. Select 'Company Name' and 'Website' as matching columns")
    print(f"  5. Save to a new Account List named '{tag}'")
    print(f"  6. Now search People in Apollo filtering by Account List = '{tag}'!")


def main():
    while True:
        try:
            with get_connection() as conn:
                ensure_table(conn)
                batches = fetch_company_batches(conn)
                display_batches(batches)

                print("\nACTIONS:")
                print("  [1] Export Net-New companies for a specific batch (Recommended for Apollo Account List)")
                print("  [2] Export ALL companies for a specific batch (Include existing in CRM)")
                print("  [3] Export ALL Net-New companies across ALL batches (Global deduplicated)")
                print("  [4] Export ALL companies across ALL batches")
                print("  [5] Delete a batch from `enrich_companies`")
                print("  [6] Refresh list")
                print("  [7] Exit")

                choice = input("\nSelect an option (1-7): ").strip()

                if choice == "1":
                    if not batches:
                        print("No batches available.")
                        continue
                    b_idx = input(f"Enter batch number (1-{len(batches)}) or batch name: ").strip()
                    selected_batch = None
                    if b_idx.isdigit() and 1 <= int(b_idx) <= len(batches):
                        selected_batch = batches[int(b_idx) - 1]["batch"]
                    else:
                        for b in batches:
                            if b["batch"].lower() == b_idx.lower():
                                selected_batch = b["batch"]
                                break
                    if selected_batch:
                        export_companies_to_csv(conn, batch_name=selected_batch, net_new_only=True)
                    else:
                        print("[!] Invalid batch selection.")
                    input("\nPress Enter to continue...")

                elif choice == "2":
                    if not batches:
                        print("No batches available.")
                        continue
                    b_idx = input(f"Enter batch number (1-{len(batches)}) or batch name: ").strip()
                    selected_batch = None
                    if b_idx.isdigit() and 1 <= int(b_idx) <= len(batches):
                        selected_batch = batches[int(b_idx) - 1]["batch"]
                    else:
                        for b in batches:
                            if b["batch"].lower() == b_idx.lower():
                                selected_batch = b["batch"]
                                break
                    if selected_batch:
                        export_companies_to_csv(conn, batch_name=selected_batch, net_new_only=False)
                    else:
                        print("[!] Invalid batch selection.")
                    input("\nPress Enter to continue...")

                elif choice == "3":
                    export_companies_to_csv(conn, batch_name=None, net_new_only=True)
                    input("\nPress Enter to continue...")

                elif choice == "4":
                    export_companies_to_csv(conn, batch_name=None, net_new_only=False)
                    input("\nPress Enter to continue...")

                elif choice == "5":
                    if not batches:
                        print("No batches available.")
                        continue
                    b_idx = input(f"Enter batch number (1-{len(batches)}) or batch name to delete: ").strip()
                    selected_batch = None
                    if b_idx.isdigit() and 1 <= int(b_idx) <= len(batches):
                        selected_batch = batches[int(b_idx) - 1]["batch"]
                    else:
                        for b in batches:
                            if b["batch"].lower() == b_idx.lower():
                                selected_batch = b["batch"]
                                break
                    if selected_batch:
                        confirm = input(f"Type 'yes' to confirm deleting companies in batch '{selected_batch}': ").strip().lower()
                        if confirm == "yes":
                            with conn.cursor() as cur:
                                cur.execute("DELETE FROM `enrich_companies` WHERE `batch` = %s", (selected_batch,))
                                del_cnt = cur.rowcount
                            print(f"[✓] Deleted {del_cnt} companies from '{selected_batch}'.")
                    input("\nPress Enter to continue...")

                elif choice == "6":
                    continue
                elif choice in ["7", "q", "exit", "quit"]:
                    print("Goodbye!")
                    break
                else:
                    print("Invalid option.")
                    input("\nPress Enter to continue...")

        except KeyboardInterrupt:
            print("\nExiting.")
            break
        except Exception as e:
            print(f"\n[Error]: {e}")
            input("Press Enter to retry...")


if __name__ == "__main__":
    main()
