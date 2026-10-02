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
    SELECT batch, company_domain, name, apollo_id
    FROM apollo_saved_leads 
    WHERE name = 'Jackie Marko' AND company_domain = 'whoisprivacyservice.org'
""")
res = cursor.fetchall()

print("Duplicates for Jackie Marko:")
for r in res:
    print(r)

cursor.close()
connection.close()
