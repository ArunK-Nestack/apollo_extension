import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pymysql
from backend.api import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        # Check apollo_saved_leads by account_used
        cur.execute("""
            SELECT account_used, 
                   COUNT(*) as total_leads, 
                   SUM(credits_charged) as total_credits,
                   COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END) as with_email,
                   MIN(created_at), MAX(created_at)
            FROM apollo_saved_leads
            GROUP BY account_used;
        """)
        print("\n--- APOLLO_SAVED_LEADS BY ACCOUNT_USED ---")
        for r in cur.fetchall():
            print(f"Account: {r[0]} | Leads: {r[1]} | Credits Charged: {r[2]} | With Email: {r[3]} | Dates: {r[4]} - {r[5]}")

        # Check total leads saved by batch with recruiting
        cur.execute("""
            SELECT batch, account_used, COUNT(*), SUM(credits_charged),
                   COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END) as with_email,
                   MIN(created_at), MAX(created_at)
            FROM apollo_saved_leads
            WHERE batch LIKE '%recruiting%' OR account_used LIKE '%recruiting%'
            GROUP BY batch, account_used;
        """)
        print("\n--- RECRUITING BATCHES & ACCOUNTS ---")
        for r in cur.fetchall():
            print(f"Batch: {r[0]} | Account: {r[1]} | Leads: {r[2]} | Credits: {r[3]} | With Email: {r[4]} | Dates: {r[5]} - {r[6]}")

        # Check today's apollo_saved_leads
        cur.execute("""
            SELECT batch, account_used, COUNT(*), SUM(credits_charged),
                   COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END) as with_email,
                   MIN(created_at), MAX(created_at)
            FROM apollo_saved_leads
            WHERE created_at >= CURDATE()
            GROUP BY batch, account_used;
        """)
        print("\n--- TODAY'S SAVED LEADS IN DB ---")
        for r in cur.fetchall():
            print(f"Batch: {r[0]} | Account: {r[1]} | Leads: {r[2]} | Credits: {r[3]} | With Email: {r[4]} | Dates: {r[5]} - {r[6]}")

