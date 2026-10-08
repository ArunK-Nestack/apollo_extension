from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def _detect_dialect(path: Path) -> type[csv.Dialect]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def normalize_email(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().lower()


def generate_run_tag(owner: str | None) -> str:
    """
    Generates tag: <owner_name>-<Month>-<Year> (e.g. vijay_raghavan-Aug-2026)
    """
    month_year = datetime.now().strftime("%b-%Y")
    if owner and str(owner).strip():
        raw_owner = str(owner).strip()
        if "@" in raw_owner:
            owner_name = raw_owner.split("@")[0]
        else:
            owner_name = raw_owner
        clean_owner = re.sub(r"[^\w\-]", "_", owner_name)
        clean_owner = re.sub(r"_+", "_", clean_owner).strip("_")
        return f"{clean_owner}-{month_year}"
    return f"unassigned-{month_year}"


@dataclass
class ParsedFile:
    source_path: Path
    file_stem: str
    tag_applied: str
    apollo_login_owner: str | None
    total_raw_rows: int
    duplicates_dropped: int
    unique_records: list[dict[str, str]]
    email_column: str


def _read_excel_file(file_path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Reads an Excel file (.xlsx / .xls) and returns (fieldnames, raw_rows).
    """
    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        try:
            sheet = wb.active
            if sheet is None:
                raise ValueError(f"No active sheet found in Excel file: {file_path.name}")

            row_iter = sheet.iter_rows(values_only=True)
            try:
                header_row = next(row_iter)
            except StopIteration:
                raise ValueError(f"Excel file is empty: {file_path.name}")

            fieldnames = [str(cell).strip() if cell is not None else "" for cell in header_row]
            raw_rows: list[dict[str, Any]] = []

            for row_vals in row_iter:
                if not any(v is not None and str(v).strip() != "" for v in row_vals):
                    continue
                row_dict: dict[str, Any] = {}
                for idx, col_name in enumerate(fieldnames):
                    if not col_name:
                        continue
                    val = row_vals[idx] if idx < len(row_vals) else None
                    if val is None:
                        row_dict[col_name] = ""
                    else:
                        row_dict[col_name] = str(val).strip()
                raw_rows.append(row_dict)

            clean_fieldnames = [f for f in fieldnames if f]
            return clean_fieldnames, raw_rows
        finally:
            wb.close()
    except Exception as exc:
        # Fallback to pandas if installed
        try:
            import pandas as pd

            df = pd.read_excel(file_path, dtype=str)
            df = df.fillna("")
            clean_fieldnames = [str(c).strip() for c in df.columns if str(c).strip()]
            raw_rows = [
                {str(k).strip(): str(v).strip() for k, v in row.items() if str(k).strip()}
                for row in df.to_dict(orient="records")
            ]
            return clean_fieldnames, raw_rows
        except Exception:
            raise ValueError(f"Failed to read Excel file {file_path.name}: {exc}") from exc


def _read_csv_file(file_path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Reads a CSV file and returns (fieldnames, raw_rows).
    """
    dialect = _detect_dialect(file_path)
    with file_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError(f"No headers found in CSV: {file_path.name}")
        raw_rows = list(reader)
        fieldnames = [str(f).strip() for f in (reader.fieldnames or []) if f is not None]
        return fieldnames, raw_rows


def parse_and_dedupe_file(file_path: Path) -> ParsedFile:
    """
    Reads a CSV or Excel (.xlsx/.xls) file, detects email column, extracts Contact Owner,
    deduplicates emails within the file, and returns clean records.
    """
    if not file_path.is_file():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix in (".xlsx", ".xls", ".xlsm", ".xltx", ".xltm"):
        fieldnames, raw_rows = _read_excel_file(file_path)
    else:
        fieldnames, raw_rows = _read_csv_file(file_path)

    total_raw_rows = len(raw_rows)

    # Detect email column
    email_col: str | None = None
    for f in fieldnames:
        if f.strip().lower() in ("email", "contact: emails", "email address", "contact: emails (primary)"):
            email_col = f
            break

    if not email_col:
        # Fallback to first column containing '@' in sample
        for f in fieldnames:
            if any("@" in str(r.get(f, "")) for r in raw_rows[:20]):
                email_col = f
                break

    if not email_col:
        raise ValueError(f"Could not find an email column in file: {file_path.name}")

    # Extract Apollo Login Owner from Contact Owner column
    apollo_owner: str | None = None
    for row in raw_rows:
        for k, v in row.items():
            if k and str(k).strip().lower() in ("contact owner", "contact: apollo login owner", "apollo login owner"):
                if v and str(v).strip():
                    apollo_owner = str(v).strip()
                    break
        if apollo_owner:
            break

    # Deduplicate within file
    seen_emails: set[str] = set()
    unique_records: list[dict[str, str]] = []
    duplicates_dropped = 0

    for row in raw_rows:
        clean_row = {
            (str(k).strip() if k is not None else ""): (str(v) if v is not None else "")
            for k, v in row.items()
        }
        em = normalize_email(clean_row.get(email_col))
        if not em:
            continue

        if em in seen_emails:
            duplicates_dropped += 1
            continue

        seen_emails.add(em)
        clean_row[email_col] = em
        unique_records.append(clean_row)

    tag = generate_run_tag(apollo_owner)

    return ParsedFile(
        source_path=file_path,
        file_stem=file_path.stem,
        tag_applied=tag,
        apollo_login_owner=apollo_owner,
        total_raw_rows=total_raw_rows,
        duplicates_dropped=duplicates_dropped,
        unique_records=unique_records,
        email_column=email_col,
    )

