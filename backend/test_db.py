import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

try:
    connection = psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )

    cursor = connection.cursor()

    cursor.execute("SELECT NOW();")
    result = cursor.fetchone()

    if result is not None:
        print("Database connected successfully!")
        print("PostgreSQL time:", result[0])
    else:
        print("No result returned from PostgreSQL.")

    cursor.close()
    connection.close()

except Exception as error:
    print("Database connection failed!")
    print(error)