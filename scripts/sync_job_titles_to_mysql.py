#!/usr/bin/env python3
"""
================================================================================
                    FRESHSALES CRM -> AWS RDS MYSQL
                   JOB TITLE SYNCHRONIZATION ENGINE
================================================================================
Transfers ~6.11 Million contact job titles from PostgreSQL (freshsales_crm)
to AWS RDS MySQL (apollo_scrapers.emails) with high-speed streaming batching,
zero-deadlock execution, live terminal dashboard, automatic checkpoints, and
automated 5-minute progress digests delivered to Telegram.

Usage:
    python scripts/sync_job_titles_to_mysql.py
    python scripts/sync_job_titles_to_mysql.py --batch-size 3000
    python scripts/sync_job_titles_to_mysql.py --telegram-interval 300 # 5 mins
    python scripts/sync_job_titles_to_mysql.py --reset    # Restart from row 0
    python scripts/sync_job_titles_to_mysql.py --limit 10000 # Test first 10k
================================================================================
"""

import os
import sys
import time
import json
import signal
import argparse
import requests
from pathlib import Path
from datetime import datetime
from collections import deque
from dotenv import load_dotenv
import psycopg
import pymysql

# ------------------------------------------------------------------------------
# ENVIRONMENT & CONFIGURATION
# ------------------------------------------------------------------------------
PROJECT_ROOT = Path(r"c:\Users\test\Desktop\projects\apollo_extension")
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / "freshsales_agent" / ".env")
load_dotenv(PROJECT_ROOT / ".env", override=False)

CHECKPOINT_FILE = PROJECT_ROOT / ".sync_job_titles_checkpoint.json"

# Telegram credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Interruption flag for graceful exit on Ctrl+C
SHUTDOWN_REQUESTED = False

def sigint_handler(signum, frame):
    global SHUTDOWN_REQUESTED
    print("\n\n[!] Interrupt signal received (Ctrl+C). Saving checkpoint and exiting gracefully...")
    SHUTDOWN_REQUESTED = True

signal.signal(signal.SIGINT, sigint_handler)

