"""
Import September 2026 Export Files into MySQL `emails` Table
============================================================
Scans a target directory (default: 'G:\\My Drive\\exported files\\september 2026')
for all .xlsx and .csv files, extracts unique emails, full names, domains,
and company names, and imports them into MySQL `emails` table using INSERT IGNORE.
"""

import os
import sys
import argparse
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.api import get_connection, extract_root_domain, _s


def import_september_emails(folder_path):
    if not os.path.exists(folder_path):
        print(f"[!] Target path does not exist: {folder_path}")
        return

    all_files = os.listdir(folder_path)
    supported_files = [
        os.path.join(folder_path, f)
        for f in all_files
        if f.lower().endswith(".xlsx") or f.lower().endswith(".csv")
    ]
    gsheet_files = [f for f in all_files if f.lower().endswith(".gsheet")]

    print("=" * 80)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] SEPTEMBER 2026 EMAIL IMPORTER")
    print(f"Source Folder: {folder_path}")
    print(f"Supported Spreadsheets Found (.xlsx/.csv): {len(supported_files)}")
    if gsheet_files:
        print(f"Cloud Google Sheets Found (.gsheet): {len(gsheet_files)} (Must be downloaded as .xlsx/.csv)")
    print("=" * 80)

    if not supported_files:
        print("[!] No .xlsx or .csv files found to import.")
        return

    records = []
    seen_in_batch = set()
    total_raw = 0

    for fp in supported_files:
        fname = os.path.basename(fp)
        try:
            if fp.lower().endswith(".xlsx"):
                df = pd.read_excel(fp)
            else:
                df = pd.read_csv(fp)

            file_rows = len(df)
            total_raw += file_rows

            for _, row in df.iterrows():
                raw_email = str(row.get("Contact : Emails", "")).strip().lower()
                if not raw_email or "@" not in raw_email or raw_email == "nan":
                    continue

                candidates = [
                    e.strip() for e in raw_email.replace(";", ",").split(",") if "@" in e.strip()
                ]
                if not candidates:
                    candidates = [raw_email]

                raw_fname = str(row.get("Contact : First name", "")).strip() if pd.notna(row.get("Contact : First name")) else ""
                raw_lname = str(row.get("Contact : Last name", "")).strip() if pd.notna(row.get("Contact : Last name")) else ""
                if raw_fname.lower() == "nan":
                    raw_fname = ""
                if raw_lname.lower() == "nan":
                    raw_lname = ""

                fullname = f"{raw_fname} {raw_lname}".strip() or "Manager"
                comp_val = row.get("Contact : Accounts") if pd.notna(row.get("Contact : Accounts")) else (row.get("Contact : Account Name") if pd.notna(row.get("Contact : Account Name")) else "")
                comp_name = str(comp_val).strip() if comp_val else ""
                if comp_name.lower() == "nan":
                    comp_name = ""

                for em in candidates:
                    if em in seen_in_batch:
                        continue
                    seen_in_batch.add(em)

                    dom_part = em.split("@")[-1]
                    domain = extract_root_domain(dom_part)

                    records.append((
                        _s(em, 250),
                        _s(fullname, 250),
                        _s(domain, 250),
                        _s(comp_name, 250) if comp_name else None
                    ))

            print(f"  ✓ Processed {fname} ({file_rows} rows)")
        except Exception as ex:
            print(f"  ✗ Error reading {fname}: {ex}")

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Total Raw Rows: {total_raw}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Total Unique Valid Emails in Files: {len(records)}")

    if not records:
        print("[!] No valid email records found.")
        return

    chunk_size = 5000
    inserted_count = 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(records), chunk_size):
                chunk = records[i:i + chunk_size]
                sql = """
                    INSERT IGNORE INTO `emails` (`email`, `full_name`, `domain`, `company_name`)
                    VALUES (%s, %s, %s, %s)
                """
                cur.executemany(sql, chunk)
                affected = cur.rowcount
                inserted_count += affected
                batch_num = i // chunk_size + 1
                total_batches = (len(records) - 1) // chunk_size + 1
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Batch {batch_num}/{total_batches}: processed {len(chunk)} (new rows inserted: {affected})")
            conn.commit()

    print("\n" + "=" * 80)
    print("IMPORT SUMMARY")
    print(f"Total Unique Emails in Files: {len(records)}")
    print(f"Net-New Records Inserted:     {inserted_count}")
    print(f"Already Existed in Database:  {len(records) - inserted_count}")
    print("=" * 80)


def main():
    default_dir = r"G:\My Drive\exported files\september 2026"
    parser = argparse.ArgumentParser(description="Import September export files into MySQL emails table.")
    parser.add_argument("--dir", type=str, default=default_dir, help="Directory containing export files")
    args = parser.parse_args()
    import_september_emails(args.dir)


if __name__ == "__main__":
    main()
