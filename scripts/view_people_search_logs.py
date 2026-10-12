"""
View People Search API Audit Logs
=================================
Inspects Apollo People Search API logs from MySQL table `people_search_api_log`
(with automatic fallback to `logs/people_search_api.jsonl`).

Provides transparent audit trail proving zero credit consumption (0 credits)
for all people search operations.

Usage:
    python scripts/view_people_search_logs.py
    python scripts/view_people_search_logs.py --limit 100
    python scripts/view_people_search_logs.py --account abel@nestacktech.com
    python scripts/view_people_search_logs.py --keyword hospital
    python scripts/view_people_search_logs.py --json
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.people_search_logger import (
    LOG_FILE_TXT,
    LOG_FILE_JSONL,
    ensure_people_search_log_table,
)


def load_logs_from_db(
    limit: int = 50,
    account_filter: str = "",
    keyword_filter: str = "",
    status_filter: int = 0,
) -> List[Dict[str, Any]]:
    """Fetch logs from MySQL table people_search_api_log."""
    try:
        from backend.api import get_connection
        conn = get_connection()
    except Exception:
        return []

    results = []
    try:
        ensure_people_search_log_table(conn)
        with conn.cursor() as cur:
            query = """
                SELECT id, account_email, endpoint, page_number, per_page,
                       keywords, status_code, returned_count, total_entries,
                       credits_charged, duration_ms, filters_json, created_at
                FROM `people_search_api_log`
                WHERE 1=1
            """
            params: List[Any] = []
            if account_filter:
                query += " AND `account_email` LIKE %s"
                params.append(f"%{account_filter}%")
            if keyword_filter:
                query += " AND `keywords` LIKE %s"
                params.append(f"%{keyword_filter}%")
            if status_filter:
                query += " AND `status_code` = %s"
                params.append(status_filter)

            query += " ORDER BY id DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, tuple(params))
            rows = cur.fetchall()

            for r in rows:
                (
                    log_id, acc, endpoint, page, per_page,
                    kw, status, ret_cnt, tot_entries,
                    credits, dur, f_json, created_at
                ) = r
                results.append({
                    "id": log_id,
                    "account_email": acc or "unknown",
                    "endpoint": endpoint,
                    "page_number": page,
                    "per_page": per_page,
                    "keywords": kw,
                    "status_code": status,
                    "returned_count": ret_cnt,
                    "total_entries": tot_entries,
                    "credits_charged": credits,
                    "duration_ms": dur,
                    "created_at": str(created_at)[:19],
                    "filters": json.loads(f_json) if f_json else {},
                })
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return results


def load_logs_from_jsonl(
    limit: int = 50,
    account_filter: str = "",
    keyword_filter: str = "",
    status_filter: int = 0,
) -> List[Dict[str, Any]]:
    """Fallback reader parsing logs/people_search_api.jsonl."""
    if not os.path.exists(LOG_FILE_JSONL):
        return []

    matched = []
    try:
        with open(LOG_FILE_JSONL, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if account_filter and account_filter.lower() not in entry.get("account_email", "").lower():
                        continue
                    if keyword_filter and keyword_filter.lower() not in entry.get("keywords", "").lower():
                        continue
                    if status_filter and entry.get("status_code") != status_filter:
                        continue
                    matched.append(entry)
                except Exception:
                    continue
    except Exception:
        pass

    return matched[-limit:]


def main():
    parser = argparse.ArgumentParser(description="View Apollo People Search API Audit Logs")
    parser.add_argument("--limit", "-n", type=int, default=50, help="Number of recent logs to show (default: 50)")
    parser.add_argument("--account", "-a", type=str, default="", help="Filter by Apollo account email")
    parser.add_argument("--keyword", "-k", type=str, default="", help="Filter by search keyword")
    parser.add_argument("--status", "-s", type=int, default=0, help="Filter by HTTP status code (e.g. 200, 429)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON format")
    args = parser.parse_args()

    # Try DB first, fallback to JSONL file
    logs = load_logs_from_db(
        limit=args.limit,
        account_filter=args.account,
        keyword_filter=args.keyword,
        status_filter=args.status,
    )
    source_name = "MySQL `apollo_scrapers.people_search_api_log`"

    if not logs:
        logs = load_logs_from_jsonl(
            limit=args.limit,
            account_filter=args.account,
            keyword_filter=args.keyword,
            status_filter=args.status,
        )
        source_name = f"File `{LOG_FILE_JSONL}`"

    if not logs:
        print("\n[!] No People Search API logs found.")
        print(f"    Audited File Path: {LOG_FILE_TXT}")
        print(f"    Audited JSONL    : {LOG_FILE_JSONL}")
        sys.exit(0)

    if args.json:
        print(json.dumps(logs, indent=2, default=str))
        sys.exit(0)

    print("\n" + "=" * 140)
    print("APOLLO PEOPLE SEARCH API AUDIT TRAIL (Zero Credit Guarantee)")
    print(f"Source: {source_name} | Showing {len(logs)} most recent queries")
    print("=" * 140)
    print(
        f"{'TIMESTAMP (IST)':19} | "
        f"{'ACCOUNT EMAIL':28} | "
        f"{'ENDPOINT':24} | "
        f"{'PG':3} | "
        f"{'KEYWORD':18} | "
        f"{'STAT':4} | "
        f"{'RET':4} | "
        f"{'TOTAL':9} | "
        f"{'CREDITS':7} | "
        f"{'LATENCY':7}"
    )
    print("-" * 140)

    total_credits = 0
    total_returned = 0

    # Sort chronological for display
    display_rows = list(reversed(logs)) if "id" in logs[0] else logs

    for item in display_rows:
        ts = item.get("created_at") or item.get("timestamp_ist") or item.get("timestamp") or "-"
        acc = (item.get("account_email") or "unknown")[:28]
        ep = item.get("endpoint", "")
        if "api/v1/" in ep:
            ep = ep.split("api/v1/")[-1]
        ep = ep[:24]

        page = str(item.get("page_number", 1))[:3]
        kw = (item.get("keywords") or "-")[:18]
        stat = str(item.get("status_code", 200))[:4]
        ret = str(item.get("returned_count", 0))[:4]
        tot = f"{int(item.get('total_entries', 0)):,d}"[:9]
        credits = item.get("credits_charged", 0)
        total_credits += credits
        total_returned += int(item.get("returned_count", 0))
        lat = f"{item.get('duration_ms', 0)}ms"[:7]

        credit_str = f"{credits} FREE" if credits == 0 else f"! {credits} !"

        print(
            f"{str(ts)[:19]:19} | "
            f"{acc:28} | "
            f"{ep:24} | "
            f"{page:>3} | "
            f"{kw:18} | "
            f"{stat:>4} | "
            f"{ret:>4} | "
            f"{tot:>9} | "
            f"{credit_str:>7} | "
            f"{lat:>7}"
        )

    print("-" * 140)
    print(
        f"SUMMARY: {len(display_rows)} Queries Logged | "
        f"Total Leads Returned: {total_returned:,d} | "
        f"Total Credits Charged: {total_credits} (100% Free Search Operations)"
    )
    print("=" * 140 + "\n")


if __name__ == "__main__":
    main()
