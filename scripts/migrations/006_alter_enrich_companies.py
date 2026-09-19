import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import get_connection

def update_table():
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Check if crm_status column exists
            cur.execute("""
                SELECT COUNT(*) 
                FROM information_schema.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                  AND TABLE_NAME = 'enrich_companies' 
                  AND COLUMN_NAME = 'crm_status'
            """)
            if cur.fetchone()[0] == 0:
                cur.execute("ALTER TABLE `enrich_companies` ADD COLUMN `crm_status` VARCHAR(64) DEFAULT 'net_new' AFTER `website_link`;")
                print("[✓] Added `crm_status` column to `enrich_companies`.")
            else:
                print("[✓] `crm_status` column already exists.")

if __name__ == "__main__":
    update_table()
