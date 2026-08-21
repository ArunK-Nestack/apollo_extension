import csv
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_FOLDER = BASE_DIR / "data"

FIRST_NAME_COLUMNS = ["Contact : First name", "First Name", "First name"]
LAST_NAME_COLUMNS = ["Contact : Last name", "Last Name", "Last name"]
EMAIL_COLUMNS = ["Contact : Emails", "Email", "Emails", "Email Address"]
JOB_TITLE_COLUMNS = ["Contact : Job title", "Title", "Job Title", "Job title"]
COMPANY_COLUMNS = ["Company Name", "Company Name for Emails", "Company", "Account Name"]
WEBSITE_COLUMNS = ["Website", "Company Website"]
DOMAIN_COLUMNS = ["Email Domain", "Domain"]

EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

BATCH_SIZE = 5000


def get_field_val(row: dict, candidates: list[str]) -> str:
    for col in candidates:
        if col in row and row[col]:
            return str(row[col]).strip()
    return ""


def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = value.strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-z0-9]", "", value)
    return value


def get_email_domain(email: str) -> tuple[str, str]:
    if not email or "@" not in email:
        return "", ""
    domain = email.split("@", 1)[1].lower().strip()
    company_part = domain.split(".")[0]
    normalized_domain = normalize_text(company_part)
    return domain, normalized_domain


def extract_domain_from_url(url: str) -> tuple[str, str]:
    if not url:
        return "", ""
    val = url.strip().lower()
    if "://" not in val:
        val = f"https://{val}"
    try:
        host = urlparse(val).netloc.lower().lstrip("www.")
        if host:
            brand = host.split(".")[0]
            return host, normalize_text(brand)
    except Exception:
        pass
    return "", ""


csv_files = list(DATA_FOLDER.glob("*.csv"))

if not csv_files:
    print("No CSV files found inside the data folder.")
    raise SystemExit

csv_file = csv_files[0]
print(f"Importing: {csv_file.name}")

connection = psycopg.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
)

cursor = connection.cursor()

# Clear existing table to avoid duplicate accumulation
cursor.execute("TRUNCATE TABLE contacts RESTART IDENTITY;")
connection.commit()

inserted = 0
skipped = 0
batch = []

with open(
    csv_file,
    "r",
    encoding="utf-8-sig",
    newline=""
) as file:

    reader = csv.DictReader(file)

    if reader.fieldnames is None:
        print("CSV has no headers.")
        connection.close()
        raise SystemExit

    for row in reader:
        first_name = get_field_val(row, FIRST_NAME_COLUMNS)
        last_name = get_field_val(row, LAST_NAME_COLUMNS)
        job_title = get_field_val(row, JOB_TITLE_COLUMNS)
        email_field = get_field_val(row, EMAIL_COLUMNS)
        company = get_field_val(row, COMPANY_COLUMNS)
        website = get_field_val(row, WEBSITE_COLUMNS)
        explicit_domain = get_field_val(row, DOMAIN_COLUMNS)
        apollo_id = row.get("Apollo Contact Id", "") or row.get("Apollo Record Id", "")

        if not first_name or not last_name:
            skipped += 1
            continue

        normalized_name = normalize_text(f"{first_name} {last_name}")
        normalized_title = normalize_text(job_title)

        emails = EMAIL_PATTERN.findall(email_field) if email_field else []

        if emails:
            for email in emails:
                email = email.strip().lower()
                email_domain, normalized_domain = get_email_domain(email)

                batch.append(
                    (
                        email,
                        first_name,
                        last_name,
                        job_title,
                        normalized_name,
                        normalized_title,
                        email_domain,
                        normalized_domain,
                        "test_login",
                        csv_file.name,
                    )
                )
        else:
            # Handle records without explicit email
            email_domain = ""
            normalized_domain = ""

            if explicit_domain:
                email_domain = explicit_domain.lower()
                normalized_domain = normalize_text(explicit_domain.split(".")[0])
            elif website:
                email_domain, normalized_domain = extract_domain_from_url(website)
            elif company:
                normalized_company = normalize_text(company)
                email_domain = f"{normalized_company}.com"
                normalized_domain = normalized_company

            email_id = apollo_id if apollo_id else f"{normalized_name}_{normalized_domain}"
            placeholder_email = f"contact_{email_id}@{email_domain or 'unknown.com'}"

            batch.append(
                (
                    placeholder_email,
                    first_name,
                    last_name,
                    job_title,
                    normalized_name,
                    normalized_title,
                    email_domain,
                    normalized_domain,
                    "test_login",
                    csv_file.name,
                )
            )

        if len(batch) >= BATCH_SIZE:
            cursor.executemany(
                """
                INSERT INTO contacts (
                    email,
                    first_name,
                    last_name,
                    job_title,
                    normalized_name,
                    normalized_title,
                    email_domain,
                    normalized_domain,
                    source_login,
                    source_file
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                batch,
            )
            connection.commit()
            inserted += len(batch)
            print(f"Imported: {inserted:,}")
            batch.clear()

if batch:
    cursor.executemany(
        """
        INSERT INTO contacts (
            email,
            first_name,
            last_name,
            job_title,
            normalized_name,
            normalized_title,
            email_domain,
            normalized_domain,
            source_login,
            source_file
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        """,
        batch,
    )
    connection.commit()
    inserted += len(batch)

cursor.close()
connection.close()

print()
print("Import completed!")
print(f"Contacts imported: {inserted:,}")
print(f"Rows skipped: {skipped:,}")