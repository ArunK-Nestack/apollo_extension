import csv
import os
import re
import unicodedata
from pathlib import Path

import psycopg
from dotenv import load_dotenv


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_FOLDER = BASE_DIR / "data"

FIRST_NAME_COLUMN = "Contact : First name"
LAST_NAME_COLUMN = "Contact : Last name"
EMAIL_COLUMN = "Contact : Emails"
JOB_TITLE_COLUMN = "Contact : Job title"

EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

BATCH_SIZE = 5000


def normalize_text(value: str) -> str:
    """
    Example:
    'VE Group' -> 'vegroup'
    'Chief Operations Officer' -> 'chiefoperationsofficer'
    """

    value = value.strip().lower()

    # Remove accents
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    # Keep only letters and numbers
    value = re.sub(r"[^a-z0-9]", "", value)

    return value


def get_email_domain(email: str) -> tuple[str, str]:
    """
    Example:
    joffrey@ve-group.com

    Returns:
    ('ve-group.com', 'vegroup')
    """

    domain = email.split("@", 1)[1].lower().strip()

    # For version 1, use the part before the first dot
    company_part = domain.split(".")[0]

    normalized_domain = normalize_text(company_part)

    return domain, normalized_domain


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

    required_columns = [
        FIRST_NAME_COLUMN,
        LAST_NAME_COLUMN,
        EMAIL_COLUMN,
        JOB_TITLE_COLUMN,
    ]

    if reader.fieldnames is None:
        print("CSV has no headers.")
        connection.close()
        raise SystemExit

    for column in required_columns:
        if column not in reader.fieldnames:
            print(f'Column "{column}" was not found.')
            connection.close()
            raise SystemExit


    for row in reader:

        first_name = (
            row.get(FIRST_NAME_COLUMN, "") or ""
        ).strip()

        last_name = (
            row.get(LAST_NAME_COLUMN, "") or ""
        ).strip()

        job_title = (
            row.get(JOB_TITLE_COLUMN, "") or ""
        ).strip()

        email_field = (
            row.get(EMAIL_COLUMN, "") or ""
        ).strip()


        if not first_name or not last_name or not email_field:
            skipped += 1
            continue


        emails = EMAIL_PATTERN.findall(email_field)

        if not emails:
            skipped += 1
            continue


        normalized_name = normalize_text(
            f"{first_name} {last_name}"
        )

        normalized_title = normalize_text(
            job_title
        )


        for email in emails:

            email = email.strip().lower()

            email_domain, normalized_domain = (
                get_email_domain(email)
            )

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

                print(
                    f"Imported: {inserted:,}"
                )

                batch.clear()


# Insert remaining records
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