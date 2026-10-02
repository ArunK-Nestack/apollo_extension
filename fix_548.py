import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

connection = pymysql.connect(
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT", 3306)),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    autocommit=True
)
cursor = connection.cursor(pymysql.cursors.DictCursor)

# Find leads inserted in the last 15 minutes for this batch
batch_name = 'vijay_raghavan_nestack_com-sep_new'

cursor.execute("""
    SELECT id, company_domain 
    FROM apollo_saved_leads 
    WHERE batch=%s AND created_at > DATE_SUB(NOW(), INTERVAL 15 MINUTE)
""", (batch_name,))
recent_leads = cursor.fetchall()

print(f"Found {len(recent_leads)} recently inserted leads.")

# Group by domain
domain_to_ids = {}
for r in recent_leads:
    d = r['company_domain']
    if d not in domain_to_ids:
        domain_to_ids[d] = []
    domain_to_ids[d].append(r['id'])

ids_to_delete = []
for d, ids in domain_to_ids.items():
    if len(ids) > 1:
        # Keep the first one, delete the rest
        ids.sort()
        ids_to_delete.extend(ids[1:])

if ids_to_delete:
    print(f"Deleting {len(ids_to_delete)} duplicate leads to ensure 1 per company...")
    format_strings = ','.join(['%s'] * len(ids_to_delete))
    cursor.execute(f"DELETE FROM apollo_saved_leads WHERE id IN ({format_strings})", tuple(ids_to_delete))
    print(f"Successfully deleted {len(ids_to_delete)} extra leads.")
else:
    print("No duplicates found to delete.")

cursor.close()
connection.close()
