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

cursor.execute("""
    SELECT company_domain, name, COUNT(*) as cnt 
    FROM apollo_saved_leads 
    GROUP BY company_domain, name 
    HAVING cnt > 1 
    ORDER BY cnt DESC;
""")
duplicates_name = cursor.fetchall()

cursor.execute("""
    SELECT company_domain, COUNT(*) as cnt 
    FROM apollo_saved_leads 
    GROUP BY company_domain 
    HAVING cnt > 1 
    ORDER BY cnt DESC
    LIMIT 20;
""")
duplicates_domain = cursor.fetchall()

print(f"Exact person duplicates (name + domain): {len(duplicates_name)}")
for r in duplicates_name[:5]:
    print(r)

print(f"\nDomain duplicates (multiple people from same company): {len(duplicates_domain)}")
for r in duplicates_domain[:5]:
    print(r)

cursor.close()
connection.close()
