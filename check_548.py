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

batch_name = 'vijay_raghavan_nestack_com-sep_new'
cursor.execute("SELECT COUNT(DISTINCT company_domain) as c FROM apollo_saved_leads WHERE batch=%s AND created_at > DATE_SUB(NOW(), INTERVAL 15 MINUTE)", (batch_name,))
unique_domains = cursor.fetchone()['c']

cursor.execute("SELECT COUNT(*) as c FROM apollo_saved_leads WHERE batch=%s AND created_at > DATE_SUB(NOW(), INTERVAL 15 MINUTE)", (batch_name,))
total_leads = cursor.fetchone()['c']

print(f"Unique domains among recent: {unique_domains}")
print(f"Total recent leads: {total_leads}")

cursor.close()
connection.close()
