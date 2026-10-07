"""
Database Migration Script: Add Page Breakdown to `extension_activity_log` and `apollo_saved_leads`
==================================================================================================
1. Extends `extension_activity_log` with:
   - `total_on_page` (INT)
   - `not_required_on_page` (INT)
   - `existing_on_page` (INT)
   - `guardrail_rejected_on_page` (INT)
   - `breakdown_json` (LONGTEXT)
   - Index on (`batch`, `page_number`)
2. Extends `apollo_saved_leads` and `enrich_saved_leads` with:
   - `page_number` (INT)
   - Index on (`batch`, `page_number`)
"""

import sys

sys.path.insert(0, ".")
from backend.api import get_connection, ensure_extension_activity_log_table, ensure_apollo_saved_leads_table


def main():
    with get_connection() as conn:
        print(">>> 1. Ensuring and migrating extension_activity_log...")
        ensure_extension_activity_log_table(conn)

        with conn.cursor() as cur:
            # Check existing columns in extension_activity_log
            cur.execute("SHOW COLUMNS FROM `extension_activity_log`")
            existing_cols = {row[0].lower() for row in cur.fetchall()}

            activity_new_cols = [
                ("total_on_page", "INT NULL DEFAULT 0 AFTER `page_number`"),
                ("not_required_on_page", "INT NULL DEFAULT 0 AFTER `required_on_page`"),
                ("existing_on_page", "INT NULL DEFAULT 0 AFTER `not_required_on_page`"),
                ("guardrail_rejected_on_page", "INT NULL DEFAULT 0 AFTER `existing_on_page`"),
                ("breakdown_json", "LONGTEXT NULL AFTER `page_url`"),
            ]

            for col_name, col_def in activity_new_cols:
                if col_name.lower() not in existing_cols:
                    print(f"  Adding `{col_name}` to extension_activity_log...")
                    cur.execute(f"ALTER TABLE `extension_activity_log` ADD COLUMN `{col_name}` {col_def};")

            # Check index
            cur.execute("SHOW INDEX FROM `extension_activity_log`")
            indexes = {row[2] for row in cur.fetchall()}
            if "idx_batch_page" not in indexes:
                print("  Adding index `idx_batch_page` to extension_activity_log...")
                cur.execute("ALTER TABLE `extension_activity_log` ADD INDEX `idx_batch_page` (`batch`, `page_number`);")

        print(">>> 2. Ensuring and migrating apollo_saved_leads and enrich_saved_leads...")
        ensure_apollo_saved_leads_table(conn)

        with conn.cursor() as cur:
            for tbl in ["apollo_saved_leads", "enrich_saved_leads"]:
                try:
                    cur.execute(f"SHOW TABLES LIKE '{tbl}'")
                    if not cur.fetchone():
                        continue
                    cur.execute(f"SHOW COLUMNS FROM `{tbl}`")
                    tbl_cols = {row[0].lower() for row in cur.fetchall()}
                    if "page_number" not in tbl_cols:
                        print(f"  Adding `page_number` to {tbl}...")
                        cur.execute(f"ALTER TABLE `{tbl}` ADD COLUMN `page_number` INT NULL DEFAULT NULL AFTER `batch`;")

                    cur.execute(f"SHOW INDEX FROM `{tbl}`")
                    tbl_idx = {row[2] for row in cur.fetchall()}
                    if "idx_batch_page" not in tbl_idx:
                        print(f"  Adding index `idx_batch_page` to {tbl}...")
                        cur.execute(f"ALTER TABLE `{tbl}` ADD INDEX `idx_batch_page` (`batch`, `page_number`);")
                except Exception as ex:
                    print(f"  Notice for {tbl}: {ex}")

        conn.commit()

        print("\n>>> Migration Complete! Verifying extension_activity_log columns:")
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM `extension_activity_log`")
            for row in cur.fetchall():
                print(f"  {row[0]:28} {row[1]}")


if __name__ == "__main__":
    main()
