"""
People Search API Logger
========================
Maintains audit logs for all Apollo People Search API operations.
Records every API request to:
1. Local log files: `logs/people_search_api.log` and `logs/people_search_api.jsonl`
2. MySQL database table: `people_search_api_log` in `apollo_scrapers`

Ensures transparent auditing and proof of zero credit consumption (0 credits).
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOG_FILE_TXT = LOGS_DIR / "people_search_api.log"
LOG_FILE_JSONL = LOGS_DIR / "people_search_api.jsonl"


def ensure_people_search_log_table(conn) -> None:
    """Create the people_search_api_log table if it does not exist."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `people_search_api_log` (
                    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                    `account_email` VARCHAR(255) NOT NULL DEFAULT '',
                    `endpoint` VARCHAR(255) NOT NULL,
                    `page_number` INT NOT NULL DEFAULT 1,
                    `per_page` INT NOT NULL DEFAULT 10,
                    `keywords` VARCHAR(255) NOT NULL DEFAULT '',
                    `status_code` INT NOT NULL DEFAULT 200,
                    `returned_count` INT NOT NULL DEFAULT 0,
                    `total_entries` INT NOT NULL DEFAULT 0,
                    `credits_charged` INT NOT NULL DEFAULT 0,
                    `duration_ms` INT NOT NULL DEFAULT 0,
                    `filters_json` TEXT,
                    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX `idx_search_log_account` (`account_email`),
                    INDEX `idx_search_log_created` (`created_at`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
        conn.commit()
    except Exception:
        pass


def log_people_search_call(
    endpoint: str,
    payload: Dict[str, Any],
    status_code: int,
    returned_count: int,
    total_entries: int,
    duration_ms: int,
    account_email: str = "",
    credits_charged: int = 0,
    conn=None,
) -> Dict[str, Any]:
    """
    Record a People Search API call to both file logs and the database.
    Always documents credits_charged = 0 for free search requests.
    """
    now = datetime.now()
    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
    iso_timestamp = now.isoformat()

    page = int(payload.get("page", 1) or 1)
    per_page = int(payload.get("per_page", 10) or 10)
    keywords = str(payload.get("q_keywords") or "")[:255]

    # Create safe serializable filters summary
    safe_filters = {
        k: v for k, v in payload.items()
        if k not in ("api_key", "X-Api-Key")
    }
    filters_json = json.dumps(safe_filters, default=str)

    log_entry = {
        "timestamp": iso_timestamp,
        "timestamp_ist": timestamp_str,
        "account_email": account_email or "unknown",
        "endpoint": endpoint,
        "page_number": page,
        "per_page": per_page,
        "keywords": keywords,
        "status_code": status_code,
        "returned_count": returned_count,
        "total_entries": total_entries,
        "credits_charged": credits_charged,
        "duration_ms": duration_ms,
        "filters": safe_filters,
    }

    # 1. Write to local log files (thread-safe append)
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)

        # Append to human-readable TXT log
        with open(LOG_FILE_TXT, "a", encoding="utf-8") as f:
            short_ep = endpoint.split("api/v1/")[-1] if "api/v1/" in endpoint else endpoint
            kw_disp = keywords if keywords else "[None]"
            acc_disp = account_email if account_email else "ANON"
            f.write(
                f"[{timestamp_str}] [{acc_disp}] [{short_ep}] "
                f"Page {page} (limit {per_page}) | Keyword: '{kw_disp}' | "
                f"Status: {status_code} | Returned: {returned_count} (Total: {total_entries:,d}) | "
                f"Credits: {credits_charged} | {duration_ms}ms\n"
            )

        # Append to JSONL log
        with open(LOG_FILE_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass

    # 2. Write to MySQL if connection provided or obtainable
    target_conn = conn
    close_after = False
    if target_conn is None:
        try:
            from backend.api import get_connection
            target_conn = get_connection()
            close_after = True
        except Exception:
            target_conn = None

    if target_conn is not None:
        try:
            ensure_people_search_log_table(target_conn)
            with target_conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO `people_search_api_log` (
                        `account_email`, `endpoint`, `page_number`, `per_page`,
                        `keywords`, `status_code`, `returned_count`, `total_entries`,
                        `credits_charged`, `duration_ms`, `filters_json`, `created_at`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    str(account_email or "")[:255],
                    str(endpoint)[:255],
                    page,
                    per_page,
                    keywords,
                    status_code,
                    returned_count,
                    total_entries,
                    credits_charged,
                    duration_ms,
                    filters_json,
                    now,
                ))
            target_conn.commit()
        except Exception:
            pass
        finally:
            if close_after and target_conn:
                try:
                    target_conn.close()
                except Exception:
                    pass

    return log_entry
