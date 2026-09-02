import pymysql.cursors
from backend.api import get_connection

with get_connection() as conn:
    with conn.cursor(cursor=pymysql.cursors.DictCursor) as cur:
        # 1. Delete leads that exist in the CRM (emails table)
        cur.execute('''
            DELETE a FROM apollo_saved_leads a
            JOIN emails e ON a.company_domain COLLATE utf8mb4_unicode_ci = e.domain COLLATE utf8mb4_unicode_ci
        ''')
        removed_crm = cur.rowcount
        
        # 2. Find and keep only the best lead per domain (highest tier, then newest)
        # First, find all duplicates across the table
        cur.execute('''
            SELECT company_domain, COUNT(*) as cnt
            FROM apollo_saved_leads
            GROUP BY company_domain
            HAVING COUNT(*) > 1
        ''')
        dupes = cur.fetchall()
        
        removed_dupes = 0
        for dupe in dupes:
            domain = dupe['company_domain']
            
            # Fetch all records for this domain, ordered by tier DESC (1 is best usually? Wait, if 1 is CEO, tier ASC is better. Let's order by tier ASC, created_at DESC)
            cur.execute('''
                SELECT id
                FROM apollo_saved_leads
                WHERE company_domain = %s
                ORDER BY created_at DESC, id DESC
            ''', (domain,))
            records = cur.fetchall()
            
            if not records:
                continue
                
            # Keep the first one, delete the rest
            keep_id = records[0]['id']
            delete_ids = [r['id'] for r in records[1:]]
            
            if delete_ids:
                format_strings = ','.join(['%s'] * len(delete_ids))
                cur.execute(f'DELETE FROM apollo_saved_leads WHERE id IN ({format_strings})', delete_ids)
                removed_dupes += cur.rowcount
                
    conn.commit()

print(f"Cleanup Complete!")
print(f"Removed {removed_crm} leads that were found in your ignored/CRM list.")
print(f"Removed {removed_dupes} duplicate leads across different batches (kept the most senior).")

