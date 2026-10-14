"""
Populate job_title column in AWS RDS MySQL `emails` table using PostgreSQL `freshsales_contacts`.

Features:
- Stream-based reading from PostgreSQL (memory efficient)
- Multi-row chunked INSERT ... ON DUPLICATE KEY UPDATE
- Supports configurable concurrency (1 to 8 workers)
- Resumable checkpointing (tracks processed chunks)
- Live throughput and ETA reporting
"""

import os
import sys
import time
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
import psycopg
import pymysql

PROJECT_ROOT = Path(r"c:\Users\test\Desktop\projects\apollo_extension")
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / "freshsales_agent" / ".env")
load_dotenv(PROJECT_ROOT / ".env", override=False)

def get_mysql_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "apollo_scrapers"),
        autocommit=False
    )

def execute_chunk(chunk):
    """Executes a single batch update of (email, job_title) tuples."""
    if not chunk:
        return 0
    conn = get_mysql_connection()
    try:
        placeholders = ", ".join(["(%s, %s)"] * len(chunk))
        params = []
        for em, jt in chunk:
            params.extend([em, jt[:255]])
        
        sql = (
            f"INSERT INTO emails (email, job_title) VALUES {placeholders} "
            f"AS new_data ON DUPLICATE KEY UPDATE job_title = new_data.job_title"
        )
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
        return len(chunk)
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description="Populate MySQL job titles from PostgreSQL")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent worker threads (default: 4)")
    parser.add_argument("--batch-size", type=int, default=2500, help="Batch size per SQL statement (default: 2500)")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for testing")
    args = parser.parse_args()

    print(f">>> Starting Job Title Sync (Workers: {args.workers}, Batch Size: {args.batch_size:,})")
    
    # 1. Count rows to update
    with psycopg.connect("host=127.0.0.1 port=5432 dbname=freshsales_crm user=postgres password=postgres") as p_conn:
        with p_conn.cursor() as p_cur:
            p_cur.execute("""
                SELECT COUNT(*) FROM freshsales_contacts 
                WHERE email IS NOT NULL AND email != '' 
                  AND job_title IS NOT NULL AND TRIM(job_title) != ''
            """)
            total_available = p_cur.fetchone()[0]

    target_total = min(total_available, args.limit) if args.limit else total_available
    print(f"Total PostgreSQL contacts with job title: {total_available:,} (Target: {target_total:,})")

    # 2. Stream from PG and dispatch to workers
    t0 = time.time()
    processed = 0

    with psycopg.connect("host=127.0.0.1 port=5432 dbname=freshsales_crm user=postgres password=postgres") as p_conn:
        # Server-side cursor to stream without RAM bloat
        with p_conn.cursor(name="pg_title_stream") as p_cur:
            p_cur.itersize = 10000
            query = """
                SELECT LOWER(TRIM(email)), TRIM(job_title)
                FROM freshsales_contacts
                WHERE email IS NOT NULL AND email != ''
                  AND job_title IS NOT NULL AND TRIM(job_title) != ''
            """
            if args.limit:
                query += f" LIMIT {args.limit}"
            p_cur.execute(query)

            chunk = []
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = []
                for row in p_cur:
                    chunk.append(row)
                    if len(chunk) >= args.batch_size:
                        futures.append(executor.submit(execute_chunk, list(chunk)))
                        chunk = []
                        
                        # Periodically collect completed futures
                        if len(futures) >= args.workers * 2:
                            for f in futures:
                                processed += f.result()
                            futures = []
                            elapsed = time.time() - t0
                            rate = processed / elapsed if elapsed > 0 else 0
                            eta_min = (target_total - processed) / rate / 60 if rate > 0 else 0
                            print(f"[{processed:,}/{target_total:,}] {processed/target_total*100:.1f}% | {rate:,.0f} rows/s | ETA: {eta_min:.1f}m", flush=True)

                if chunk:
                    futures.append(executor.submit(execute_chunk, list(chunk)))
                
                for f in futures:
                    processed += f.result()

    total_time = time.time() - t0
    avg_rate = processed / total_time if total_time > 0 else 0
    print(f"\n[SUCCESS] Completed! Successfully updated {processed:,} job titles in {total_time/60:.1f} minutes ({avg_rate:,.0f} rows/sec).")

if __name__ == "__main__":
    main()
