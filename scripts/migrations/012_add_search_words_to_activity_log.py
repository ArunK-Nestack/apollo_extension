"""
Database Migration Script: Add search_word and search_bar_word to extension_activity_log
Safely adds:
  - `search_word` (VARCHAR(255) NOT NULL DEFAULT '')
  - `search_bar_word` (VARCHAR(255) NOT NULL DEFAULT '')
Preserves all existing data and table structure intact.
"""

import sys
import pymysql

sys.path.insert(0, ".")
from backend.api import get_connection

def migrate():
    print(">>> Running Migration: 012_add_search_words_to_activity_log...")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM `extension_activity_log`")
            existing_cols = {row[0].lower() for row in cur.fetchall()}
            print(f"Existing columns ({len(existing_cols)}): {sorted(existing_cols)}")

            if "search_word" not in existing_cols:
                print("  Adding column `search_word`...")
                cur.execute(
                    "ALTER TABLE `extension_activity_log` ADD COLUMN `search_word` VARCHAR(255) NOT NULL DEFAULT '' AFTER `batch`;"
                )
            else:
                print("  `search_word` already exists, skipping.")

            if "search_bar_word" not in existing_cols:
                print("  Adding column `search_bar_word`...")
                cur.execute(
                    "ALTER TABLE `extension_activity_log` ADD COLUMN `search_bar_word` VARCHAR(255) NOT NULL DEFAULT '' AFTER `search_word`;"
                )
            else:
                print("  `search_bar_word` already exists, skipping.")

        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM `extension_activity_log`")
            cols_after = [row[0] for row in cur.fetchall()]
            print(">>> Verified columns in `extension_activity_log`:")
            for col in cols_after:
                print(f"  - {col}")

if __name__ == "__main__":
    migrate()
