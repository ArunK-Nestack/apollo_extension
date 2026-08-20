import os

import psycopg
from dotenv import load_dotenv

from api import (
    clean_domain_host,
    company_matches,
    company_token_list,
    company_variants,
    domain_variants,
    get_domain_brand,
    get_domain_parts,
)


load_dotenv()


# Change these two values while debugging a real case.
apollo_company = "WSO Worldwide Security Options"
normalized_name = "johnnytorres"


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
    (normalized_name,),
)

rows = cursor.fetchall()

print("\n=== APOLLO COMPANY ===")
print("Company:", apollo_company)
print("Tokens:", company_token_list(apollo_company))
print("Variants:", sorted(company_variants(apollo_company)))


for row in rows:
    email_domain = row[4] or ""

    host, brand, suffix = get_domain_parts(
        email_domain
    )

    print("\n" + "=" * 60)
    print("CRM ROW")
    print("=" * 60)
    print("Email:", row[0])
    print("Name:", row[1], row[2])
    print("Job title:", row[3])
    print("Stored email_domain:", email_domain)
    print("Stored normalized_domain:", row[5])

    print("\nDOMAIN PARSING")
    print("Clean host:", clean_domain_host(email_domain))
    print("Registrable brand:", get_domain_brand(email_domain))
    print("Suffix:", suffix)
    print("Domain variants:", sorted(domain_variants(email_domain)))

    print("\nMATCH RESULT")
    print(
        "✅ MATCH"
        if company_matches(
            apollo_company,
            email_domain,
        )
        else "❌ NO MATCH"
    )


cursor.close()
connection.close()