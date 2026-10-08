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

cursor.execute("SELECT COUNT(*) as c FROM apollo_saved_leads WHERE batch=%s", (batch,))
print("Count in apollo_saved_leads:", cursor.fetchone()['c'])

cursor.execute("SELECT COUNT(*) as c FROM apollo_saved_leads WHERE batch=%s AND company_domain NOT IN (SELECT domain FROM emails)", (batch,))
print("Count not in emails (by domain):", cursor.fetchone()['c'])

cursor.close()
connection.close()
