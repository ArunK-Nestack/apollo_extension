#!/usr/bin/env python3
"""
Populate `company_name` in `emails` table from `freshsales_companies`.
1. Adds `company_name` column to `emails` table if not present.
2. Creates an ultra-fast temporary lookup table `freshsales_domain_map` with PRIMARY KEY (domain).
3. Updates `emails` in safe chunks of 100,000 rows by ID range with live progress reporting.
4. Cleans up temporary table upon completion.
"""

import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import backend.api as api

def main():
    print("=" * 80)
    print("MAPPING FRESHSALES COMPANIES TO EMAILS TABLE DOMAINS")
    print("=" * 80)

    conn = api.get_connection()
    try:
        with conn.cursor() as cur:
            # Step 1: Ensure column exists
            print("\n[Step 1/4] Checking 'company_name' column in `emails` table...")
            cur.execute("SHOW COLUMNS FROM `emails` LIKE 'company_name'")
            col = cur.fetchone()
            if not col:
                print(">> Adding column `company_name` VARCHAR(255) NULL to `emails`...")
                t0 = time.time()
                cur.execute("ALTER TABLE `emails` ADD COLUMN `company_name` VARCHAR(255) NULL DEFAULT NULL")
                conn.commit()
                print(f"✓ Column added in {time.time() - t0:.2f}s!")
            else:
                print(f"✓ Column `company_name` already exists: {col[1]}")

            # Step 2: Create temporary lookup table with exact matching collation
            print("\n[Step 2/4] Building clean primary-keyed domain mapping table...")
            cur.execute("""
                SELECT collation_name 
                FROM information_schema.columns 
                WHERE table_name = 'emails' AND column_name = 'domain'
            """)
            r_col = cur.fetchone()
            emails_collation = r_col[0] if r_col and r_col[0] else "utf8mb4_unicode_ci"
            print(f"  • Matched collation for `domain`: {emails_collation}")

            cur.execute("DROP TABLE IF EXISTS `freshsales_domain_map`")
            t0 = time.time()
            cur.execute(f"""
                CREATE TABLE `freshsales_domain_map` (
                    `domain` VARCHAR(255) CHARACTER SET utf8mb4 COLLATE {emails_collation} NOT NULL PRIMARY KEY,
                    `company_name` VARCHAR(255) CHARACTER SET utf8mb4 COLLATE {emails_collation} NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={emails_collation};
            """)
            conn.commit()

            print(">> Populating `freshsales_domain_map` from `freshsales_companies`...")
            cur.execute("""
                INSERT IGNORE INTO `freshsales_domain_map` (`domain`, `company_name`)
                SELECT `domain`, `company_name`
                FROM `freshsales_companies`
                WHERE `domain` IS NOT NULL AND `domain` != '' AND `company_name` IS NOT NULL AND `company_name` != '';
            """)
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM `freshsales_domain_map`")
            map_count = cur.fetchone()[0]
            print(f"✓ Populated {map_count:,d} distinct company domains in {time.time() - t0:.2f}s!")

            # Step 3: Check ID bounds in emails
            print("\n[Step 3/4] Preparing chunked update across `emails` table...")
            cur.execute("SELECT MIN(id), MAX(id), COUNT(*) FROM `emails`")
            min_id, max_id, total_emails = cur.fetchone()
            print(f"  • Emails Total Records: {total_emails:,d}")
            print(f"  • ID Range            : {min_id:,d} to {max_id:,d}")

            chunk_size = 100000
            total_chunks = ((max_id - min_id) // chunk_size) + 1
            print(f"  • Chunk Size          : {chunk_size:,d} rows per batch (~{total_chunks} batches)")

            # Step 4: Chunked update loop
            print("\n[Step 4/4] Executing chunked update...")
            current_start = min_id
            chunk_num = 0
            total_updated = 0
            start_time = time.time()

            while current_start <= max_id:
                chunk_num += 1
                current_end = current_start + chunk_size - 1
                chunk_t0 = time.time()

                query = """
                    UPDATE `emails` e
                    JOIN `freshsales_domain_map` m ON e.domain = m.domain
                    SET e.company_name = m.company_name
                    WHERE e.id BETWEEN %s AND %s AND (e.company_name IS NULL OR e.company_name = '');
                """
                cur.execute(query, (current_start, current_end))
                affected = cur.rowcount
                conn.commit()
                total_updated += affected

                chunk_elapsed = time.time() - chunk_t0
                pct = min(100.0, (current_end / max_id) * 100)
                print(f"  [{chunk_num:>3}/{total_chunks}] IDs {current_start:>9,d} - {current_end:>9,d} | Updated: {affected:>6,d} rows | Total: {total_updated:>8,d} ({pct:>5.1f}%) | {chunk_elapsed:.2f}s", flush=True)

                current_start = current_end + 1

            total_elapsed = time.time() - start_time
            print("\n" + "=" * 80)
            print(f"✓ UPDATE COMPLETE in {total_elapsed:.1f}s!")
            print(f"✓ Total `emails` records mapped with company_name: {total_updated:,d}")
            print("=" * 80)

            # Check final stats
            cur.execute("SELECT COUNT(*) FROM `emails` WHERE company_name IS NOT NULL AND company_name != ''")
            mapped_total = cur.fetchone()[0]
            print(f"\nVerification: {mapped_total:,d} / {total_emails:,d} ({mapped_total/total_emails*100:.2f}%) of emails have company_name populated.")

            # Cleanup temp table
            print("Cleaning up temporary `freshsales_domain_map`...")
            cur.execute("DROP TABLE IF EXISTS `freshsales_domain_map`")
            conn.commit()
            print("✓ Done!")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
