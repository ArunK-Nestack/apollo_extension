import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

email = input("Enter email to check: ").strip().lower()

connection = psycopg.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
)

cursor = connection.cursor()

cursor.execute(
    """
    SELECT EXISTS (
        SELECT 1
        FROM contacts
        WHERE LOWER(email) = %s
    );
    """,
    (email,),
)

result = cursor.fetchone()

if result and result[0]:
    print("✅ EXISTS in Apollo database")
else:
    print("❌ NOT FOUND")

cursor.close()
connection.close()