import os
import re
import unicodedata

import psycopg
from dotenv import load_dotenv

load_dotenv()


def normalize_text(value: str) -> str:
    value = value.strip().lower()

    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    value = re.sub(r"[^a-z0-9]", "", value)

    return value


def company_matches(
    apollo_company: str,
    crm_domain: str,
) -> bool:

    company = normalize_text(apollo_company)
    domain = normalize_text(crm_domain)

    if not company or not domain:
        return False

    # Exact:
    # VE Group ↔ ve-group.com
    if company == domain:
        return True

    # Brand/domain may be part of a longer legal company name:
    # Simplon Fahrrad GmbH ↔ simplon.com
    if len(domain) >= 4 and domain in company:
        return True

    return False


name = input("Apollo name: ").strip()
job_title = input("Apollo job title: ").strip()
company = input("Apollo company: ").strip()

normalized_name = normalize_text(name)
normalized_title = normalize_text(job_title)


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
    SELECT
        email,
        first_name,
        last_name,
        job_title,
        normalized_domain
    FROM contacts
    WHERE normalized_name = %s
      AND normalized_title = %s;
    """,
    (
        normalized_name,
        normalized_title,
    ),
)

rows = cursor.fetchall()

match = None

for row in rows:
    email = row[0]
    first_name = row[1]
    last_name = row[2]
    stored_title = row[3]
    stored_domain = row[4]

    if stored_domain and company_matches(
        company,
        stored_domain
    ):
        match = row
        break


print()

if match:
    print("✅ EXISTING CONTACT")
    print(f"Name: {match[1]} {match[2]}")
    print(f"Job title: {match[3]}")
    print(f"Email: {match[0]}")
    print(f"Domain/company key: {match[4]}")
else:
    print("❌ NO 3-FIELD MATCH")


cursor.close()
connection.close()