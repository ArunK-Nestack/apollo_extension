import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pymysql
from backend.api import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT batch, account_used, credits_charged, 
                   COUNT(*) as total_leads, 
                   COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END) as with_email,
                   COUNT(CASE WHEN linkedin_url IS NOT NULL AND linkedin_url != '' THEN 1 END) as with_linkedin,
                   MIN(created_at), MAX(created_at)
            FROM apollo_saved_leads
            WHERE batch = 'recruiting@nestack.com(aug 16 - sep 16)'
            GROUP BY batch, account_used, credits_charged;
        """)
        for r in cur.fetchall():
            print("Batch Summary:", r)

        cur.execute("""
            SELECT id, apollo_id, name, first_name, last_name, job_title, company, company_domain, email, credits_charged, created_at
            FROM apollo_saved_leads
            WHERE batch = 'recruiting@nestack.com(aug 16 - sep 16)'
            LIMIT 5;
        """)
        print("\nSample Leads:")
        for r in cur.fetchall():
            print(r)
