#!/usr/bin/env python3
"""
Targeted Job Title Reconciliation: PostgreSQL -> AWS RDS MySQL
==============================================================
Identifies the exact ~67,497 emails in MySQL that have NULL job_title
where PostgreSQL freshsales_contacts has a valid job title, and updates
them in high-throughput chunked batches with automatic retry.
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
import psycopg
import pymysql

PROJECT_ROOT = Path(r"c:\Users\test\Desktop\projects\apollo_extension")
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / "freshsales_agent" / ".env")
load_dotenv(PROJECT_ROOT / ".env", override=False)

def get_pg_conn():
    return psycopg.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        user=os.getenv("PG_USER", "postgres"),
        password=os.getenv("PG_PASS", "postgres"),
        dbname=os.getenv("PG_DB", "freshsales_crm")
    )

def get_mysql_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "apollo_scrapers"),
        autocommit=False
    )

def main():
    print("=" * 80)
    print("      RECONCILING MISSING JOB TITLES: POSTGRESQL -> MYSQL")
    print("=" * 80)
    
    t0 = time.time()
    
    # 1. Load all emails from MySQL that have NULL or empty job_title
    print("Step 1/3: Loading MySQL emails with NULL job_title into memory...", end=" ", flush=True)
    m_conn = get_mysql_conn()
    try:
        with m_conn.cursor() as cur:
            cur.execute("SELECT lower(trim(email)) FROM emails WHERE (job_title IS NULL OR trim(job_title) = '');")
            null_emails_set = {r[0] for r in cur.fetchall()}
    finally:
        m_conn.close()
        
    print(f"DONE ({len(null_emails_set):,} candidates loaded in {time.time() - t0:.1f}s)")
    
    # 2. Stream PostgreSQL freshsales_contacts and find matches
    print("Step 2/3: Streaming PostgreSQL records to find missing titles...", flush=True)
    p_conn = get_pg_conn()
    m_conn = get_mysql_conn()
    
    matched_updates = []
    total_pg_scanned = 0
    total_updated = 0
    batch_size = 2000
    
    t_stream_start = time.time()
    try:
        with p_conn.cursor(name="reconcile_stream") as pcur:
            pcur.itersize = 25000
            pcur.execute("""
                SELECT lower(trim(email)), trim(job_title)
                FROM freshsales_contacts
                WHERE email IS NOT NULL AND trim(email) != ''
                  AND job_title IS NOT NULL AND trim(job_title) != ''
                ORDER BY id ASC;
            """)
            
            for em, jt in pcur:
                total_pg_scanned += 1
                if em in null_emails_set:
                    matched_updates.append((em, jt[:255]))
                    null_emails_set.remove(em)
                    
                    if len(matched_updates) >= batch_size:
                        _apply_batch(m_conn, matched_updates)
                        total_updated += len(matched_updates)
                        print(f"  -> Reconciled {total_updated:,} titles into MySQL (scanned {total_pg_scanned:,} PG rows)...", flush=True)
                        matched_updates = []
                        
            # Flush final remainder
            if matched_updates:
                _apply_batch(m_conn, matched_updates)
                total_updated += len(matched_updates)
                print(f"  -> Reconciled {total_updated:,} titles into MySQL (scanned {total_pg_scanned:,} PG rows)...", flush=True)
                matched_updates = []
                
    finally:
        p_conn.close()
        m_conn.close()
        
    print(f"\nStep 3/3: Verification & Final Audit (completed in {time.time() - t0:.1f}s)...")
    
    # 3. Final verification of counts in both databases
    m_conn = get_mysql_conn()
    p_conn = get_pg_conn()
    try:
        with p_conn.cursor() as cur:
            cur.execute("SELECT count(DISTINCT lower(trim(email))) FROM freshsales_contacts WHERE job_title IS NOT NULL AND trim(job_title) != '';")
            pg_distinct_with_title = cur.fetchone()[0]
            
        with m_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM emails WHERE job_title IS NOT NULL AND trim(job_title) != '';")
            my_with_title = cur.fetchone()[0]
            cur.execute("SELECT count(DISTINCT lower(trim(email))) FROM emails WHERE job_title IS NOT NULL AND trim(job_title) != '';")
            my_distinct_with_title = cur.fetchone()[0]
            
        print("=" * 80)
        print("FINAL VERIFICATION AUDIT")
        print("=" * 80)
        print(f"PostgreSQL distinct emails with title : {pg_distinct_with_title:,}")
        print(f"MySQL rows with populated title       : {my_with_title:,}")
        print(f"MySQL distinct emails with title      : {my_distinct_with_title:,}")
        print(f"Remaining discrepancy                 : {pg_distinct_with_title - my_distinct_with_title:,}")
        if pg_distinct_with_title == my_distinct_with_title:
            print("\n>>> SUCCESS: 100% EXACT PARITY ACHIEVED BETWEEN POSTGRESQL & MYSQL! <<<")
        else:
            print(f"\nNotice: Gap of {pg_distinct_with_title - my_distinct_with_title:,} remains.")
        print("=" * 80)
    finally:
        p_conn.close()
        m_conn.close()


def _apply_batch(conn, batch, max_retries=5):
    """Executes a batch update with automatic reconnect/retry."""
    placeholders = ", ".join(["(%s, %s)"] * len(batch))
    params = []
    for em, jt in batch:
        params.extend([em, jt])
        
    sql = (
        f"INSERT INTO emails (email, job_title) VALUES {placeholders} "
        f"AS new_data ON DUPLICATE KEY UPDATE job_title = new_data.job_title"
    )
    
    for attempt in range(max_retries):
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
            return
        except Exception as e:
            conn.rollback()
            if attempt == max_retries - 1:
                print(f"[!] Batch update failed after {max_retries} attempts: {e}", file=sys.stderr)
                raise
            time.sleep(1.0 * (attempt + 1))


if __name__ == "__main__":
    main()
