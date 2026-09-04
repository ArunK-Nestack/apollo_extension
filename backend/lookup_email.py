import os
import sys
import pymysql
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv()

host = os.getenv("DB_HOST", "localhost").strip()
port = int(os.getenv("DB_PORT", "3306").strip())
user = os.getenv("DB_USER", "root").strip()
password = os.getenv("DB_PASSWORD", "").strip()
database = os.getenv("DB_NAME", "apollo_scrapers").strip()
table = os.getenv("DB_TABLE", "emails").strip()

if len(sys.argv) > 1:
    email = sys.argv[1].strip().lower()
else:
    email = input("Enter email to check: ").strip().lower()

try:
    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        connect_timeout=15
    )

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT `id`, `email`, `full_name`, `domain` FROM `{table}` WHERE `email` = %s LIMIT 1;", (email,))
        row = cursor.fetchone()

        if row:
            print(f"EXISTS in MySQL CRM: ID={row[0]}, Name={row[2]}, Domain={row[3]}")
        else:
            print("NOT FOUND in CRM emails table")

    connection.close()

except Exception as error:
    print("Database lookup error:", error)