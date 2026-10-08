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

batch_name = 'vijay_raghavan_nestack_com-sep_new'

# Get the recent 638 leads
cursor.execute("""
    SELECT id, company_domain 
    FROM apollo_saved_leads 
    WHERE batch=%s AND created_at > DATE_SUB(NOW(), INTERVAL 30 MINUTE)
""", (batch_name,))
recent_leads = cursor.fetchall()

ids_to_delete = []

for row in recent_leads:
    # Check if this domain existed in apollo_saved_leads BEFORE 30 mins ago
    cursor.execute("""
        SELECT 1 
        FROM apollo_saved_leads 
        WHERE company_domain=%s AND id != %s AND created_at <= DATE_SUB(NOW(), INTERVAL 30 MINUTE)
        LIMIT 1
    """, (row['company_domain'], row['id']))
    
    if cursor.fetchone():
        ids_to_delete.append(row['id'])

if ids_to_delete:
    print(f"Found {len(ids_to_delete)} leads to delete because their company was already in DB.")
    format_strings = ','.join(['%s'] * len(ids_to_delete))
    cursor.execute(f"DELETE FROM apollo_saved_leads WHERE id IN ({format_strings})", tuple(ids_to_delete))
    print("Deleted successfully.")
else:
    print("No leads to delete.")

# Final check
cursor.execute("""
    SELECT COUNT(*) as c 
    FROM apollo_saved_leads 
    WHERE batch=%s AND created_at > DATE_SUB(NOW(), INTERVAL 30 MINUTE)
""", (batch_name,))
print(f"Remaining recently inserted leads: {cursor.fetchone()['c']}")

cursor.close()
connection.close()
