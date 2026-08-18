import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

connection = psycopg.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
)

cursor = connection.cursor()

# First name
cursor.execute(
    """
    ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS first_name TEXT;
    """
)

# Last name
cursor.execute(
    """
    ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS last_name TEXT;
    """
)

# Job title
cursor.execute(
    """
    ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS job_title TEXT;
    """
)

# Normalized full name
cursor.execute(
    """
    ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS normalized_name TEXT;
    """
)

# Normalized job title
cursor.execute(
    """
    ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS normalized_title TEXT;
    """
)

# Original email domain
cursor.execute(
    """
    ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS email_domain TEXT;
    """
)

# Normalized company/domain
cursor.execute(
    """
    ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS normalized_domain TEXT;
    """
)

# Fast name + title lookup
cursor.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_contacts_name_title
    ON contacts (normalized_name, normalized_title);
    """
)

# Fast domain lookup
cursor.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_contacts_domain
    ON contacts (normalized_domain);
    """
)

connection.commit()

cursor.close()
connection.close()

print("Matching columns and indexes created successfully!")