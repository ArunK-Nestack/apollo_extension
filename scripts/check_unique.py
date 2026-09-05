import sys
import os
sys.path.append(os.getcwd())
from backend.api import get_connection, get_target_table_schema, is_mysql_conn

batch_name = 'vijay_raghavan_nestack_com-sep_new'
with get_connection() as conn:
    schema = get_target_table_schema(conn)
    tbl_name = schema['table_name']
    domain_col = schema['email_domain']

    with conn.cursor() as cur:
        # 1. Total leads in batch
        cur.execute(f"SELECT COUNT(*) FROM apollo_saved_leads WHERE batch='{batch_name}';")
        total_leads = cur.fetchone()[0]

        # 2. Check for exact duplicates inside the batch (same name and domain)
        cur.execute(f"""
            SELECT name, company_domain, COUNT(*) 
            FROM apollo_saved_leads 
            WHERE batch='{batch_name}' 
            GROUP BY name, company_domain 
            HAVING COUNT(*) > 1;
        """)
        internal_dupes = cur.fetchall()

        # 3. Check how many domains in this batch already exist in the CRM table
        cur.execute(f"""
            SELECT a.company_domain 
            FROM apollo_saved_leads a
            JOIN {tbl_name} e ON a.company_domain = e.{domain_col}
            WHERE a.batch='{batch_name}'
            GROUP BY a.company_domain;
        """)
        crm_domain_dupes = cur.fetchall()

        print(f'Total Leads in {batch_name}: {total_leads}')
        print(f'Internal Duplicates (same person/domain): {len(internal_dupes)}')
        print(f'Domains overlapping with CRM: {len(crm_domain_dupes)}')
