import os
import sys
import glob
import pandas as pd
from datetime import datetime

sys.path.append(os.getcwd())
from backend.api import get_connection, extract_root_domain, _s

def main():
    folder = r"c:\Users\test\Desktop\projects\apollo_extension\august 2026"
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower() != "desktop.ini"]
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting import of {len(files)} files from '{folder}'...")
    
    records = []
    seen_emails = set()
    total_raw = 0
    
    for f in files:
        fname = os.path.basename(f)
        try:
            try:
                df = pd.read_excel(f)
            except Exception:
                df = pd.read_csv(f)
            
            file_rows = len(df)
            total_raw += file_rows
            
            for _, row in df.iterrows():
                raw_email = str(row.get("Contact : Emails", "")).strip().lower()
                if not raw_email or "@" not in raw_email or raw_email == "nan":
                    continue
                
                # In case multiple emails separated by comma/semicolon/newline
                email_candidates = [e.strip() for e in raw_email.replace(";", ",").split(",") if "@" in e.strip()]
                if not email_candidates:
                    email_candidates = [raw_email]
                
                raw_fname = str(row.get("Contact : First name", "")).strip() if pd.notna(row.get("Contact : First name")) else ""
                raw_lname = str(row.get("Contact : Last name", "")).strip() if pd.notna(row.get("Contact : Last name")) else ""
                if raw_fname.lower() == "nan":
                    raw_fname = ""
                if raw_lname.lower() == "nan":
                    raw_lname = ""
                
                fullname = f"{raw_fname} {raw_lname}".strip() or "Manager"
                
                for email in email_candidates:
                    if email in seen_emails:
                        continue
                    seen_emails.add(email)
                    
                    domain_part = email.split("@")[-1]
                    domain = extract_root_domain(domain_part)
                    
                    records.append((
                        _s(email, 250),
                        _s(fullname, 250),
                        _s(domain, 250)
                    ))
            print(f"  Processed {fname} ({file_rows} rows)")
        except Exception as e:
            print(f"  Error reading {fname}: {e}")
            
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Total raw rows: {total_raw}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Total unique valid emails extracted: {len(records)}")
    
    # Insert in chunks
    chunk_size = 5000
    inserted_count = 0
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(records), chunk_size):
                chunk = records[i:i + chunk_size]
                sql = "INSERT IGNORE INTO emails (email, full_name, domain) VALUES (%s, %s, %s)"
                cur.executemany(sql, chunk)
                affected = cur.rowcount
                inserted_count += affected
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Batch {i//chunk_size + 1}/{(len(records)-1)//chunk_size + 1}: processed {len(chunk)} (new rows inserted: {affected})")
            
            conn.commit()
            
    print(f"\n==========================================")
    print(f"IMPORT COMPLETE")
    print(f"Total Raw Contacts: {total_raw}")
    print(f"Total Unique Valid Emails: {len(records)}")
    print(f"Total Net-New Records Inserted into DB: {inserted_count}")
    print(f"Already Existed in DB (Ignored): {len(records) - inserted_count}")
    print(f"==========================================")

if __name__ == "__main__":
    main()
