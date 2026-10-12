#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freshsales CRM Job Title Synchronizer
=====================================
Fetches job titles from Freshsales CRM and populates the MySQL `emails` table.

Capabilities:
  1. Fetch a specific number of job titles (e.g., 5,000 / custom limit).
  2. Fetch ALL missing job titles across all contacts.
  3. Fetch job titles for a particular account / domain (skipping contacts that already have a title).
  - Bulk operation: 1 single API call retrieves 100 contacts with job titles.
  - Safe rate limiter: ~4,000 to 4,500 requests/hour (~400,000+ contacts/hour).
  - Resumable: saves and loads checkpoint (`config/freshsales_title_sync_checkpoint.json`).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import requests
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load configurations
load_dotenv(PROJECT_ROOT / "freshsales_agent" / ".env")
load_dotenv(PROJECT_ROOT / ".env", override=False)

FS_API_KEY = os.getenv("FRESHSALES_API_KEY", "").strip()
FS_DOMAIN = os.getenv("FRESHSALES_DOMAIN", "https://nestack.freshsales.io").rstrip("/")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1").strip()
DB_USER = os.getenv("DB_USER", "root").strip()
DB_PASS = os.getenv("DB_PASSWORD", "").strip()
DB_NAME = os.getenv("DB_NAME", "apollo_scrapers").strip()
DB_PORT = int(os.getenv("DB_PORT", "3306"))

CHECKPOINT_FILE = PROJECT_ROOT / "config" / "freshsales_title_sync_checkpoint.json"
DEFAULT_ALL_CONTACTS_VIEW_ID = 12001258277


def get_db_connection() -> pymysql.Connection:
    """Creates a new MySQL database connection."""
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        port=DB_PORT,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def ensure_emails_job_title_column(conn: pymysql.Connection) -> None:
    """Verifies that the job_title column exists in the emails table."""
    with conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM emails")
        cols = {r["Field"] for r in cur.fetchall()}
        if "job_title" not in cols:
            print("[DB] Adding `job_title` column to `emails` table...")
            cur.execute("ALTER TABLE emails ADD COLUMN job_title VARCHAR(255) DEFAULT NULL")
            conn.commit()
            print("[DB] `job_title` column added successfully.")


class FreshsalesClient:
    """Client for Freshsales REST API with rate limiting and scroll support."""

    def __init__(self, api_key: str, domain: str, max_calls_per_hour: int = 4000):
        self.api_key = api_key
        self.base_url = domain.rstrip("/")
        self.max_calls_per_hour = max_calls_per_hour
        # 3600 seconds / 4000 calls = 0.90 seconds minimum spacing per call
        self.min_interval = 3600.0 / max_calls_per_hour
        self.last_call_time = 0.0
        self.hourly_call_count = 0
        self.hour_window_start = time.time()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token token={self.api_key}",
            "Content-Type": "application/json",
        })

    def _wait_rate_limit(self) -> None:
        now = time.time()
        # Reset hourly tracking window if 1 hour has elapsed
        if now - self.hour_window_start >= 3600:
            self.hour_window_start = now
            self.hourly_call_count = 0

        # Hard-cap: if 4,000 calls reached within current hour, sleep until next hour
        if self.hourly_call_count >= self.max_calls_per_hour:
            sleep_time = max(1.0, 3600.0 - (now - self.hour_window_start))
            print(f"\n [Rate Limiter] Reached 4,000 API calls for this hour. Sleeping {sleep_time:.1f}s until next hour window...")
            time.sleep(sleep_time)
            self.hour_window_start = time.time()
            self.hourly_call_count = 0

        # Even pacing: ensure 0.90s spacing between individual calls
        elapsed = time.time() - self.last_call_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        self.last_call_time = time.time()
        self.hourly_call_count += 1

    def request(self, method: str, path: str, **kwargs: Any) -> dict:
        self._wait_rate_limit()
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(4):
            try:
                resp = self.session.request(method, url, timeout=60, **kwargs)
                if resp.status_code == 429:
                    retry_sec = int(resp.headers.get("Retry-After", 60))
                    print(f" [Rate-Limit 429] Sleeping for {retry_sec}s...")
                    time.sleep(retry_sec)
                    continue
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except requests.RequestException as ex:
                if attempt == 3:
                    print(f" [API Error] {method} {url} failed: {ex}")
                    return {}
                time.sleep(2 ** attempt)
        return {}

    def fetch_contacts_scroll(self, view_id: int | str, last_fetched_id: Optional[int | str] = None, limit: int = 100) -> dict:
        """Fetches 100 contacts in a single API call using cursor scroll."""
        path = f"api/contacts/scroll/{view_id}"
        params: dict[str, Any] = {"limit": limit}
        if last_fetched_id is not None:
            params["last_fetched_id"] = last_fetched_id
        return self.request("GET", path, params=params)

    def search_contact_by_email(self, email: str) -> Optional[dict]:
        """Looks up a single contact by email via /api/search."""
        clean_email = email.strip().lower()
        res = self.request("GET", "api/search", params={"q": clean_email, "include": "contact"})
        if isinstance(res, list):
            for item in res:
                if item.get("type") == "contact" and str(item.get("email", "")).strip().lower() == clean_email:
                    cid = item.get("id")
                    if cid:
                        detail = self.request("GET", f"api/contacts/{cid}")
                        return detail.get("contact", detail)
                    return item
        return None


