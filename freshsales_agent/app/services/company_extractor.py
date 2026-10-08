from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database.company_model import FreshsalesCompany
from app.database.db import engine, get_db

CHECKPOINT_FILE = settings.data_dir / "company_extraction_checkpoint.json"


def extract_domain(website_or_url: str | None) -> str | None:
    """
    Extracts a clean hostname/domain from a website string or URL.
    Examples:
      'https://www.example.com/about' -> 'example.com'
      'http://sub.domain.org:8080/'   -> 'sub.domain.org'
      'company.io'                   -> 'company.io'
    """
    if not website_or_url or not isinstance(website_or_url, str):
        return None

    cleaned = website_or_url.strip().lower()
    if not cleaned:
        return None

    try:
        # Prepend http:// if missing scheme for urlparse
        if not cleaned.startswith(("http://", "https://")):
            parsed = urlparse(f"http://{cleaned}")
        else:
            parsed = urlparse(cleaned)

        domain = parsed.hostname or parsed.path.split("/")[0]
        if domain:
            domain = domain.strip().lower()
            if domain.startswith("www."):
                domain = domain[4:]
            # Remove trailing port if any
            domain = domain.split(":")[0].strip()
            # Basic validation: must contain at least one dot
            if "." in domain and not domain.endswith("."):
                return domain
    except Exception:
        pass

    return None


def resolve_company_name(acc: dict[str, Any], strict_column_only: bool = False) -> str | None:
    """
    Extracts the genuine company name from a Freshsales sales account.
    The primary column where company names are stored in Freshsales is 'cf_company_name' (labeled 'Company Name').

    - If strict_column_only is True: extracts ONLY from this dedicated 'Company Name' column (cf_company_name).
    - If strict_column_only is False: prioritizes 'cf_company_name'; falls back to 'name' only if 'name'
      is an authentic human name (rejecting domains, URLs, and paths).
    """
    cf = acc.get("custom_field") or {}
    cf_name = str(cf.get("cf_company_name") or "").strip()
    if cf_name:
        lower_cf = cf_name.lower()
        if not lower_cf.startswith(("http://", "https://", "www.")) and not re.search(r"\.[a-z]{2,}(\/|$)", lower_cf):
            return cf_name

    if strict_column_only:
        return None

    acc_name = str(acc.get("name") or "").strip()
    if not acc_name:
        return None

    website = str(acc.get("website") or acc.get("company_website") or "").strip()
    domain = extract_domain(website or acc_name)

    lower_acc = acc_name.lower()
    if lower_acc.startswith(("http://", "https://", "www.")):
        return None
    if "/" in acc_name or re.search(r"\.[a-z]{2,}(\/|$)", lower_acc):
        return None
    if domain and lower_acc == domain.lower():
        return None

    return acc_name


def save_checkpoint(
    last_fetched_id: str | int | None,
    last_page: int,
    total_extracted: int,
    view_id: str | int | None = None,
) -> None:
    """Saves extraction progress checkpoint to disk atomically."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_fetched_id": str(last_fetched_id) if last_fetched_id is not None else None,
        "last_page": last_page,
        "total_extracted": total_extracted,
        "view_id": view_id,
        "updated_at": datetime.utcnow().isoformat(),
    }
    tmp_file = CHECKPOINT_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_file, CHECKPOINT_FILE)


def load_checkpoint() -> dict[str, Any] | None:
    """Loads existing extraction checkpoint if present."""
    if not CHECKPOINT_FILE.is_file():
        return None
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_checkpoint() -> None:
    """Removes checkpoint file."""
    if CHECKPOINT_FILE.is_file():
        try:
            CHECKPOINT_FILE.unlink()
        except OSError:
            pass


def save_companies_batch(records: list[dict[str, Any]]) -> int:
    """
    Persists a batch of company records to the database with upsert / de-duplication.
    Works seamlessly on both MySQL and SQLite.
    Returns the number of records processed.
    """
    if not records:
        return 0

    dialect_name = engine.dialect.name
    now = datetime.utcnow()

    # Normalize records
    normalized: list[dict[str, Any]] = []
    for r in records:
        account_id = str(r.get("account_id") or "").strip()
        company_name = str(r.get("company_name") or "").strip()
        website = str(r.get("website") or "").strip() or None
        domain = str(r.get("domain") or "").strip() or extract_domain(website)

        if not account_id or not company_name:
            continue

        normalized.append({
            "account_id": account_id,
            "company_name": company_name[:255],
            "domain": domain[:255] if domain else None,
            "website": website,
            "created_at": now,
            "updated_at": now,
        })

    if not normalized:
        return 0

    db: Session = get_db()
    try:
        if dialect_name == "mysql":
            from sqlalchemy.dialects.mysql import insert as mysql_insert

            stmt = mysql_insert(FreshsalesCompany).values(normalized)
            upsert_stmt = stmt.on_duplicate_key_update(
                company_name=stmt.inserted.company_name,
                domain=stmt.inserted.domain,
                website=stmt.inserted.website,
                updated_at=stmt.inserted.updated_at,
            )
            db.execute(upsert_stmt)
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(FreshsalesCompany).values(normalized)
            upsert_stmt = stmt.on_conflict_do_update(
                index_elements=["account_id"],
                set_={
                    "company_name": stmt.excluded.company_name,
                    "domain": stmt.excluded.domain,
                    "website": stmt.excluded.website,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            db.execute(upsert_stmt)
        else:
            # Fallback for PostgreSQL / other dialects
            for item in normalized:
                existing = (
                    db.query(FreshsalesCompany)
                    .filter(FreshsalesCompany.account_id == item["account_id"])
                    .first()
                )
                if existing:
                    existing.company_name = item["company_name"]
                    existing.domain = item["domain"]
                    existing.website = item["website"]
                    existing.updated_at = item["updated_at"]
                else:
                    db.add(FreshsalesCompany(**item))

        db.commit()
        return len(normalized)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