# ------------------------------------------------------------------------------
# TELEGRAM DISPATCHER
# ------------------------------------------------------------------------------
def send_telegram_alert(message_html: str) -> bool:
    """Dispatches a notification to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        # Don't interrupt db operations if telegram experiences a network hitch
        return False

# ------------------------------------------------------------------------------
# DATABASE CONNECTION FACTORIES
# ------------------------------------------------------------------------------
def get_mysql_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "apollo_scrapers"),
        autocommit=False,
        connect_timeout=15,
        read_timeout=60,
        write_timeout=60
    )

def get_pg_conn():
    return psycopg.connect(
        "host=127.0.0.1 port=5432 dbname=freshsales_crm user=postgres password=postgres"
    )

# ------------------------------------------------------------------------------
# CHECKPOINT MANAGEMENT
# ------------------------------------------------------------------------------
def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_id", 0), data.get("processed", 0)
        except Exception:
            pass
    return 0, 0

def save_checkpoint(last_id, processed):
    try:
        temp_file = CHECKPOINT_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump({
                "last_id": last_id,
                "processed": processed,
                "timestamp": datetime.now().isoformat()
            }, f, indent=2)
        if temp_file.exists():
            temp_file.replace(CHECKPOINT_FILE)
    except Exception as e:
        print(f"[!] Warning: Could not save checkpoint: {e}", file=sys.stderr)

def clear_checkpoint():
    if CHECKPOINT_FILE.exists():
        try:
            CHECKPOINT_FILE.unlink()
        except Exception:
            pass

# ------------------------------------------------------------------------------
# TERMINAL DASHBOARD FORMATTING
# ------------------------------------------------------------------------------
def format_time(seconds):
    if seconds is None or seconds < 0 or seconds > 86400 * 30:
        return "--:--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def render_dashboard(processed, total, rate, elapsed, eta, errors):
    pct = (processed / total * 100.0) if total > 0 else 0.0
    bar_width = 30
    filled = int(bar_width * (pct / 100.0))
    bar = "=" * filled + (">" if filled < bar_width else "") + " " * (bar_width - filled - (1 if filled < bar_width else 0))
    
    status_line = (
        f"\r[{bar}] {pct:5.1f}% | "
        f"{processed:,}/{total:,} | "
        f"{rate:5,.0f} rows/s | "
        f"Elapsed: {format_time(elapsed)} | "
        f"ETA: {format_time(eta)} | "
        f"Err: {errors}"
    )
    sys.stdout.write(status_line)
    sys.stdout.flush()

# ------------------------------------------------------------------------------
# BATCH EXECUTION ENGINE (ZERO-DEADLOCK PERSISTENT PIPELINE)
# ------------------------------------------------------------------------------
def execute_chunk_with_retry(m_conn, chunk, dry_run=False):
    """Executes a single multi-row chunk with automatic reconnect/retry."""
    if not chunk or dry_run:
        return m_conn, len(chunk), 0

    max_retries = 5
    last_error = None
    for attempt in range(max_retries):
        try:
            placeholders = ", ".join(["(%s, %s)"] * len(chunk))
            params = []
            for _, em, jt in chunk:
                params.extend([em[:255], jt[:255]])

            sql = (
                f"INSERT INTO emails (email, job_title) VALUES {placeholders} "
                f"AS new_data ON DUPLICATE KEY UPDATE job_title = new_data.job_title"
            )

            with m_conn.cursor() as cur:
                cur.execute(sql, params)
            m_conn.commit()
            return m_conn, len(chunk), 0
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            last_error = e
            print(
                f"\n[!] Batch attempt {attempt + 1}/{max_retries} failed "
                f"({type(e).__name__}): {e}",
                file=sys.stderr,
            )
            time.sleep(2.0 * (attempt + 1))
            try:
                m_conn.close()
            except Exception:
                pass
            try:
                m_conn = get_mysql_conn()
            except Exception:
                pass
        except Exception as e:
            last_error = e
            print(
                f"\n[!] Batch attempt {attempt + 1}/{max_retries} failed "
                f"({type(e).__name__}): {e}",
                file=sys.stderr,
            )
            try:
                m_conn.rollback()
            except Exception:
                pass
            time.sleep(2.0)

    first_id = chunk[0][0]
    last_id = chunk[-1][0]
    print(
        f"\n[!] Failed batch of {len(chunk)} rows after {max_retries} attempts "
        f"(contact IDs {first_id}-{last_id}). Last error: {last_error}",
        file=sys.stderr,
    )
    return m_conn, 0, len(chunk)


def next_checkpoint_id(current_high_id, chunk, successful_count):
    """Advance only after every row in the ordered source chunk was committed."""
    if chunk and successful_count == len(chunk):
        return chunk[-1][0]
    return current_high_id

# ------------------------------------------------------------------------------
# MAIN COORDINATOR
# ------------------------------------------------------------------------------
def main():
    global SHUTDOWN_REQUESTED

    parser = argparse.ArgumentParser(description="Synchronize Freshsales job titles to MySQL")
    parser.add_argument("--batch-size", type=int, default=2500, help="Number of rows per SQL statement (default: 2500)")
    parser.add_argument("--telegram-interval", type=int, default=300, help="Telegram report interval in seconds (default: 300 = 5 mins)")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram notifications")
    parser.add_argument("--limit", type=int, default=None, help="Process up to N rows (for testing)")
    parser.add_argument("--reset", action="store_true", help="Ignore saved checkpoint and start from row 0")
    parser.add_argument("--dry-run", action="store_true", help="Read from PG and simulate without writing to MySQL")
    args = parser.parse_args()

    # 1. Clear or Load checkpoint
    if args.reset:
        clear_checkpoint()
        last_id, processed_offset = 0, 0
    else:
        last_id, processed_offset = load_checkpoint()

    # 2. Get total records in PostgreSQL
    print("=" * 80)
    print("           FRESHSALES CRM -> AWS RDS MYSQL JOB TITLE SYNC")
    print("=" * 80)
    print(f"Target RDS Host   : {os.getenv('DB_HOST', '127.0.0.1')}")
    print(f"Database/Table    : {os.getenv('DB_NAME', 'apollo_scrapers')}.emails")
    print(f"Batch Size        : {args.batch_size:,} rows/statement")
    print(f"Engine Mode       : High-Speed Sequential Pipeline (Deadlock-Free)")
    telegram_active = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and not args.no_telegram)
    print(f"Telegram Alerts   : {'ACTIVE (Every 5 mins)' if telegram_active else 'DISABLED'}")
    if args.dry_run:
        print("Mode              : [DRY-RUN SIMULATION - NO DB WRITES]")
    print("-" * 80)

    print("Connecting to databases...", end=" ", flush=True)
    try:
        with get_pg_conn() as p_test, get_mysql_conn() as m_test:
            pass
        print("SUCCESS [PostgreSQL & MySQL Online]")
    except Exception as e:
        print(f"FAILED!\nError: {e}")
        sys.exit(1)

    print("Calculating records with job titles in PostgreSQL...", end=" ", flush=True)
    with get_pg_conn() as p_conn:
        with p_conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM freshsales_contacts 
                WHERE email IS NOT NULL AND email != '' 
                  AND job_title IS NOT NULL AND TRIM(job_title) != ''
            """)
            grand_total = cur.fetchone()[0]
    print(f"{grand_total:,} records found.")

    if last_id > 0:
        print(f"[CHECKPOINT] Resuming from Contact ID: {last_id:,} (Already processed: {processed_offset:,})")
    
    target_count = min(grand_total, args.limit) if args.limit else grand_total
    remaining_to_process = max(0, target_count - processed_offset)

    if remaining_to_process == 0:
        print("[SUCCESS] All contacts are already processed. Nothing to do!")
        clear_checkpoint()
        return

    print(f"Total remaining to synchronize : {remaining_to_process:,} rows")
    print("=" * 80)
    print("Live Monitor Active (Press Ctrl+C at any time to pause and save checkpoint)\n")

    # Send Launch Notification to Telegram
    if telegram_active:
        pct_start = (processed_offset / target_count * 100.0) if target_count > 0 else 0
        launch_msg = (
            "🚀 <b>Job Title Sync Started</b>\n\n"
            f"• <b>Target:</b> AWS RDS MySQL (<code>apollo_scrapers.emails</code>)\n"
            f"• <b>Total Targets:</b> <code>{target_count:,}</code> rows\n"
            f"• <b>Starting From:</b> <code>{processed_offset:,}</code> ({pct_start:.1f}%)\n"
            f"• <b>Remaining:</b> <code>{remaining_to_process:,}</code> rows\n"
            f"• <b>Reports:</b> Dispatched every 5 minutes ⏱️\n"
            f"• <b>Started:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        send_telegram_alert(launch_msg)

    t_start = time.time()
    total_processed = processed_offset
    total_errors = 0
    rate_window = deque(maxlen=20)
    last_print_time = 0
    last_checkpoint_time = time.time()
    last_telegram_time = time.time()
    current_high_id = last_id
    batch_failure = None

    m_conn = get_mysql_conn()

    try:
        with get_pg_conn() as p_conn:
            with p_conn.cursor(name="title_sync_stream") as cur:
                cur.itersize = 10000
                
                sql = """
                    SELECT id, LOWER(TRIM(email)), TRIM(job_title)
                    FROM freshsales_contacts
                    WHERE id > %s
                      AND email IS NOT NULL AND email != ''
                      AND job_title IS NOT NULL AND TRIM(job_title) != ''
                    ORDER BY id ASC
                """
                if args.limit:
                    sql += f" LIMIT {remaining_to_process}"
                
                cur.execute(sql, (last_id,))

                chunk = []
                for row in cur:
                    if SHUTDOWN_REQUESTED:
                        break

                    chunk.append(row)
                    if len(chunk) >= args.batch_size:
                        m_conn, ok, err = execute_chunk_with_retry(m_conn, chunk, args.dry_run)
                        total_processed += ok
                        total_errors += err
                        next_high_id = next_checkpoint_id(current_high_id, chunk, ok)
                        if err:
                            batch_failure = {
                                "first_id": chunk[0][0],
                                "last_id": chunk[-1][0],
                                "rows": err,
                            }
                            save_checkpoint(current_high_id, total_processed)
                            chunk = []
                            break
                        current_high_id = next_high_id
                        chunk = []

                        now = time.time()
                        elapsed = now - t_start
                        recent_rate = (total_processed - processed_offset) / elapsed if elapsed > 0 else 0
                        rate_window.append(recent_rate)
                        smoothed_rate = sum(rate_window) / len(rate_window) if rate_window else recent_rate
                        rem_items = target_count - total_processed
                        eta_sec = rem_items / smoothed_rate if smoothed_rate > 0 else 0

                        if now - last_print_time >= 0.25:
                            render_dashboard(
                                total_processed, target_count, smoothed_rate, 
                                elapsed, eta_sec, total_errors
                            )
                            last_print_time = now

                        # Auto-save checkpoint every 30 seconds
                        if now - last_checkpoint_time >= 30.0:
                            save_checkpoint(current_high_id, total_processed)
                            last_checkpoint_time = now

                        # Automated Telegram Progress Digest every 5 minutes (default 300s)
                        if telegram_active and (now - last_telegram_time >= args.telegram_interval):
                            pct = (total_processed / target_count * 100.0) if target_count > 0 else 0
                            update_msg = (
                                "📊 <b>Job Title Sync Progress (5-Min Digest)</b>\n\n"
                                f"• <b>Completed:</b> <code>{total_processed:,} / {target_count:,}</code> (<b>{pct:.1f}%</b>)\n"
                                f"• <b>Throughput:</b> <code>{smoothed_rate:,.0f} rows/sec</code>\n"
                                f"• <b>Elapsed Time:</b> <code>{format_time(elapsed)}</code>\n"
                                f"• <b>Estimated ETA:</b> <code>{format_time(eta_sec)}</code>\n"
                                f"• <b>Last Contact ID:</b> <code>{current_high_id}</code>\n"
                                f"• <b>Error Count:</b> <code>{total_errors}</code>\n"
                                f"• <b>Status:</b> 🟢 Healthy & Streaming"
                            )
                            send_telegram_alert(update_msg)
                            last_telegram_time = now

                # Final chunk if present
                if chunk and not SHUTDOWN_REQUESTED and not batch_failure:
                    m_conn, ok, err = execute_chunk_with_retry(m_conn, chunk, args.dry_run)
                    total_processed += ok
                    total_errors += err
                    next_high_id = next_checkpoint_id(current_high_id, chunk, ok)
                    if err:
                        batch_failure = {
                            "first_id": chunk[0][0],
                            "last_id": chunk[-1][0],
                            "rows": err,
                        }
                    else:
                        current_high_id = next_high_id
    finally:
        try:
            m_conn.close()
        except Exception:
            pass

    total_elapsed = time.time() - t_start
    avg_speed = (total_processed - processed_offset) / total_elapsed if total_elapsed > 0 else 0

    render_dashboard(
        total_processed, target_count, avg_speed, 
        total_elapsed, 0, total_errors
    )
    print("\n" + "=" * 80)

    if SHUTDOWN_REQUESTED:
        save_checkpoint(current_high_id, total_processed)
        print("\n[PAUSED] Checkpoint saved successfully.")
        print(f"Processed: {total_processed:,} / {target_count:,} ({total_processed/target_count*100:.1f}%)")
        print("To resume where you left off, simply run:")
        print("    python scripts/sync_job_titles_to_mysql.py")

        if telegram_active:
            pct = (total_processed / target_count * 100.0) if target_count > 0 else 0
            pause_msg = (
                "⏸️ <b>Job Title Sync Paused (Ctrl+C)</b>\n\n"
                f"• <b>Processed:</b> <code>{total_processed:,} / {target_count:,}</code> ({pct:.1f}%)\n"
                f"• <b>Checkpoint Saved:</b> Contact ID <code>{current_high_id}</code>\n"
                f"• <b>Elapsed:</b> <code>{format_time(total_elapsed)}</code>\n"
                f"• <b>Status:</b> Safe to resume anytime via terminal."
            )
            send_telegram_alert(pause_msg)
    elif batch_failure:
        save_checkpoint(current_high_id, total_processed)
        print("\n[PAUSED] An unrecoverable MySQL batch error occurred.")
        print(
            "Failed source contact IDs: "
            f"{batch_failure['first_id']}-{batch_failure['last_id']} "
            f"({batch_failure['rows']:,} rows)"
        )
        print(f"Checkpoint remains at the last committed contact ID: {current_high_id}")
        print("Correct the reported database error, then rerun the same command.")
    else:
        clear_checkpoint()
        print("\n[SUCCESS] Synchronization complete!")
        print(f"Total Titles Updated : {total_processed:,}")
        print(f"Total Errors         : {total_errors}")
        print(f"Total Elapsed Time   : {format_time(total_elapsed)}")
        print(f"Average Throughput   : {avg_speed:,.0f} rows/second")
        print("=" * 80)

        if telegram_active:
            complete_msg = (
                "✅ <b>Job Title Sync Completed!</b>\n\n"
                f"• <b>Total Titles Synced:</b> <code>{total_processed:,}</code> rows\n"
                f"• <b>Errors:</b> <code>{total_errors}</code>\n"
                f"• <b>Total Time:</b> <code>{format_time(total_elapsed)}</code>\n"
                f"• <b>Average Speed:</b> <code>{avg_speed:,.0f} rows/sec</code>\n"
                f"• <b>Database:</b> AWS RDS MySQL (<code>apollo_scrapers.emails</code>)"
            )
            send_telegram_alert(complete_msg)

if __name__ == "__main__":
    main()