# ==============================================================================
# CHECKPOINT MANAGEMENT
# ==============================================================================

def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_fetched_id": None, "total_processed": 0, "total_updated": 0, "last_synced_at": None}


def save_checkpoint(data: dict) -> None:
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ==============================================================================
# CORE WORKERS
# ==============================================================================

def run_bulk_sync(client: FreshsalesClient, target_titles_count: Optional[int] = None) -> None:
    """
    Worker for Option 1 (Custom limit) and Option 2 (Select All).
    Streams 100 contacts per API call and updates matching emails.
    """
    checkpoint = load_checkpoint()
    last_id = checkpoint.get("last_fetched_id")
    total_processed = checkpoint.get("total_processed", 0)
    total_updated = checkpoint.get("total_updated", 0)

    print("\n" + "=" * 70)
    print(" >>> STARTING FRESHSALES BULK JOB TITLE SYNCHRONIZATION")
    print(f" Target Limit       : {'ALL' if target_titles_count is None else f'{target_titles_count:,}'}")
    print(f" Resuming From ID   : {last_id or 'Beginning (Page 1)'}")
    print(f" Prior Processed    : {total_processed:,} contacts")
    print(f" Prior Titles Saved : {total_updated:,} titles")
    print(f" Speed & Efficiency : 100 contacts per single API call")
    print("=" * 70 + "\n")

    conn = get_db_connection()
    ensure_emails_job_title_column(conn)

    stop_requested = False

    def handle_sigint(signum, frame):
        nonlocal stop_requested
        print("\n [!] Pause requested (Ctrl+C). Saving checkpoint gracefully...")
        stop_requested = True

    prev_handler = signal.signal(signal.SIGINT, handle_sigint)

    session_api_calls = 0
    session_titles_updated = 0
    start_time = time.time()

    try:
        while not stop_requested:
            # 1 single API call retrieves 100 contacts
            resp = client.fetch_contacts_scroll(
                view_id=DEFAULT_ALL_CONTACTS_VIEW_ID,
                last_fetched_id=last_id,
                limit=100
            )
            session_api_calls += 1

            contacts = resp.get("contacts", [])
            meta = resp.get("meta", {})
            has_next = meta.get("has_next_page", False)
            next_last_id = meta.get("last_fetched_id")

            if not contacts:
                print("\n[Finished] No more contacts returned by Freshsales API.")
                break

            total_processed += len(contacts)

            # Filter for contacts that have a valid job_title
            valid_updates: List[Tuple[str, str]] = []
            for c in contacts:
                email = str(c.get("email") or "").strip().lower()
                title = str(c.get("job_title") or "").strip()
                if email and title:
                    valid_updates.append((title[:255], email))

            # Cap updates to exact user requested target if specified
            if target_titles_count is not None:
                needed = max(0, target_titles_count - session_titles_updated)
                valid_updates = valid_updates[:needed]

            # Batch update in MySQL
            batch_updated_count = 0
            if valid_updates:
                with conn.cursor() as cur:
                    update_sql = """
                        UPDATE emails 
                        SET job_title = %s 
                        WHERE email = %s 
                          AND (job_title IS NULL OR job_title = '');
                    """
                    cur.executemany(update_sql, valid_updates)
                    batch_updated_count = cur.rowcount
                    conn.commit()

            total_updated += batch_updated_count
            session_titles_updated += batch_updated_count
            last_id = next_last_id or contacts[-1].get("id")

            # Progress output
            elapsed = time.time() - start_time
            rate_per_sec = total_processed / max(1.0, elapsed)
            titles_per_sec = session_titles_updated / max(1.0, elapsed)
            print(
                f"\r[API Call #{session_api_calls:,}] "
                f"Scanned: {total_processed:,} | "
                f"Titles Updated: {total_updated:,} (+{batch_updated_count} in batch) | "
                f"Speed: {rate_per_sec * 3600:,.0f} contacts/hr",
                end="",
                flush=True
            )

            # Checkpoint every 20 API calls (~2,000 contacts)
            if session_api_calls % 20 == 0:
                save_checkpoint({
                    "last_fetched_id": last_id,
                    "total_processed": total_processed,
                    "total_updated": total_updated,
                    "last_synced_at": datetime.now().isoformat(),
                })

            # Check if target limit is reached
            if target_titles_count is not None and session_titles_updated >= target_titles_count:
                print(f"\n\n[Target Reached] Successfully fetched and updated {session_titles_updated:,} job titles!")
                break

            if not has_next:
                print("\n\n[End of View] Reached the end of Freshsales contacts view.")
                break

    finally:
        save_checkpoint({
            "last_fetched_id": last_id,
            "total_processed": total_processed,
            "total_updated": total_updated,
            "last_synced_at": datetime.now().isoformat(),
        })
        conn.close()
        signal.signal(signal.SIGINT, prev_handler)

    print("\n" + "=" * 70)
    print(f" SESSION COMPLETE:")
    print(f" Total API Calls Made   : {session_api_calls:,}")
    print(f" Total Contacts Scanned : {total_processed:,}")
    print(f" Total Titles in DB     : {total_updated:,}")
    print(f" Checkpoint Saved At    : {CHECKPOINT_FILE}")
    print("=" * 70 + "\n")


