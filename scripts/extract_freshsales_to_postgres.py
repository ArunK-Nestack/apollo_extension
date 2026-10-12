#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freshsales CRM to PostgreSQL High-Performance Multi-Worker Extractor
=====================================================================
Extracts all ~7.6M contacts with 30-40 columns from Freshsales CRM
into local PostgreSQL database (`freshsales_crm.freshsales_contacts`).

Key Architectural Features:
  - Multi-Worker Parallelism: Runs across partitioned ID slices to overcome
    server latency (2.8-3.0s per request) without violating API caps.
  - Sequential Cursor Partitioning: Partitions the remaining ID space into distinct
    slices with strict stop boundaries to prevent overlaps or wasted calls.
  - Checkpoint Tracking: Thread-safe, atomic checkpointing with per-worker tracking
    (last_fetched_id, total_inserted, status) while preserving backwards compatibility.
  - Connection Isolation: Dedicated PostgreSQL connection and HTTP session per worker thread.
  - Safe Shared Rate Limiter: Synchronized token bucket capping requests across ALL workers
    to safely stay under account-wide Freshsales limits (default 3,600/hr, leaving 1,400/hr buffer).
  - Synchronized 429 Backoff: If any thread encounters HTTP 429, all workers pause immediately.
  - WhatsApp & Telegram Alerts: Sends launch, hourly digests, blocker alerts, and completion notices.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.types.json import Jsonb
import requests
from dotenv import load_dotenv

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
FS_DOMAIN = os.getenv("FRESHSALES_DOMAIN", "https://nestack.freshsales.io").strip()
VIEW_ID = 12001258277  # "All contacts" view
TOTAL_EXPECTED_CONTACTS = 7604088

# PostgreSQL credentials
PG_HOST = os.getenv("PG_HOST", "127.0.0.1").strip()
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_USER = os.getenv("PG_USER", "").strip()
PG_PASS = os.getenv("PG_PASS", "").strip()
PG_DB = os.getenv("PG_DB", "freshsales_crm").strip()

if not PG_USER or not PG_PASS:
    raise ValueError("Missing PG_USER or PG_PASS in environment (.env file).")
if not FS_API_KEY:
    raise ValueError("Missing FRESHSALES_API_KEY in environment (.env file).")

CHECKPOINT_FILE = PROJECT_ROOT / "config" / "freshsales_postgres_checkpoint.json"


def send_whatsapp(message: str) -> bool:
    """Dispatches a WhatsApp alert via CallMeBot."""
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    phone = os.getenv("WHATSAPP_PHONE_NUMBER", "").strip()
    apikey = os.getenv("WHATSAPP_CALLMEBOT_APIKEY", "").strip()
    if not phone or not apikey:
        return False
    url = "https://api.callmebot.com/whatsapp.php"
    params = {"phone": phone, "text": message, "apikey": apikey}
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            print("\n[WhatsApp ✓] Alert delivered successfully!")
            return True
        else:
            print(f"\n[WhatsApp !] CallMeBot returned status {resp.status_code}: {resp.text[:120]}")
            return False
    except Exception as e:
        print(f"\n[WhatsApp Warning] Failed to deliver notification: {e}")
        return False


def send_telegram(message: str) -> bool:
    """Dispatches an alert via Official Telegram Bot API."""
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("\n[Telegram ✓] Alert delivered successfully!")
            return True
        else:
            print(f"\n[Telegram !] Telegram returned status {resp.status_code}: {resp.text[:120]}")
            return False
    except Exception as e:
        print(f"\n[Telegram Warning] Failed to deliver notification: {e}")
        return False


def send_alert(message: str):
    """Sends alert to configured notification channels."""
    send_telegram(message)
    send_whatsapp(message)


def get_pg_connection():
    return psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASS,
        dbname=PG_DB,
        autocommit=False,
    )


