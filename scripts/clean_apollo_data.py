#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apollo Data Cleaning & Deduplication CLI
========================================
Applies standardized Apollo data cleaning pipeline:
1. Load Apollo CSV / Excel file (drag-and-drop friendly)
2. Clean and standardize column names
3. Ensure required columns exist (Email, Job Title, Owner, Last Name, First Name, Lists, # Employees, Industry)
4. Clean and validate email column
5. Assign hierarchy priority (Owner=1, Founder=2, President=3, CEO=4, COO=5, CFO/CTO=6, VP=7, Head=8, Director=9, Manager/Partner/Chief=10)
6. Deduplicate by Email keeping highest-priority title record
7. Fill blank Last Name with First Name
8. Create Account column from Email domain
9. Create Hierarchy column from Lists
10. Copy '# Employees' to 'Employee 2' and 'Industry' to 'Industry 2'
11. Empty Revenue cells if revenue has more than 11 digits
12. Save final cleaned CSV to Downloads folder
"""

import os
import sys
import re
import time
from pathlib import Path

# Force UTF-8 encoding on standard streams for Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import pandas as pd
except ImportError:
    print("[ERROR] pandas is required. Run 'pip install pandas' to install it.")
    sys.exit(1)


# Priority mapping (Lower number = higher priority)
PRIORITY_MAP = {
    "owner": 1,
    "founder": 2,
    "co-founder": 2,
    "co founder": 2,
    "president": 3,
    "ceo": 4,
    "coo": 5,
    "cfo": 6,
    "cto": 6,
    "vice president": 7,
    "vp": 7,
    "head": 8,
    "director": 9,
    "principal": 10,
    "manager": 10,
    "managing partner": 10,
    "partner": 10,
    "chief": 10,
}

REQUIRED_COLS = [
    "Email",
    "Job Title",
    "Owner",
    "Last Name",
    "First Name",
    "Lists",
    "# Employees",
    "Industry",
]


def get_priority(row):
    """Assign numerical priority based on Owner and Job Title."""
    job_title = str(row.get("Job Title", "")).lower().strip()
    if not job_title or job_title == "nan":
        # Fallback to Title if Job Title is empty
        job_title = str(row.get("Title", "")).lower().strip()

    owner = str(row.get("Owner", "")).lower().strip()

    # Highest priority if Owner column has a valid value
    if owner and owner != "nan":
        return 1

    # Check Job Title keywords in order of priority
    for keyword, rank in PRIORITY_MAP.items():
        if keyword in job_title:
            return rank

    # If nothing matches
    return 999


def load_data_file(file_path):
    """Load CSV or Excel file safely with multiple encoding fallbacks."""
    clean_path = str(file_path).strip('\'"').strip()
    if not os.path.exists(clean_path):
        raise FileNotFoundError(f"File not found: '{clean_path}'")

    ext = os.path.splitext(clean_path)[1].lower()
    if ext in [".xlsx", ".xls", ".xlsm"]:
        return pd.read_excel(clean_path), clean_path

    # Try common CSV encodings
    encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
    last_err = None
    for enc in encodings:
        try:
            df = pd.read_csv(clean_path, encoding=enc, low_memory=False)
            return df, clean_path
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception as e:
            raise e

    raise last_err or RuntimeError(f"Could not decode CSV file: {clean_path}")


def clean_apollo_dataframe(df):
    """
    Executes the 12-step Apollo cleaning pipeline on a pandas DataFrame.
    Returns (df_final, stats_dict).
    """
    initial_count = len(df)

    # 1. Clean column names (strip whitespace and BOM)
    df.columns = df.columns.astype(str).str.strip().str.replace("\ufeff", "", regex=False)

    # Remove duplicate columns if present (e.g. from BOM cleanup)
    df = df.loc[:, ~df.columns.duplicated()].copy()

    # Normalize common column names case-insensitively
    col_lookup = {c.lower(): c for c in df.columns}
    for req in REQUIRED_COLS:
        if req not in df.columns and req.lower() in col_lookup:
            df.rename(columns={col_lookup[req.lower()]: req}, inplace=True)

    # If Job Title is missing but Title exists, copy Title to Job Title
    if ("Job Title" not in df.columns or df["Job Title"].isna().all()) and "Title" in df.columns:
        df["Job Title"] = df["Title"]
    elif ("Job Title" not in df.columns or df["Job Title"].isna().all()) and "title" in col_lookup:
        df["Job Title"] = df[col_lookup["title"]]

    for col in REQUIRED_COLS:
        if col not in df.columns:
            df[col] = ""

    # 3. Clean Email column
    df["Email"] = df["Email"].astype(str).str.strip().str.lower()
    df_valid = df[
        df["Email"].notna()
        & (df["Email"] != "")
        & (df["Email"] != "nan")
        & (df["Email"] != "none")
    ].copy()
    valid_email_count = len(df_valid)

    # 4. Assign priority
    df_valid["priority"] = df_valid.apply(get_priority, axis=1)

    # 5. Sort by Email + Priority (ascending)
    df_sorted = df_valid.sort_values(
        by=["Email", "priority"],
        ascending=[True, True]
    )

    # 6. Deduplicate by Email (keep first = highest priority record)
    df_final = df_sorted.drop_duplicates(
        subset=["Email"],
        keep="first"
    ).copy()
    duplicates_removed = valid_email_count - len(df_final)

    # 7. Fill Last Name with First Name if Last Name is blank
    mask = (
        df_final["Last Name"].isna()
        | (df_final["Last Name"].astype(str).str.strip() == "")
        | (df_final["Last Name"].astype(str).str.lower().str.strip() == "nan")
    )
    last_name_filled = int(mask.sum())
    df_final.loc[mask, "Last Name"] = df_final.loc[mask, "First Name"]
    df_final.loc[mask, "First Name"] = ""

    # 8. Create Account column from Email domain
    df_final["Account"] = (
        df_final["Email"]
        .astype(str)
        .str.strip()
        .str.split("@")
        .str[-1]
    )

    # 9. Create Hierarchy column from Lists
    df_final["Hierarchy"] = (
        df_final["Lists"]
        .astype(str)
        .str.split("-")
        .str[0]
        .str.strip()
    )
    df_final.loc[df_final["Hierarchy"].astype(str).str.lower() == "nan", "Hierarchy"] = ""

    # 10. Copy Employee and Industry columns
    df_final["Employee 2"] = df_final["# Employees"]
    df_final["Industry 2"] = df_final["Industry"]

    # 11. Empty Revenue cells if revenue has more than 11 digits
    revenue_cols = [col for col in df_final.columns if "revenue" in col.lower()]
    revenue_cells_emptied = 0
    for col in revenue_cols:
        revenue_digit_count = (
            df_final[col]
            .astype(str)
            .str.replace(r"\D", "", regex=True)
            .str.len()
        )
        over_11 = revenue_digit_count > 11
        revenue_cells_emptied += int(over_11.sum())
        df_final.loc[over_11, col] = ""

    # 12. Drop helper priority column
    df_final.drop(columns=["priority"], inplace=True, errors="ignore")

    stats = {
        "initial_count": initial_count,
        "valid_email_count": valid_email_count,
        "invalid_emails_removed": initial_count - valid_email_count,
        "duplicates_removed": duplicates_removed,
        "final_count": len(df_final),
        "last_name_filled": last_name_filled,
        "revenue_cols": revenue_cols,
        "revenue_cells_emptied": revenue_cells_emptied,
    }
    return df_final, stats


def run_clean_apollo_pipeline(input_path, output_dir=None):
    """
    Loads file, runs cleaning, saves to Downloads folder, and returns summary stats.
    """
    df, clean_input_path = load_data_file(input_path)
    df_cleaned, stats = clean_apollo_dataframe(df)

    # Resolve output directory to user's Downloads folder
    if not output_dir:
        output_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(output_dir, exist_ok=True)

    base_name = Path(clean_input_path).stem
    out_filename = f"{base_name} - Cleaned_Deduplicated.csv"
    output_path = os.path.join(output_dir, out_filename)

    df_cleaned.to_csv(output_path, index=False, encoding="utf-8")
    stats["input_file"] = clean_input_path
    stats["output_file"] = output_path
    return df_cleaned, stats


def print_summary(stats):
    """Prints a clear summary report in ASCII format."""
    print("\n" + "=" * 80)
    print("                APOLLO DATA CLEANING & DEDUPLICATION REPORT")
    print("=" * 80)
    print(f"  • Source File:                 {stats['input_file']}")
    print(f"  • Initial Rows:                {stats['initial_count']:,d}")
    print(f"  • Valid Email Rows:            {stats['valid_email_count']:,d}")
    print(f"  • Blank/Invalid Emails Dropped:{stats['invalid_emails_removed']:,d}")
    print(f"  • Duplicates Removed (Email):  {stats['duplicates_removed']:,d}")
    print(f"  • Final Cleaned Records:       {stats['final_count']:,d}")
    print(f"  • Blank Last Names Replaced:   {stats['last_name_filled']:,d}")
    print(f"  • Revenue Columns Checked:     {', '.join(stats['revenue_cols']) if stats['revenue_cols'] else 'None'}")
    print(f"  • Revenue Cells Emptied (>11d):{stats['revenue_cells_emptied']:,d}")
    print(f"  • Output Saved To:             {stats['output_file']}")
    print("=" * 80)
    print("  [✓] New Columns Added:         ['Account', 'Hierarchy', 'Employee 2', 'Industry 2']")
    print("  [✓] Hierarchy Priority Applied (Owner > Founder > CEO > Director > Manager)")
    print("=" * 80 + "\n")


def select_file_via_dialog(title="Select Apollo CSV / Excel File", initialdir=None):
    """
    Opens native Windows File Explorer dialog to pick an Apollo CSV or Excel file.
    Tries Tkinter first; falls back to PowerShell OpenFileDialog.
    """
    if not initialdir:
        initialdir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(initialdir):
            initialdir = os.path.expanduser("~")

    # 1. Try Tkinter
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        file_path = filedialog.askopenfilename(
            title=title,
            initialdir=initialdir,
            filetypes=[
                ("Apollo CSV & Excel", "*.csv;*.xlsx;*.xls"),
                ("CSV Files (*.csv)", "*.csv"),
                ("Excel Files (*.xlsx;*.xls)", "*.xlsx;*.xls"),
                ("All Files (*.*)", "*.*"),
            ]
        )
        root.destroy()
        if file_path:
            return os.path.normpath(file_path)
    except Exception:
        pass

    # 2. Fallback to PowerShell System.Windows.Forms.OpenFileDialog
    try:
        import subprocess
        ps_code = f'''
[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms") | Out-Null
$f = New-Object System.Windows.Forms.OpenFileDialog
$f.Title = "{title}"
$f.InitialDirectory = "{initialdir.replace('\\', '\\\\')}"
$f.Filter = "Apollo Files (*.csv;*.xlsx;*.xls)|*.csv;*.xlsx;*.xls|All Files (*.*)|*.*"
$f.TopMost = $true
if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    Write-Output $f.FileName
}}
'''
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_code], capture_output=True, text=True, timeout=60)
        selected = res.stdout.strip()
        if selected and os.path.exists(selected):
            return os.path.normpath(selected)
    except Exception:
        pass

    return None


def clean_apollo_file_action():
    """Interactive prompt for CLI and Manage Batches integration."""
    print("\n" + "=" * 80)
    print("               APOLLO DATA CLEANER & DEDUPLICATOR")
    print("=" * 80)
    print("This tool standardizes columns, deduplicates by email based on hierarchy")
    print("priority (Owner > Founder > CEO > VP > Director > Manager), creates Account")
    print("& Hierarchy tags, fills missing last names, sanitizes revenue >11 digits,")
    print("and exports the clean file directly to your Downloads folder.\n")

    print("Choose input method:")
    print("  [1] Open Windows File Explorer (Browse & select file)  <-- [Default: press Enter]")
    print("  [2] Drag-and-drop or paste file path")

    user_input = input("\nEnter choice or paste/drag file path [Press Enter for File Explorer]: ").strip()
    clean_choice = user_input.strip('\'"').strip()

    clean_path = None
    if not clean_choice or clean_choice.lower() in ["1", "b", "browse", "o", "open", "f"]:
        print("\n[>>>] Opening Windows File Explorer... Please select your Apollo file.")
        clean_path = select_file_via_dialog(title="Select Apollo File (CSV / Excel)")
        if not clean_path:
            print("[!] File selection canceled or no file chosen.")
            return None
        print(f"[✓] Selected file: {clean_path}")
    elif clean_choice == "2":
        path_in = input("Drag and drop file here (or paste path): ").strip().strip('\'"').strip()
        if not path_in:
            print("[!] No file path provided.")
            return None
        clean_path = path_in
    else:
        # User directly pasted / dragged a path into the initial prompt
        clean_path = clean_choice

    if not os.path.exists(clean_path):
        print(f"[ERROR] File does not exist: '{clean_path}'")
        return None

    try:
        print(f"\nProcessing '{os.path.basename(clean_path)}'...")
        _, stats = run_clean_apollo_pipeline(clean_path)
        print_summary(stats)
        return stats
    except Exception as e:
        print(f"\n[ERROR] Failed to clean file: {e}")
        return None


def main():
    """Main CLI entrypoint."""
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        input_file = sys.argv[1]
        try:
            _, stats = run_clean_apollo_pipeline(input_file)
            print_summary(stats)
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
    else:
        clean_apollo_file_action()


if __name__ == "__main__":
    main()