def run_account_sync(client: FreshsalesClient, query_target: str) -> None:
    """
    Worker for Option 3: Fetch for a particular Account / Domain.
    Checks DB contacts: skips if title already exists, fetches remaining from Freshsales.
    """
    query_clean = query_target.strip().lower()
    if not query_clean:
        print("[!] No account or domain provided.")
        return

    print("\n" + "=" * 70)
    print(f" >>> QUERYING DATABASE FOR ACCOUNT: '{query_target}'")
    print("=" * 70)

    conn = get_db_connection()
    ensure_emails_job_title_column(conn)

    try:
        with conn.cursor() as cur:
            # Optimize to hit idx_emails_domain or idx_emails_company_name index directly
            if "." in query_clean:
                sql = "SELECT id, email, full_name, domain, company_name, job_title FROM emails WHERE domain = %s LIMIT 500;"
                cur.execute(sql, (query_clean,))
            else:
                sql = "SELECT id, email, full_name, domain, company_name, job_title FROM emails WHERE company_name = %s LIMIT 500;"
                cur.execute(sql, (query_clean,))
            rows = cur.fetchall()

            if not rows and "." not in query_clean:
                # Fallback: search domain prefix
                cur.execute("SELECT id, email, full_name, domain, company_name, job_title FROM emails WHERE domain LIKE %s LIMIT 500;", (f"{query_clean}.%",))
                rows = cur.fetchall()

        if not rows:
            print(f" [No Match] No contacts found in database for domain or company '{query_target}'.")
            return

        print(f"\n Found {len(rows)} contact(s) associated with this account:\n")

        already_have_title = []
        missing_title = []

        for r in rows:
            t = (r.get("job_title") or "").strip()
            if t:
                already_have_title.append(r)
            else:
                missing_title.append(r)

        # 1. Report contacts that already have job titles
        if already_have_title:
            print(f" ✓ {len(already_have_title)} contact(s) ALREADY have job titles (SKIPPED):")
            for r in already_have_title:
                print(f"    - {r['email']:<32} | {r.get('full_name') or 'N/A':<20} | Title: {r['job_title']}")
            print()

        # 2. Report contacts needing job titles
        if not missing_title:
            print(" ✓ All contacts for this account already have job titles in the database. Nothing to fetch!")
            return

        print(f" ⚡ {len(missing_title)} contact(s) MISSING job title. Querying Freshsales CRM...")

        newly_updated = 0
        with conn.cursor() as cur:
            for idx, r in enumerate(missing_title, 1):
                email = r["email"].strip().lower()
                print(f"    [{idx}/{len(missing_title)}] Searching Freshsales for: {email}...", end=" ", flush=True)

                fs_contact = client.search_contact_by_email(email)
                if not fs_contact:
                    print("NOT FOUND in Freshsales.")
                    continue

                fs_title = (fs_contact.get("job_title") or "").strip()
                if not fs_title:
                    print("Found in Freshsales, but job_title is EMPTY.")
                    continue

                # Update DB
                cur.execute("UPDATE emails SET job_title = %s WHERE email = %s", (fs_title[:255], email))
                conn.commit()
                newly_updated += 1
                print(f"FOUND! Title: '{fs_title}' -> Updated in DB.")

        print("\n" + "=" * 70)
        print(f" ACCOUNT SYNC SUMMARY FOR '{query_target}':")
        print(f" Total Contacts in DB : {len(rows)}")
        print(f" Already Had Title    : {len(already_have_title)} (Skipped)")
        print(f" Newly Updated        : {newly_updated}")
        print("=" * 70 + "\n")

    finally:
        conn.close()


