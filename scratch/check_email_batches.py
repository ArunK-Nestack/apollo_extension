import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pymysql
from backend.api import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        # Check all records in apollo_saved_leads with enriched_at or email today
        cur.execute("""
            SELECT batch, account_used, credits_charged, 
                   COUNT(*) as count,
                   MIN(created_at), MAX(created_at)
            FROM apollo_saved_leads
            WHERE email IS NOT NULL AND email != ''
            GROUP BY batch, account_used, credits_charged;
        """)
        print("=== SAVED LEADS WITH EMAIL ===")
        for r in cur.fetchall():
            print(r)

        # Check all records in apollo_saved_leads that were deleted today or exist
        cur.execute("""
            SELECT batch, COUNT(*)
            FROM apollo_saved_leads
            GROUP BY batch;
        """)
        print("\n=== ALL BATCHES COUNT ===")
        for r in cur.fetchall():
            print(r)
