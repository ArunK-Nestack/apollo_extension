"""
Database Migration Script: Create `extension_activity_log`
==========================================================
Stores Apollo scraper extension activity: start, login selected, page loaded/advanced,
with login owner, cycle tag, page number and required-lead counts.
"""

import sys

sys.path.insert(0, ".")
from backend.api import get_connection, ensure_extension_activity_log_table


def main():
    with get_connection() as conn:
        ensure_extension_activity_log_table(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM `extension_activity_log`")
            for row in cur.fetchall():
                print(f"  {row[0]:18} {row[1]}")
    print("extension_activity_log is ready.")


if __name__ == "__main__":
    main()
