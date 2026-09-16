import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pymysql
from backend.api import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        # Check all tables with records mentioning recruiting
        for tbl in ['batch_enrichment_ledger', 'apollo_saved_leads', 'detected_leads']:
            print(f"\n=== TABLE: {tbl} ===")
            cur.execute(f"SHOW COLUMNS FROM `{tbl}`;")
            cols = [c[0] for c in cur.fetchall()]
            
            # Find any column that could have email/account/batch
            matching_cols = [c for c in cols if any(k in c.lower() for k in ['email', 'account', 'batch', 'login'])]
            print(f"Candidate cols: {matching_cols}")
            
            where_clauses = [f"`{c}` LIKE '%recruiting%'" for c in matching_cols]
            if where_clauses:
                query = f"SELECT COUNT(*) FROM `{tbl}` WHERE {' OR '.join(where_clauses)};"
                cur.execute(query)
                print(f"Total rows matching 'recruiting': {cur.fetchone()[0]}")

        # Check all credits charged ever in apollo_saved_leads
        print("\n=== TOTAL CREDITS IN apollo_saved_leads BY BATCH & ACCOUNT ===")
        cur.execute("""
            SELECT batch, account_used, COUNT(*), SUM(credits_charged),
                   MIN(created_at), MAX(created_at)
            FROM apollo_saved_leads
            WHERE credits_charged > 0
            GROUP BY batch, account_used;
        """)
        for r in cur.fetchall():
            print(r)

        # Check all records in batch_enrichment_ledger
        print("\n=== TOTAL IN batch_enrichment_ledger BY ACCOUNT & BATCH ===")
        cur.execute("""
            SELECT login_email, batch, COUNT(*), SUM(credits_charged),
                   MIN(attempted_at), MAX(attempted_at)
            FROM batch_enrichment_ledger
            GROUP BY login_email, batch;
        """)
        for r in cur.fetchall():
            print(r)

        # Check if there are any leads in apollo_saved_leads with email found today
        print("\n=== LEADS WITH EMAIL TODAY ===")
        cur.execute("""
            SELECT batch, account_used, COUNT(*), 
                   COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END) as has_email,
                   COUNT(CASE WHEN phone_number IS NOT NULL AND phone_number != '' THEN 1 END) as has_phone
            FROM apollo_saved_leads
            WHERE DATE(created_at) = CURDATE()
            GROUP BY batch, account_used;
        """)
        for r in cur.fetchall():
            print(r)
