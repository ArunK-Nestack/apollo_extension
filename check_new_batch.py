import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

connection = pymysql.connect(
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT", 3306)),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME")
)
cursor = connection.cursor(pymysql.cursors.DictCursor)

batch = 'vijay_raghavan_nestack_com-sep_new'

# 1. Total in batch
cursor.execute("SELECT COUNT(*) as c FROM apollo_saved_leads WHERE batch=%s", (batch,))
total = cursor.fetchone()['c']

# 2. How many of them exist in OTHER batches (i.e. already saved in DB from before)
cursor.execute("""
    SELECT COUNT(DISTINCT a.id) as c
    FROM apollo_saved_leads a
    JOIN apollo_saved_leads b ON a.name = b.name AND a.company_domain = b.company_domain
    WHERE a.batch = %s AND b.batch != %s
""", (batch, batch))
dup_in_other = cursor.fetchone()['c']

# 3. How many are completely unique to this batch (NOT in any other batch)
unique = total - dup_in_other

print(f"Total leads in batch '{batch}': {total}")
print(f"Leads that ALREADY EXISTED in other batches: {dup_in_other}")
print(f"Leads that are COMPLETELY NEW (not in other batches): {unique}")

cursor.close()
connection.close()
