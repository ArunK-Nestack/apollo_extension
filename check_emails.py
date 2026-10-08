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

# How many total in batch
cursor.execute("SELECT COUNT(*) as c FROM apollo_saved_leads WHERE batch=%s", (batch,))
total = cursor.fetchone()['c']

# How many are already in emails table (by exact name and domain)
cursor.execute("""
    SELECT COUNT(*) as c 
    FROM apollo_saved_leads a
    JOIN emails e ON a.company_domain = e.domain AND a.name = e.full_name
    WHERE a.batch=%s
""", (batch,))
exact_match = cursor.fetchone()['c']

# How many have domains that are in emails table
cursor.execute("""
    SELECT COUNT(DISTINCT a.id) as c 
    FROM apollo_saved_leads a
    JOIN emails e ON a.company_domain = e.domain
    WHERE a.batch=%s
""", (batch,))
domain_match = cursor.fetchone()['c']

print(f"Total in batch {batch}: {total}")
print(f"Exact matches in emails table: {exact_match}")
print(f"Domain matches in emails table: {domain_match}")

cursor.close()
connection.close()
