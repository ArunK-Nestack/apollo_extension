import csv
import os
import sys
import unicodedata
import re
import pymysql
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(r"c:\Users\test\Desktop\projects\apollo_extension\.env")

CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "job_titles (2) - job_titles (2).csv"
)

# Required Segments (Can close, approve, or support):
REQUIRED_SEGMENTS = {
    "A1_Signer",
    "A2_Budget_Holder",
    "A3_Approver",
    "B1_Champion",
    "B1_Champion_Technical",
    "B2_Champion_Commercial",
    "B3_Technical_Evaluator",
    "B4_Process_Owner",
    "C1_User",
    "D1_Door_Opener",
    "D2_Regional_Leader",
}

def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value).strip().lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", value)


def import_job_titles_pipeline(csv_path: str = CSV_PATH):
    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found at {csv_path}")
        return

    print("=" * 70)
    print(">>> [JOB TITLES PIPELINE] Ingesting CSV into MySQL Database")
    print(f"    Source: {csv_path}")
    print("=" * 70)

    conn = pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        autocommit=True,
    )

    with conn.cursor() as cur:
        # Create the new job_title_guardrails table
        print("\n1. Ensuring table `job_title_guardrails` exists...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `job_title_guardrails` (
                `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                `job_title` VARCHAR(255) NOT NULL,
                `normalized_title` VARCHAR(255) NOT NULL,
                `segment` VARCHAR(64) NOT NULL,
                `is_required` TINYINT(1) NOT NULL,
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX `idx_norm_title` (`normalized_title`),
                INDEX `idx_req_norm` (`normalized_title`, `is_required`),
                UNIQUE KEY `uq_norm_segment` (`normalized_title`, `segment`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        print("2. Reading and parsing CSV rows...")
        rows_to_insert = []
        stats = {"required": 0, "not_required": 0, "total": 0}

        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                title = (r.get("title") or "").strip()
                segment = (r.get("segment") or "").strip()
                if not title:
                    continue

                norm_title = normalize_text(title)
                if not norm_title:
                    continue

                # Segment 1 & 2 -> Required (1), else Not Required (0)
                is_req = 1 if segment in REQUIRED_SEGMENTS else 0
                if is_req == 1:
                    stats["required"] += 1
                else:
                    stats["not_required"] += 1
                stats["total"] += 1

                rows_to_insert.append((
                    title[:255],
                    norm_title[:255],
                    segment[:64],
                    is_req,
                ))

        print(f"3. Inserting {len(rows_to_insert)} titles into MySQL (Batch size: 2,000)...")
        batch_size = 2000
        sql = """
            INSERT INTO `job_title_guardrails` (`job_title`, `normalized_title`, `segment`, `is_required`)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `job_title` = VALUES(`job_title`),
                `is_required` = VALUES(`is_required`);
        """

        for i in range(0, len(rows_to_insert), batch_size):
            batch = rows_to_insert[i : i + batch_size]
            cur.executemany(sql, batch)
            print(f"   |-- Ingested {min(i + batch_size, len(rows_to_insert))}/{len(rows_to_insert)} rows...", flush=True)

        print("\n4. Verifying imported count in MySQL:")
        cur.execute("SELECT is_required, COUNT(*) FROM `job_title_guardrails` GROUP BY is_required;")
        for row in cur.fetchall():
            status_str = "REQUIRED (Prio 1 & 2 Segments)" if row[0] == 1 else "NOT REQUIRED (Prio 3 & 4 Segments)"
            print(f"   |-- {status_str}: {row[1]} titles")

        cur.execute("SELECT COUNT(*) FROM `job_title_guardrails`;")
        total_in_db = cur.fetchone()[0]
        print(f"\n✅ Total Rows in `job_title_guardrails` table: {total_in_db}")

    conn.close()

if __name__ == "__main__":
    import_job_titles_pipeline()