class ThreadSafeRateLimiter:
    """Enforces strict account-wide rate limit across all worker threads."""

    def __init__(self, max_calls_per_hour: int = 3600):
        self.max_calls_per_hour = max_calls_per_hour
        self.min_interval = 3600.0 / max_calls_per_hour
        self.lock = threading.Lock()
        self.last_call_time = 0.0
        self.hourly_call_count = 0
        self.hour_window_start = time.time()
        self.pause_until = 0.0

    def pause(self, seconds: float):
        with self.lock:
            self.pause_until = max(self.pause_until, time.time() + seconds)

    def wait(self):
        while True:
            with self.lock:
                now = time.time()
                if now < self.pause_until:
                    sleep_dur = self.pause_until - now
                else:
                    sleep_dur = 0.0

                if sleep_dur == 0.0:
                    if now - self.hour_window_start >= 3600:
                        self.hour_window_start = now
                        self.hourly_call_count = 0

                    if self.hourly_call_count >= self.max_calls_per_hour:
                        sleep_time = max(1.0, 3600.0 - (now - self.hour_window_start))
                        print(f"\n[Rate Limiter] Hourly limit reached. Pausing for {sleep_time:.1f}s...")
                        self.pause_until = now + sleep_time
                        sleep_dur = sleep_time
                    else:
                        elapsed = now - self.last_call_time
                        if elapsed < self.min_interval:
                            sleep_dur = self.min_interval - elapsed
                        else:
                            self.last_call_time = now
                            self.hourly_call_count += 1
                            return

            if sleep_dur > 0:
                time.sleep(min(sleep_dur, 0.5))


