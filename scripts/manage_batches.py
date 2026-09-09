#!/usr/bin/env python3
"""
Apollo Saved Leads & CRM Database Manager CLI
=============================================
1. Displays existing batches in `apollo_saved_leads` with lead counts and domains.
2. Provides option to delete an entire batch from `apollo_saved_leads`.
3. Provides option to upload/import CSV or Excel file directly into master CRM (`emails` table),
   automatically extracting only the required columns: email, full_name, and domain.
"""

import os
import sys
import re
import csv
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from backend.api import get_connection, extract_root_domain, _s, get_seniority_score, get_target_table_schema
from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row
from scripts.lead_guardrails import apply_4_layer_guardrails

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "domain_slugs_cache.txt")


def get_crm_emails_count(conn):
    """Return total records in master CRM emails table."""
    try:
        schema = get_target_table_schema(conn)
        tbl = schema.get("table_name", "emails")
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{tbl}`")
            row = cur.fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


def fetch_batches(conn):
    """Fetch all distinct batches with lead count, distinct domains, and timestamps."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 
                batch,
                COUNT(*) as total_leads,
                COUNT(DISTINCT company_domain) as distinct_domains,
                MIN(created_at) as first_added,
                MAX(created_at) as last_added
            FROM apollo_saved_leads
            GROUP BY batch
            ORDER BY MAX(created_at) DESC;
        """)
        rows = cur.fetchall()
        
    batches = []
    for r in rows:
        batches.append({
            "batch": str(r[0] or "unnamed"),
            "total_leads": int(r[1] or 0),
            "distinct_domains": int(r[2] or 0),
            "first_added": str(r[3])[:16] if r[3] else "N/A",
            "last_added": str(r[4])[:16] if r[4] else "N/A"
        })
    return batches


def display_batch_overview(batches, emails_count):
    """Print clean ASCII table of all current batches."""
    print("\n" + "=" * 92)
    print("                      APOLLO SAVED LEADS - CURRENT BATCHES")
    print("=" * 92)
    print(f" {'#':<3} | {'Batch Name':<38} | {'Leads':<8} | {'Domains':<8} | {'First Added':<14} | {'Last Added':<14}")
    print("-" * 92)

    total_leads_all = 0
    total_domains_approx = 0

    if not batches:
        print("  [No batches found in apollo_saved_leads table]")
    else:
        for idx, b in enumerate(batches, 1):
            total_leads_all += b["total_leads"]
            total_domains_approx += b["distinct_domains"]
            name_display = b["batch"]
            if len(name_display) > 38:
                name_display = name_display[:35] + "..."
            print(f" {idx:<3} | {name_display:<38} | {b['total_leads']:<8,d} | {b['distinct_domains']:<8,d} | {b['first_added']:<14} | {b['last_added']:<14}")

    print("=" * 92)
    print(f" Total Saved Leads: {total_leads_all:,d} across {len(batches)} batches | Master CRM `emails` Total: {emails_count:,d} records")
    print("=" * 92 + "\n")


def delete_batch_action(batches, conn):
    """Prompt user to select a batch and delete its records from apollo_saved_leads."""
    if not batches:
        print("No batches available to delete.")
        return

    print("\n--- DELETE BATCH FROM `apollo_saved_leads` ---")
    user_input = input("Enter the batch NUMBER (1 to %d) or exact BATCH NAME to delete (or press Enter to cancel): " % len(batches)).strip()
    
    if not user_input:
        print("Deletion canceled.")
        return

    selected_batch = None
    if user_input.isdigit():
        idx = int(user_input) - 1
        if 0 <= idx < len(batches):
            selected_batch = batches[idx]
        else:
            print("Invalid batch number.")
            return
    else:
        for b in batches:
            if b["batch"].lower() == user_input.lower():
                selected_batch = b
                break

    if not selected_batch:
        print(f"Batch '{user_input}' not found.")
        return

    batch_name = selected_batch["batch"]
    lead_count = selected_batch["total_leads"]
    domain_count = selected_batch["distinct_domains"]

    print("\n" + "!" * 65)
    print(f" WARNING: You are about to permanently delete batch:")
    print(f"   Batch:            {batch_name}")
    print(f"   Total Leads:      {lead_count:,d}")
    print(f"   Distinct Domains: {domain_count:,d}")
    print("!" * 65)

    confirm = input(f"Type 'yes' to confirm permanent deletion of batch '{batch_name}': ").strip().lower()
    if confirm != "yes":
        print("Deletion aborted. No changes made.")
        return

    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM apollo_saved_leads WHERE batch = %s", (batch_name,))
            deleted_rows = cur.rowcount
            conn.commit()
        print(f"\n[SUCCESS] Successfully deleted {deleted_rows:,d} leads from batch '{batch_name}'.")

        # Invalidate running backend server in-memory election cache
        try:
            import urllib.request
            import urllib.parse
            req = urllib.request.Request(f"http://127.0.0.1:8000/invalidate-batch-cache?batch={urllib.parse.quote(batch_name)}", method="POST")
            urllib.request.urlopen(req, timeout=0.5)
        except Exception:
            pass
    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Failed to delete batch: {e}")


def _find_column(columns, candidates):
    """Match column names case-insensitively with flexible substrings."""
    col_map = {str(c).strip().lower(): c for c in columns}
    # 1. Exact match
    for cand in candidates:
        cand_lower = cand.strip().lower()
        if cand_lower in col_map:
            return col_map[cand_lower]
    # 2. Substring match
    for cand in candidates:
        cand_lower = cand.strip().lower()
        for col_lower, original_col in col_map.items():
            if cand_lower in col_lower:
                return original_col
    return None


def add_file_to_emails_action(conn):
    """Prompt user for CSV or Excel file, extract required columns, and insert into emails table."""
    print("\n--- ADD / UPLOAD DATA TO MASTER CRM (`emails` TABLE) ---")
    raw_path = input("Enter path to CSV or Excel file (or drag-and-drop file here): ").strip()
    
    # Strip surrounding quotes if dragged from Windows Explorer
    clean_path = raw_path.strip('\'"').strip()
    if not clean_path:
        print("Upload canceled.")
        return

    if not os.path.exists(clean_path):
        print(f"[ERROR] File not found: '{clean_path}'")
        return

    fname = os.path.basename(clean_path)
    ext = os.path.splitext(clean_path)[1].lower()
    print(f"\nScanning file: {fname}...")

    # Read data safely with or without pandas
    source_rows = []
    cols = []
    try:
        if ext in [".xlsx", ".xls"]:
            if not PANDAS_AVAILABLE:
                print("[ERROR] pandas and openpyxl are required to read Excel files. Run: pip install pandas openpyxl")
                return
            df = pd.read_excel(clean_path)
            cols = list(df.columns)
            source_rows = df.to_dict(orient="records")
        else:
            if PANDAS_AVAILABLE:
                try:
                    df = pd.read_csv(clean_path, low_memory=False, encoding="utf-8")
                except UnicodeDecodeError:
                    df = pd.read_csv(clean_path, low_memory=False, encoding="latin1")
                cols = list(df.columns)
                source_rows = df.to_dict(orient="records")
            else:
                # Fallback to standard csv module
                with open(clean_path, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    cols = list(reader.fieldnames or [])
                    source_rows = list(reader)
    except Exception as e:
        print(f"[ERROR] Failed to read file: {e}")
        return

    total_rows = len(source_rows)
    if total_rows == 0:
        print("[ERROR] The selected file is empty.")
        return

    print(f"Found {total_rows:,d} rows and {len(cols)} columns.")

    # 1. Identify Email Column
    email_col = _find_column(cols, [
        "Contact : Emails", "contact : emails", "Emails", "emails",
        "Email", "email", "Email Address", "email address", "Work Email", "work email"
    ])
    if not email_col:
        print("[ERROR] Could not find an Email column. Detected columns:")
        print(" ", cols)
        return

    # 2. Identify Name Columns
    fn_col = _find_column(cols, ["Contact : First name", "First Name", "first_name", "firstname", "first"])
    ln_col = _find_column(cols, ["Contact : Last name", "Last Name", "last_name", "lastname", "last"])
    full_name_col = _find_column(cols, ["Contact : Full name", "Full Name", "full_name", "fullname", "Name", "name"])

    # 3. Identify Domain Column (optional, fallback to email domain)
    domain_col = _find_column(cols, [
        "Domain", "domain", "Company Domain", "company_domain", "Website", "website", "Website Domain"
    ])

    print("\n[Column Mapping Detected]:")
    print(f"  • Email column:     '{email_col}'")
    if fn_col and ln_col:
        print(f"  • First Name col:   '{fn_col}'")
        print(f"  • Last Name col:    '{ln_col}'")
    elif full_name_col:
        print(f"  • Full Name col:    '{full_name_col}'")
    else:
        print("  • Name columns:     Not found (will default to 'Manager')")
        
    if domain_col:
        print(f"  • Domain column:    '{domain_col}'")
    else:
        print("  • Domain column:    Will extract root domain directly from Email (@domain)")

    def _cell(row_dict, col_name):
        if not col_name:
            return ""
        val = row_dict.get(col_name, "")
        if val is None:
            return ""
        if PANDAS_AVAILABLE and hasattr(val, "__iter__") is False and pd.isna(val):
            return ""
        s = str(val).strip()
        return "" if s.lower() == "nan" else s

    # Process and extract only the 3 required fields
    print("\nExtracting and normalizing required records...")
    records = []
    seen_emails = set()
    new_domains = set()

    for row in source_rows:
        raw_email = _cell(row, email_col).lower()
        if not raw_email or "@" not in raw_email:
            continue

        # Handle multiple emails separated by comma or semicolon
        email_candidates = [e.strip() for e in raw_email.replace(";", ",").split(",") if "@" in e.strip()]
        if not email_candidates:
            continue

        # Extract full name
        raw_fn = _cell(row, fn_col)
        raw_ln = _cell(row, ln_col)
        raw_full = _cell(row, full_name_col)

        if raw_fn or raw_ln:
            full_name = f"{raw_fn} {raw_ln}".strip()
        elif raw_full:
            full_name = raw_full
        else:
            full_name = "Manager"

        if not full_name:
            full_name = "Manager"

        # Extract domain
        for em in email_candidates:
            if em in seen_emails:
                continue
            seen_emails.add(em)

            raw_dom = _cell(row, domain_col).lower()
            if not raw_dom:
                raw_dom = em.split("@")[-1]

            clean_dom = extract_root_domain(raw_dom)
            if clean_dom:
                new_domains.add(clean_dom)

            records.append((
                _s(em, 250),
                _s(full_name, 250),
                _s(clean_dom, 250)
            ))

    print(f"Extracted {len(records):,d} valid unique email records from {total_rows:,d} source rows.")
    if not records:
        print("[NOTICE] No valid email rows found to insert.")
        return

    # Show preview
    print("\n--- PREVIEW OF EXTRACTED ROWS (First 3) ---")
    for i, (em, fn, dom) in enumerate(records[:3], 1):
        print(f" {i}. Email: {em:<35} | Name: {fn:<22} | Domain: {dom}")

    confirm = input(f"\nInsert {len(records):,d} records into master CRM `emails` table? (y/n): ").strip().lower()
    if confirm not in ["y", "yes"]:
        print("Upload canceled. No database changes made.")
        return

    # Insert in chunks of 5000 using INSERT IGNORE
    chunk_size = 5000
    total_inserted = 0
    t0 = datetime.now()

    print("\nInserting records into `emails` table...")
    try:
        schema = get_target_table_schema(conn)
        tbl = schema["table_name"]
        em_col = schema["email"]
        nm_col = schema["name"] or "full_name"
        dm_col = schema["email_domain"]
        sql = f"INSERT IGNORE INTO `{tbl}` (`{em_col}`, `{nm_col}`, `{dm_col}`) VALUES (%s, %s, %s)"

        with conn.cursor() as cur:
            for i in range(0, len(records), chunk_size):
                chunk = records[i:i + chunk_size]
                cur.executemany(sql, chunk)
                total_inserted += cur.rowcount
            conn.commit()

        dur = (datetime.now() - t0).total_seconds()
        duplicates_skipped = len(records) - total_inserted
        print(f"[SUCCESS] Insertion Complete in {dur:.2f}s!")
        print(f"  • Net-new records inserted: {total_inserted:,d}")
        print(f"  • Existing duplicates skipped: {duplicates_skipped:,d}")

        # Update disk cache with any new unique domains
        if new_domains and os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "a", encoding="utf-8") as f:
                    for d in sorted(new_domains):
                        f.write(f"{d}\n")
                print(f"  • Appended {len(new_domains):,d} domains to deduplication cache file.")

                # Recompile MARISA-Trie binary
                try:
                    from scripts.build_marisa_trie import build_marisa_trie
                    build_marisa_trie()
                except Exception:
                    pass
            except Exception as ex_cache:
                print(f"  • Notice: Cache file update error: {ex_cache}")


    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Database insertion failed: {e}")


def export_batch_action(batches, conn):
    """Prompt user to select a batch and export clean/unique leads to CSV."""
    if not batches:
        print("No batches available to export.")
        return

    print("\n--- EXPORT BATCH TO CSV ---")
    user_input = input("Enter the batch NUMBER (1 to %d) or exact BATCH NAME to export (or press Enter to cancel): " % len(batches)).strip()
    
    if not user_input:
        print("Export canceled.")
        return

    selected_batch = None
    if user_input.isdigit():
        idx = int(user_input) - 1
        if 0 <= idx < len(batches):
            selected_batch = batches[idx]
        else:
            print("Invalid batch number.")
            return
    else:
        for b in batches:
            if b["batch"].lower() == user_input.lower():
                selected_batch = b
                break

    if not selected_batch:
        print(f"Batch '{user_input}' not found.")
        return

    batch_name = selected_batch["batch"]
    print(f"\nSelected Batch: '{batch_name}' ({selected_batch['total_leads']:,d} total leads)")
    print("Export Options:")
    print("  [1] Apollo Official 75-Column Format (4-Layer Guardrails: Strictly Verified Net-New - Recommended)")
    print("  [2] Apollo Official 75-Column Format (All Raw Leads in Batch, No Deduplication)")
    print("  [3] Legacy Core Columns (15 columns, Unique by Domain)")
    
    mode_choice = input("Select mode [1/2/3, default: 1]: ").strip()
    if not mode_choice:
        mode_choice = "1"

    use_4_layer_guardrails = (mode_choice == "1")
    use_75_col_format = (mode_choice in ("1", "2"))
    dedup_domains = (mode_choice in ("1", "3"))

    print("\nQuerying leads from database...")
    try:
        with conn.cursor() as cur:
            if use_75_col_format:
                cur.execute("""
                    SELECT 
                        id, batch, apollo_id, name, first_name, last_name, job_title, email, email_status,
                        company, company_domain, website_link, annual_revenue, employee_count,
                        industry, tech_stack, keywords, company_phone, hq_address, location, linkedin_url,
                        company_linkedin_url, apollo_profile_url, segment, account_used, credits_charged,
                        raw_enrichment_data, enriched_at, created_at
                    FROM apollo_saved_leads
                    WHERE batch = %s
                    ORDER BY id ASC;
                """, (batch_name,))
                cols = [c[0] for c in cur.description]
                raw_rows = cur.fetchall()
                dict_rows = [dict(zip(cols, r)) for r in raw_rows]
            else:
                cur.execute("""
                    SELECT id, batch, apollo_id, name, first_name, last_name, job_title,
                           company, company_domain, website_link, location, linkedin_url,
                           apollo_profile_url, segment, created_at
                    FROM apollo_saved_leads
                    WHERE batch = %s
                    ORDER BY id ASC;
                """, (batch_name,))
                raw_rows = cur.fetchall()
                dict_rows = []

        if not raw_rows:
            print(f"[NOTICE] No records found for batch '{batch_name}'.")
            return

        if use_4_layer_guardrails:
            # Apply all 4 layers: Intra-batch, CRM exact, Person LCS, MARISA Trie, DNS MX
            final_leads, metrics = apply_4_layer_guardrails(dict_rows, batch_name, conn, verbose=True)
            export_rows = [format_apollo_lead_row(lead) for lead in final_leads]
            headers = APOLLO_75_HEADERS
            dedup_count = len(dict_rows) - len(final_leads)
        elif use_75_col_format:
            # All raw leads, 75-column format
            export_rows = [format_apollo_lead_row(lead) for lead in dict_rows]
            headers = APOLLO_75_HEADERS
            dedup_count = 0
        else:
            headers = [
                "id", "batch", "apollo_id", "name", "first_name", "last_name", "job_title",
                "company", "company_domain", "website_link", "location", "linkedin_url",
                "apollo_profile_url", "segment", "created_at"
            ]
            if dedup_domains:
                seen_keys = {}
                for r in raw_rows:
                    dom = str(r[8] or "").strip().lower()
                    comp = str(r[7] or "").strip().lower()
                    key = f"dom:{dom}" if dom else (f"comp:{comp}" if comp else f"id:{r[0]}")
                    score = get_seniority_score(str(r[6] or "")) + (5 if r[11] else 0)
                    if key not in seen_keys or score > seen_keys[key][0]:
                        seen_keys[key] = (score, r)
                export_rows = [v[1] for v in seen_keys.values()]
                dedup_count = len(raw_rows) - len(export_rows)
            else:
                export_rows = raw_rows
                dedup_count = 0

        # Destination path
        os.makedirs("exports", exist_ok=True)
        suffix = "unique_leads" if dedup_domains else "all_leads"
        format_tag = "apollo_contacts" if use_75_col_format else "core"
        default_filename = os.path.join("exports", f"{batch_name}_{format_tag}_{suffix}.csv")
        
        custom_path = input(f"Enter output file path (or press Enter for default: '{default_filename}'): ").strip()
        out_path = custom_path.strip('\'"') if custom_path else default_filename

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for r in export_rows:
                writer.writerow(r)

        print(f"\n[SUCCESS] Exported {len(export_rows):,d} leads ({'75-Column Apollo Schema' if use_75_col_format else '15-Column Schema'}) to: {os.path.abspath(out_path)}")
        if dedup_domains and dedup_count > 0:
            print(f"  • Deduplicated {dedup_count:,d} redundant domain contacts.")

    except Exception as e:
        print(f"[ERROR] Export failed: {e}")


def main():
    while True:
        try:
            with get_connection() as conn:
                batches = fetch_batches(conn)
                emails_count = get_crm_emails_count(conn)
                display_batch_overview(batches, emails_count)

                print("AVAILABLE ACTIONS:")
                print("  [1] Delete a batch from Apollo Saved Leads")
                print("  [2] Add / Upload file data to master CRM (`emails` table)")
                print("  [3] Export a batch to CSV (Clean & Unique Leads)")
                print("  [4] Refresh batch statistics")
                print("  [5] Exit")
                
                choice = input("\nSelect an option (1-5): ").strip()

                if choice == "1":
                    delete_batch_action(batches, conn)
                    input("\nPress Enter to continue...")
                elif choice == "2":
                    add_file_to_emails_action(conn)
                    input("\nPress Enter to continue...")
                elif choice == "3":
                    export_batch_action(batches, conn)
                    input("\nPress Enter to continue...")
                elif choice == "4":
                    print("\nRefreshing batch statistics...")
                    continue
                elif choice in ["5", "q", "exit", "quit"]:
                    print("\nExiting. Goodbye!")
                    break
                else:
                    print("\n[Invalid choice. Please select 1, 2, 3, 4, or 5.]")
                    input("Press Enter to continue...")

        except KeyboardInterrupt:
            print("\n\nOperation interrupted by user. Exiting.")
            break
        except Exception as e:
            print(f"\n[Unexpected Error]: {e}")
            input("Press Enter to retry...")


if __name__ == "__main__":
    main()

