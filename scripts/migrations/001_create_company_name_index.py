#!/usr/bin/env python3
import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import backend.api as api

def main():
    conn = api.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW INDEX FROM `emails` WHERE Key_name = 'idx_emails_company_name'")
            idx = cur.fetchone()
            if not idx:
                print("Creating B-Tree index `idx_emails_company_name` on `emails` (company_name)...")
                t0 = time.time()
                cur.execute("ALTER TABLE `emails` ADD INDEX `idx_emails_company_name` (`company_name`)")
                conn.commit()
                print(f"✓ Index created in {time.time() - t0:.2f}s!")
            else:
                print("✓ Index `idx_emails_company_name` already exists!")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
