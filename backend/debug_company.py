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
        c
        for c in value
        if not unicodedata.combining(c)
    )

    return re.sub(
        r"[^a-z0-9]",
        "",
        value,
    )


def get_domain_brand(domain: str) -> str:
    domain = domain.strip().lower()

    if "@" in domain:
        domain = domain.split("@", 1)[1]

    parts = domain.split(".")

    if (
        len(parts) >= 3
        and len(parts[-1]) == 2
        and parts[-2] in {
            "co", "com", "org",
            "net", "gov", "edu", "ac"
        }
    ):
        return parts[-3]

    if len(parts) >= 2:
        return parts[-2]

    return parts[0]


def tokens(value: str) -> set[str]:
    value = value.lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        c
        for c in value
        if not unicodedata.combining(c)
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return set(value.split())


apollo_company = "WSO Worldwide Security Options"

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
        email_domain,
        normalized_domain
    FROM contacts
    WHERE normalized_name = %s;
    """,
    ("johnnytorres",),
)

rows = cursor.fetchall()

for row in rows:
    print("\nCRM ROW:")
    print("Email:", row[0])
    print("Name:", row[1], row[2])
    print("Job title:", row[3])
    print("Stored email_domain:", row[4])
    print("Stored normalized_domain:", row[5])

    brand = get_domain_brand(row[4] or "")

    print()
    print("Apollo company:", apollo_company)
    print("Domain brand:", brand)

    print(
        "Apollo compact:",
        normalize_text(apollo_company),
    )

    print(
        "Brand compact:",
        normalize_text(brand),
    )

    print(
        "Apollo tokens:",
        tokens(apollo_company),
    )

    print(
        "Domain tokens:",
        tokens(brand),
    )

cursor.close()
connection.close()