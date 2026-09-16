import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pymysql
from backend.api import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, batch, apollo_id, login_email, outcome, credits_charged, attempted_at
            FROM batch_enrichment_ledger
            ORDER BY id DESC
            LIMIT 50;
        """)
        print("--- LATEST 50 ROWS IN batch_enrichment_ledger ---")
        rows = cur.fetchall()
        print(f"Total fetched: {len(rows)}")
        for r in rows:
            print(r)
