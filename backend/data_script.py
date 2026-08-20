import csv
import re
import os
import tempfile

FILE = r"C:\Users\test\Desktop\projects\apollo_extension\backend\data\sample.csv"
EMAIL_COLUMN = "Contact : Emails"
DOMAIN_COLUMN = "Email Domain"


def extract_domain(email):
    if not email:
        return ""

    email = email.strip().lower()

    match = re.search(r'@([a-z0-9.-]+\.[a-z]{2,})', email, re.IGNORECASE)

    if not match:
        return ""

    return match.group(1).rstrip(".,;:)")


with open(FILE, "r", encoding="utf-8-sig", newline="") as infile:
    reader = csv.DictReader(infile)

    fieldnames = list(reader.fieldnames or [])

    if DOMAIN_COLUMN not in fieldnames:
        email_index = fieldnames.index(EMAIL_COLUMN)
        fieldnames.insert(email_index + 1, DOMAIN_COLUMN)

    rows = []

    for row in reader:
        row[DOMAIN_COLUMN] = extract_domain(row.get(EMAIL_COLUMN, ""))
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

print(f"Updated {FILE}")
print(f"Processed {len(rows)} rows")