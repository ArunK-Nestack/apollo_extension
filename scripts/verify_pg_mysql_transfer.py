import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg
import pymysql

PROJECT_ROOT = Path(r"c:\Users\test\Desktop\projects\apollo_extension")
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / "freshsales_agent" / ".env")
load_dotenv(PROJECT_ROOT / ".env", override=False)

print("Connecting to PostgreSQL...")
pg_conn = psycopg.connect(
    host=os.getenv("PG_HOST", "127.0.0.1"),
    port=int(os.getenv("PG_PORT", "5432")),
    user=os.getenv("PG_USER", "postgres"),
    password=os.getenv("PG_PASS", "postgres"),
    dbname=os.getenv("PG_DB", "freshsales_crm")
)
print("Connected to PostgreSQL.")

print("Connecting to MySQL...")
my_conn = pymysql.connect(
    host=os.getenv("DB_HOST", "127.0.0.1"),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "apollo_scrapers")
)
print("Connected to MySQL.")

print("\n--- CHECKING OVERALL TABLE COUNTS ---")
with pg_conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM freshsales_contacts;")
    pg_total = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM freshsales_contacts WHERE email IS NOT NULL AND trim(email) != '';")
    pg_valid_emails = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT lower(trim(email))) FROM freshsales_contacts WHERE email IS NOT NULL AND trim(email) != '';")
    pg_distinct_emails = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM freshsales_contacts WHERE job_title IS NOT NULL AND trim(job_title) != '';")
    pg_with_title = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT lower(trim(email))) FROM freshsales_contacts WHERE job_title IS NOT NULL AND trim(job_title) != '' AND email IS NOT NULL AND trim(email) != '';")
    pg_distinct_with_title = cur.fetchone()[0]

with my_conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM emails;")
    my_total = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT lower(trim(email))) FROM emails WHERE email IS NOT NULL AND trim(email) != '';")
    my_distinct_emails = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM emails WHERE job_title IS NOT NULL AND trim(job_title) != '';")
    my_with_title = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT lower(trim(email))) FROM emails WHERE job_title IS NOT NULL AND trim(job_title) != '' AND email IS NOT NULL AND trim(email) != '';")
    my_distinct_with_title = cur.fetchone()[0]

print(f"PostgreSQL freshsales_contacts total rows        : {pg_total:,}")
print(f"PostgreSQL contacts with valid email             : {pg_valid_emails:,}")
print(f"PostgreSQL distinct valid emails                 : {pg_distinct_emails:,}")
print(f"PostgreSQL rows with non-empty job_title         : {pg_with_title:,}")
print(f"PostgreSQL distinct emails with job_title        : {pg_distinct_with_title:,}")

print(f"\nMySQL emails table total rows                    : {my_total:,}")
print(f"MySQL distinct emails                            : {my_distinct_emails:,}")
print(f"MySQL rows with non-empty job_title              : {my_with_title:,}")
print(f"MySQL distinct emails with job_title             : {my_distinct_with_title:,}")

print(f"\nDiscrepancy (PG with title vs MySQL with title)  : {pg_distinct_with_title - my_distinct_with_title:,}")

# Let's inspect the highest ID in PostgreSQL and whether the tail rows reached MySQL
print("\n--- CHECKING LATEST POSTGRESQL CONTACTS IN MYSQL ---")
with pg_conn.cursor() as cur:
    cur.execute("""
        SELECT id, lower(trim(email)), trim(job_title) 
        FROM freshsales_contacts 
        WHERE email IS NOT NULL AND email != '' AND job_title IS NOT NULL AND trim(job_title) != ''
        ORDER BY id DESC LIMIT 10;
    """)
    latest_pg = cur.fetchall()

with my_conn.cursor() as cur:
    for pid, em, title in latest_pg:
        cur.execute("SELECT id, email, job_title FROM emails WHERE email = %s;", (em,))
        m = cur.fetchone()
        status = "MATCH" if (m and m[2] == title) else ("TITLE_MISMATCH" if m else "MISSING_FROM_MYSQL")
        print(f"PG ID: {pid} | {em} | Status: {status} | MySQL: {m}")

# Let's check where the gap of 67,500 starts!
print("\n--- FINDING WHERE THE GAP OCCURS ---")
# We scan by ID chunks in PG to see where titles stop matching MySQL
with pg_conn.cursor() as cur:
    cur.execute("SELECT min(id), max(id) FROM freshsales_contacts WHERE job_title IS NOT NULL AND trim(job_title) != '';")
    min_id, max_id = cur.fetchone()

print(f"PostgreSQL ID range: {min_id:,} to {max_id:,}")