class CheckpointManager:
    """Thread-safe, atomic manager for multi-worker progress checkpoints."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict:
        if self.filepath.exists():
            try:
                return json.loads(self.filepath.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def get_or_init_workers(
        self,
        start_id: int,
        max_id: int,
        num_workers: int = 4,
        prior_inserted: int = 0,
        reset: bool = False
    ) -> dict:
        with self.lock:
            if not reset and "workers" in self.data and isinstance(self.data["workers"], dict) and len(self.data["workers"]) > 0:
                return self.data["workers"]

            # Initialize new partitioned worker slices
            step = max(1, (max_id - start_id) // num_workers)
            workers = {}
            for i in range(num_workers):
                w_start = start_id + i * step
                w_stop = start_id + (i + 1) * step if i < num_workers - 1 else None
                workers[f"worker_{i}"] = {
                    "start_id": w_start,
                    "stop_id": w_stop,
                    "last_fetched_id": w_start,
                    "total_inserted": 0,
                    "total_calls": 0,
                    "status": "ready"
                }

            self.data["workers"] = workers
            self.data["prior_single_run_inserted"] = prior_inserted
            self.data["total_inserted"] = prior_inserted
            self.data["total_calls"] = self.data.get("total_calls", 0)
            if "started_at" not in self.data:
                self.data["started_at"] = datetime.now().isoformat()
            self.data["last_synced_at"] = datetime.now().isoformat()
            self._save_unlocked()
            return workers

    def update_worker(
        self,
        worker_name: str,
        last_id: int,
        inserted_count: int,
        status: str = "running"
    ):
        with self.lock:
            workers = self.data.get("workers", {})
            w = workers.get(worker_name)
            if not w:
                return
            w["last_fetched_id"] = last_id
            w["total_inserted"] = w.get("total_inserted", 0) + inserted_count
            w["total_calls"] = w.get("total_calls", 0) + 1
            w["status"] = status

            # Recalculate top-level summary for backward compatibility
            workers_inserted = sum(item.get("total_inserted", 0) for item in workers.values())
            prior = self.data.get("prior_single_run_inserted", 0)
            self.data["total_inserted"] = prior + workers_inserted
            self.data["total_calls"] = sum(item.get("total_calls", 0) for item in workers.values()) + self.data.get("prior_single_calls", 0)
            self.data["last_fetched_id"] = last_id
            self.data["last_synced_at"] = datetime.now().isoformat()
            self._save_unlocked()

    def get_snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.data))

    def _save_unlocked(self):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.filepath.with_suffix(".tmp")
        for attempt in range(5):
            try:
                tmp_file.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
                tmp_file.replace(self.filepath)
                return
            except PermissionError:
                if attempt == 4:
                    try:
                        self.filepath.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
                        return
                    except Exception:
                        pass
                time.sleep(0.05 * (attempt + 1))
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(0.05)


def parse_timestamp(val: Any) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


def transform_contact(c: dict) -> tuple:
    cf = c.get("custom_field") or {}

    cid = c.get("id")
    display_name = c.get("display_name") or f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip() or None
    email = c.get("email")
    email_status = c.get("email_status")

    job_title = c.get("job_title")
    department = c.get("department")
    company_name = cf.get("cf_company_name")
    industry = cf.get("cf_contact_industry") or cf.get("cf_kw_industry")
    seniority = cf.get("cf_seniority") or cf.get("cf_seniority_level")
    employee_count = cf.get("cf_number_of_employee") or cf.get("cf_number_of_employees")
    annual_revenue = cf.get("cf_revenue")

    work_number = c.get("work_number")
    mobile_number = c.get("mobile_number")
    corporate_phone = cf.get("cf_corporate_phone")
    linkedin_url = c.get("linkedin")

    address = c.get("address")
    city = c.get("city")
    state = c.get("state")
    country = c.get("country")
    zipcode = c.get("zipcode") or cf.get("cf_postal_code")
    time_zone = c.get("time_zone")

    lead_score = c.get("lead_score")
    lead_quality = c.get("lead_quality")
    subscription_status = c.get("subscription_status")
    apollo_contact_id = cf.get("cf_apollo_contact_id")
    apollo_login_owner = cf.get("cf_apollo_login_owner")
    million_result = cf.get("cf_million_result")

    # Format tags cleanly
    tags_raw = c.get("tags")
    if isinstance(tags_raw, list):
        tags = ", ".join(str(t) for t in tags_raw)
    else:
        tags = str(tags_raw) if tags_raw else None

    job_subject_line = cf.get("cf_job_subject_line")
    job_template = cf.get("cf_job_template")

    created_at = parse_timestamp(c.get("created_at"))
    updated_at = parse_timestamp(c.get("updated_at"))

    return (
        cid, display_name, email, email_status,
        job_title, department, company_name, industry, seniority, employee_count, annual_revenue,
        work_number, mobile_number, corporate_phone, linkedin_url,
        address, city, state, country, zipcode, time_zone,
        lead_score, lead_quality, subscription_status, apollo_contact_id,
        apollo_login_owner, million_result, tags, job_subject_line, job_template,
        created_at, updated_at
    )


INSERT_SQL = """
INSERT INTO freshsales_contacts (
    id, display_name, email, email_status,
    job_title, department, company_name, industry, seniority, employee_count, annual_revenue,
    work_number, mobile_number, corporate_phone, linkedin_url,
    address, city, state, country, zipcode, time_zone,
    lead_score, lead_quality, subscription_status, apollo_contact_id,
    apollo_login_owner, million_result, tags, job_subject_line, job_template,
    created_at, updated_at
) VALUES (
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s
)
ON CONFLICT (id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    email = EXCLUDED.email,
    email_status = EXCLUDED.email_status,
    job_title = EXCLUDED.job_title,
    department = EXCLUDED.department,
    company_name = EXCLUDED.company_name,
    industry = EXCLUDED.industry,
    seniority = EXCLUDED.seniority,
    employee_count = EXCLUDED.employee_count,
    annual_revenue = EXCLUDED.annual_revenue,
    work_number = EXCLUDED.work_number,
    mobile_number = EXCLUDED.mobile_number,
    corporate_phone = EXCLUDED.corporate_phone,
    linkedin_url = EXCLUDED.linkedin_url,
    address = EXCLUDED.address,
    city = EXCLUDED.city,
    state = EXCLUDED.state,
    country = EXCLUDED.country,
    zipcode = EXCLUDED.zipcode,
    time_zone = EXCLUDED.time_zone,
    lead_score = EXCLUDED.lead_score,
    lead_quality = EXCLUDED.lead_quality,
    subscription_status = EXCLUDED.subscription_status,
    apollo_contact_id = EXCLUDED.apollo_contact_id,
    apollo_login_owner = EXCLUDED.apollo_login_owner,
    million_result = EXCLUDED.million_result,
    tags = EXCLUDED.tags,
    job_subject_line = EXCLUDED.job_subject_line,
    job_template = EXCLUDED.job_template,
    updated_at = EXCLUDED.updated_at,
    synced_at = NOW();
"""


def worker_task(
    worker_name: str,
    worker_cfg: dict,
    limiter: ThreadSafeRateLimiter,
    checkpoint_mgr: CheckpointManager,
    stop_event: threading.Event,
    max_records: Optional[int] = None
):
    """Execution loop for an isolated worker thread."""
    start_id = worker_cfg.get("start_id")
    stop_id = worker_cfg.get("stop_id")
    last_fetched_id = worker_cfg.get("last_fetched_id", start_id)

    # Isolated DB connection and HTTP session
    conn = get_pg_connection()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Token token={FS_API_KEY}",
        "Content-Type": "application/json",
    })

    checkpoint_mgr.update_worker(worker_name, last_fetched_id, 0, status="running")

    try:
        while not stop_event.is_set():
            if max_records:
                snap = checkpoint_mgr.get_snapshot()
                if snap.get("total_inserted", 0) >= max_records:
                    break

            limiter.wait()
            if stop_event.is_set():
                break

            url = f"{FS_DOMAIN}/api/contacts/scroll/{VIEW_ID}"
            params = {"limit": 100}
            if last_fetched_id is not None:
                params["last_fetched_id"] = last_fetched_id

            resp = None
            for attempt in range(4):
                if stop_event.is_set():
                    break
                try:
                    resp = session.get(url, params=params, timeout=60)
                    if resp.status_code == 429:
                        retry_sec = int(resp.headers.get("Retry-After", 60))
                        print(f"\n[{worker_name} ! Rate-Limit 429] Pausing all workers for {retry_sec}s...")
                        limiter.pause(retry_sec)
                        time.sleep(retry_sec)
                        continue
                    resp.raise_for_status()
                    break
                except requests.RequestException as e:
                    if attempt == 3:
                        msg = f"[{worker_name}] API Request failed after 4 retries: {e}"
                        print(f"\n❌ {msg}")
                        send_alert(f"🚨 *CRITICAL BLOCKER ALERT!*\n\n{msg}\nLast ID: {last_fetched_id}")
                        raise
                    time.sleep(2 ** attempt)

            if stop_event.is_set() or not resp or not resp.content:
                break

            data = resp.json()
            contacts = data.get("contacts", [])
            meta = data.get("meta", {})

            if not contacts:
                checkpoint_mgr.update_worker(worker_name, last_fetched_id, 0, status="completed")
                break

            # Filter contacts within this worker's slice partition
            valid_contacts = []
            reached_boundary = False
            for c in contacts:
                cid = c.get("id")
                if stop_id is not None and cid > stop_id:
                    reached_boundary = True
                    break
                valid_contacts.append(c)

            if valid_contacts:
                rows = [transform_contact(c) for c in valid_contacts]
                with conn.cursor() as cur:
                    cur.executemany(INSERT_SQL, rows)
                conn.commit()

                last_fetched_id = valid_contacts[-1].get("id")
                checkpoint_mgr.update_worker(
                    worker_name,
                    last_fetched_id,
                    len(rows),
                    status="completed" if reached_boundary else "running"
                )

            if reached_boundary:
                break

            has_next = meta.get("has_next_page", True)
            if not has_next:
                checkpoint_mgr.update_worker(worker_name, last_fetched_id, 0, status="completed")
                break

    except Exception as e:
        print(f"\n❌ [{worker_name}] Exception: {e}")
        checkpoint_mgr.update_worker(worker_name, last_fetched_id, 0, status=f"error: {e}")
    finally:
        conn.close()
        session.close()


def main(
    max_records: Optional[int] = None,
    num_workers: int = 4,
    rate_limit: int = 3600,
    reset_workers: bool = False
):
    print("================================================================================")
    print("🚀 Freshsales CRM -> PostgreSQL High-Performance Multi-Worker Extractor")
    print(f"🎯 Target Database: {PG_DB} @ {PG_HOST}:{PG_PORT} (table: freshsales_contacts)")
    print(f"📊 Expected Total Contacts: {TOTAL_EXPECTED_CONTACTS:,}")
    print(f"⚡ Concurrency: {num_workers} parallel workers")
    print(f"🛡️ Shared Rate Limit: {rate_limit:,} calls/hr (~{rate_limit*100:,} contacts/hr)")
    print(f"💾 Checkpoint: {CHECKPOINT_FILE}")
    print("================================================================================\n")

    checkpoint_mgr = CheckpointManager(CHECKPOINT_FILE)
    snap = checkpoint_mgr.get_snapshot()

    # Determine start ID & max ID
    prior_inserted = snap.get("total_inserted", 0)
    start_id = snap.get("last_fetched_id") or 12009563494
    max_id = 12018664356  # Freshsales latest observed contact ID

    workers_dict = checkpoint_mgr.get_or_init_workers(
        start_id=start_id,
        max_id=max_id,
        num_workers=num_workers,
        prior_inserted=prior_inserted,
        reset=reset_workers
    )

    print("Partitioned Slices:")
    for w_name, w_cfg in workers_dict.items():
        print(
            f"  • {w_name:10s} | Range: [{w_cfg['start_id']} -> {w_cfg['stop_id'] or 'END'}] | "
            f"Last Fetched: {w_cfg['last_fetched_id']} | Status: {w_cfg['status']}"
        )
    print(f"\n[Checkpoint Loaded] Overall Inserted So Far: {prior_inserted:,}\n")

    limiter = ThreadSafeRateLimiter(max_calls_per_hour=rate_limit)
    stop_event = threading.Event()

    def handle_sigint(sig, frame):
        print("\n\n⚠️ Interruption received! Finishing current batches and saving checkpoints...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_sigint)

    # Initial Launch Notification
    send_alert(
        f"🚀 *Freshsales CRM Multi-Worker Extractor — Started*\n\n"
        f"📊 *Target:* {TOTAL_EXPECTED_CONTACTS:,} contacts\n"
        f"⚡ *Workers:* {num_workers} parallel threads\n"
        f"🛡️ *Speed Cap:* {rate_limit:,} calls/hr (~{rate_limit*100:,} contacts/hr)\n"
        f"💾 *Database:* PostgreSQL (`freshsales_crm.freshsales_contacts`)\n"
        f"✅ Slices safely partitioned with 0 duplicates."
    )

    # Launch worker threads
    threads = []
    for w_name, w_cfg in workers_dict.items():
        if w_cfg.get("status") == "completed":
            print(f"[{w_name}] Already completed. Skipping.")
            continue
        t = threading.Thread(
            target=worker_task,
            args=(w_name, w_cfg, limiter, checkpoint_mgr, stop_event, max_records),
            name=w_name
        )
        t.daemon = True
        threads.append(t)
        t.start()

    start_time = time.time()
    last_alert_time = time.time()
    last_hour_inserted = prior_inserted

    # Main monitoring loop
    try:
        while not stop_event.is_set():
            time.sleep(2.0)
            any_alive = any(t.is_alive() for t in threads)
            if not any_alive:
                break

            current_snap = checkpoint_mgr.get_snapshot()
            total_stored = current_snap.get("total_inserted", 0)
            elapsed = time.time() - start_time
            rate_per_sec = (total_stored - prior_inserted) / max(elapsed, 0.001)
            pct = (total_stored / TOTAL_EXPECTED_CONTACTS) * 100
            rem = max(0, TOTAL_EXPECTED_CONTACTS - total_stored)
            eta_hours = (rem / max(rate_per_sec * 3600, 1.0)) if rate_per_sec > 0 else 0.0

            workers_status = " | ".join(
                f"{k[:4]}:{v.get('last_fetched_id', 0) % 10000:04d}(+{v.get('total_inserted', 0):,})"
                for k, v in current_snap.get("workers", {}).items()
            )

            sys.stdout.write(
                f"\r📦 Total: {total_stored:,}/{TOTAL_EXPECTED_CONTACTS:,} ({pct:.2f}%) | "
                f"Rate: {rate_per_sec:.1f} rec/s (~{rate_per_sec*3600:,.0f}/h) | "
                f"ETA: ~{eta_hours:.1f}h | [{workers_status}]"
            )
            sys.stdout.flush()

            # Hourly digest alert
            now_ts = time.time()
            if now_ts - last_alert_time >= 3600:
                contacts_this_hour = total_stored - last_hour_inserted
                send_alert(
                    f"📊 *Freshsales CRM Extractor — Hourly Digest*\n\n"
                    f"✅ *Progress:* {total_stored:,} / {TOTAL_EXPECTED_CONTACTS:,} ({pct:.2f}%)\n"
                    f"📈 *Last Hour Added:* +{contacts_this_hour:,} contacts\n"
                    f"⚡ *Current Speed:* ~{rate_per_sec*3600:,.0f} contacts/hr\n"
                    f"🟢 *Status:* Healthy across {num_workers} parallel workers\n"
                    f"⏳ *ETA Remaining:* ~{eta_hours:.1f} hours\n"
                    f"💾 *Database:* PostgreSQL (`freshsales_crm.freshsales_contacts`)"
                )
                last_alert_time = now_ts
                last_hour_inserted = total_stored

    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=10.0)

        final_snap = checkpoint_mgr.get_snapshot()
        total_stored = final_snap.get("total_inserted", 0)
        pct = (total_stored / TOTAL_EXPECTED_CONTACTS) * 100
        print(f"\n\n[Session Summary] Total Inserted: {total_stored:,} / {TOTAL_EXPECTED_CONTACTS:,} ({pct:.2f}%)")
        print("All checkpoints safely committed to disk.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="High-Performance Freshsales CRM to PostgreSQL Extractor")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent worker slices (default: 4)")
    parser.add_argument("--rate-limit", type=int, default=3600, help="Max total API calls per hour across all workers (default: 3600)")
    parser.add_argument("--limit", type=int, default=None, help="Stop after inserting this many records (for testing)")
    parser.add_argument("--reset-workers", action="store_true", help="Force repartitioning worker slices from current last ID")
    args = parser.parse_args()

    main(
        max_records=args.limit,
        num_workers=args.workers,
        rate_limit=args.rate_limit,
        reset_workers=args.reset_workers
    )