# ==============================================================================
# MAIN INTERACTIVE CLI
# ==============================================================================

def main():
    if not FS_API_KEY:
        print("[ERROR] FRESHSALES_API_KEY is not set in freshsales_agent/.env or .env!")
        sys.exit(1)

    client = FreshsalesClient(api_key=FS_API_KEY, domain=FS_DOMAIN)

    while True:
        print("\n" + "=" * 70)
        print("              FRESHSALES CRM JOB TITLE SYNCHRONIZER")
        print("=" * 70)
        print(f" Target Table : emails (MySQL: {DB_HOST}:{DB_PORT}/{DB_NAME})")
        print(f" CRM Endpoint : {FS_DOMAIN}")
        print(f" Batch Speed  : 100 contacts per single API call")
        print(f" Safe Pacing  : ~4,000 to 4,500 requests/hour (~400,000 contacts/hour)")
        print("=" * 70)
        print(" Please select an option:")
        print("   [1] Select how many job titles you want to fetch (Custom Limit)")
        print("   [2] Select ALL (Full sync across all missing contacts)")
        print("   [3] Fetch for a Particular Account / Domain (Skip existing)")
        print("   [4] Reset Checkpoint (Start from scratch)")
        print("   [0] Exit")
        print("=" * 70)

        choice = input(" Enter your choice [0-4]: ").strip()

        if choice == "1":
            raw_limit = input(" Enter number of job titles to fetch (e.g. 1000, 5000, 50000): ").strip()
            try:
                target = int(raw_limit.replace(",", ""))
                if target <= 0:
                    print("[!] Please enter a positive number.")
                    continue
                run_bulk_sync(client, target_titles_count=target)
            except ValueError:
                print("[!] Invalid number entered.")

        elif choice == "2":
            confirm = input(" Are you sure you want to run FULL sync for ALL missing contacts? [y/N]: ").strip().lower()
            if confirm == "y":
                run_bulk_sync(client, target_titles_count=None)

        elif choice == "3":
            account_input = input(" Enter Account Name or Domain (e.g. chick-fil-a.com or dropcontact.io): ").strip()
            if account_input:
                run_account_sync(client, account_input)

        elif choice == "4":
            if CHECKPOINT_FILE.exists():
                CHECKPOINT_FILE.unlink()
                print(" ✓ Checkpoint file deleted. Next bulk run will start from the beginning.")
            else:
                print(" Checkpoint file does not exist.")

        elif choice == "0":
            print(" Exiting. Goodbye!")
            break
        else:
            print(" Invalid choice, please enter 0, 1, 2, 3, or 4.")


if __name__ == "__main__":
    main()
