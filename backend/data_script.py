import csv
import os
import re
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FILE = BASE_DIR / "data" / "sample.csv"
DOMAIN_COLUMN = "Email Domain"

# Recognized email column headers in order of preference
POSSIBLE_EMAIL_COLUMNS = [
    "Contact : Emails",
    "Email",
    "Emails",
    "Email Address",
]


def extract_domain(email: str) -> str:
    if not email:
        return ""

    email = email.strip().lower()
    match = re.search(r'@([a-z0-9.-]+\.[a-z]{2,})', email, re.IGNORECASE)

    if not match:
        return ""

    return match.group(1).rstrip(".,;:)")


if not FILE.exists():
    raise FileNotFoundError(f"Target file not found: {FILE}")

with open(FILE, "r", encoding="utf-8-sig", newline="") as infile:
    reader = csv.DictReader(infile)
    fieldnames = list(reader.fieldnames or [])

    # Find the matching email column
    email_col = next(
        (col for col in POSSIBLE_EMAIL_COLUMNS if col in fieldnames),
        None,
    )

    if not email_col:
        raise ValueError(f"None of {POSSIBLE_EMAIL_COLUMNS} found in CSV headers.")

    if DOMAIN_COLUMN not in fieldnames:
        email_index = fieldnames.index(email_col)
        fieldnames.insert(email_index + 1, DOMAIN_COLUMN)

    rows = []
    domain_count = 0

    for row in reader:
        extracted = extract_domain(row.get(email_col, ""))
        row[DOMAIN_COLUMN] = extracted
        if extracted:
            domain_count += 1
        rows.append(row)


# Write to temporary file first
fd, temp_path = tempfile.mkstemp(suffix=".csv")
os.close(fd)

with open(temp_path, "w", encoding="utf-8-sig", newline="") as outfile:
    writer = csv.DictWriter(outfile, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

# Replace original only after successful write
os.replace(temp_path, FILE)

print(f"Updated: {FILE}")
print(f"Total processed rows: {len(rows)}")
print(f"Rows with extracted domains: {domain_count}")