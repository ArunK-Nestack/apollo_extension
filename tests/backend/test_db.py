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
        cursor.execute("SELECT NOW(), VERSION();")
        now_val, version_val = cursor.fetchone()
        print("MySQL Database connected successfully!")
        print(f"  Host: {host}:{port}")
        print(f"  Database: {database}")
        print(f"  MySQL Version: {version_val}")
        print(f"  Server Time: {now_val}")

        cursor.execute(f"SELECT COUNT(*) FROM `{table}`;")
        count = cursor.fetchone()[0]
        print(f"  Table '{table}' row count: {count:,}")

    connection.close()

except Exception as error:
    print("MySQL Database connection failed!")
    print(error)