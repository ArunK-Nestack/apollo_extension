import os
import pymysql
from dotenv import load_dotenv

load_dotenv()
conn = pymysql.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER', 'nestack'),
    password=os.getenv('DB_PASSWORD', ''),
    database=os.getenv('DB_NAME', 'apollo_scrapers')
)

with conn.cursor(pymysql.cursors.DictCursor) as cur:
    cur.execute("""
        SELECT id, apollo_id, name, first_name, last_name, job_title, company, company_domain, website_link, location, linkedin_url, apollo_profile_url, segment, created_at
        FROM apollo_saved_leads
        WHERE batch = 'batch_1' AND id >= 46069
        ORDER BY id ASC
    """)
    rows = cur.fetchall()

print(f"Total extracted rows: {len(rows)}")
for idx, r in enumerate(rows, 1):
    print(f"{idx:02d}. [ID {r['id']}] {r['name']} | {r['job_title']} | {r['company']} | Domain: '{r['company_domain']}' | Link: '{r['website_link']}'")
