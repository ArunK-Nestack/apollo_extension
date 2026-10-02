import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import get_connection

def create_table():
    print("[*] Connecting to database to create `enrich_companies` table...")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `enrich_companies` (
                    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                    `batch` VARCHAR(64) NOT NULL DEFAULT 'enrich_companies_1',
                    `company_name` VARCHAR(255) NOT NULL DEFAULT '',
                    `domain` VARCHAR(255) NOT NULL DEFAULT '',
                    `website_link` VARCHAR(512) DEFAULT '',
                    `source` VARCHAR(64) DEFAULT 'enrich_extension',
                    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX `idx_batch` (`batch`),
                    INDEX `idx_company_name` (`company_name`),
                    INDEX `idx_domain` (`domain`),
                    UNIQUE KEY `unique_batch_domain` (`batch`, `domain`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
            print("[✓] Table `enrich_companies` created or already exists successfully.")

            cur.execute("DESCRIBE `enrich_companies`;")
            cols = cur.fetchall()
            print("\nTable Schema:")
            for c in cols:
                print(f"  - {c[0]}: {c[1]} (Null: {c[2]}, Key: {c[3]}, Default: {c[4]})")

if __name__ == "__main__":
    create_table()
