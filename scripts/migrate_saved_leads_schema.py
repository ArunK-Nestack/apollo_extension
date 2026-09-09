"""
Database Migration Script: Extend apollo_saved_leads with Enrichment Columns
===========================================================================
Safely adds firmographic and verified contact fields to apollo_saved_leads table
using non-destructive IF NOT EXISTS checks.
"""

import sys
import os
sys.path.insert(0, ".")

from backend.api import get_connection

NEW_COLUMNS = [
    ("email", "VARCHAR(255) DEFAULT '' AFTER job_title"),
    ("email_status", "VARCHAR(64) DEFAULT '' AFTER email"),
    ("annual_revenue", "VARCHAR(64) DEFAULT '' AFTER website_link"),
    ("employee_count", "INT UNSIGNED DEFAULT NULL AFTER annual_revenue"),
    ("industry", "VARCHAR(128) DEFAULT '' AFTER employee_count"),
    ("tech_stack", "JSON DEFAULT NULL AFTER industry"),
    ("keywords", "JSON DEFAULT NULL AFTER tech_stack"),
    ("company_phone", "VARCHAR(64) DEFAULT '' AFTER keywords"),
    ("hq_address", "VARCHAR(512) DEFAULT '' AFTER company_phone"),
    ("company_linkedin_url", "VARCHAR(512) DEFAULT '' AFTER linkedin_url"),
    ("account_used", "VARCHAR(128) DEFAULT '' AFTER segment"),
    ("credits_charged", "TINYINT UNSIGNED DEFAULT 0 AFTER account_used"),
    ("enriched_at", "TIMESTAMP NULL DEFAULT NULL AFTER credits_charged"),
]

def migrate():
    print("=================================================================")
    print(">>> MIGRATING `apollo_saved_leads` SCHEMA FOR IN-PLACE ENRICHMENT")
    print("=================================================================")
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Check existing columns
            cur.execute("DESCRIBE apollo_saved_leads;")
            existing_cols = set(r[0].lower() for r in cur.fetchall())
            print(f"Current columns in table: {len(existing_cols)}")

            added = 0
            skipped = 0
            for col_name, col_def in NEW_COLUMNS:
                if col_name.lower() in existing_cols:
                    print(f"  • Column '{col_name}' already exists (skipping)")
                    skipped += 1
                else:
                    sql = f"ALTER TABLE `apollo_saved_leads` ADD COLUMN `{col_name}` {col_def};"
                    cur.execute(sql)
                    print(f"  ✓ Added column: `{col_name}`")
                    added += 1

            # Check index on enriched_at
            cur.execute("SHOW INDEX FROM apollo_saved_leads;")
            indexes = set(r[2] for r in cur.fetchall())
            if "idx_enriched_at" not in indexes:
                cur.execute("ALTER TABLE `apollo_saved_leads` ADD INDEX `idx_enriched_at` (`enriched_at`);")
                print("  ✓ Added index: `idx_enriched_at`")
            else:
                print("  • Index `idx_enriched_at` already exists")

            conn.commit()

    print("=================================================================")
    print(f"MIGRATION COMPLETE: {added} columns added, {skipped} already present.")
    print("=================================================================")

if __name__ == "__main__":
    migrate()
