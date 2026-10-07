"""
View Page Activity Logs
=======================
Inspects MySQL `extension_activity_log` to view per-page metrics (Required, Not Required,
Existing in CRM, Guardrail Rejected, Total) and inspect per-contact evaluation details.

Usage:
    python scripts/view_page_logs.py
    python scripts/view_page_logs.py --batch my_batch
    python scripts/view_page_logs.py --page 2 --details
    python scripts/view_page_logs.py --saved-leads
"""

import argparse
import json
import sys

sys.path.insert(0, ".")
from backend.api import get_connection, ensure_extension_activity_log_table


def view_logs(batch=None, page=None, limit=50, show_details=False):
    with get_connection() as conn:
        ensure_extension_activity_log_table(conn)
        with conn.cursor() as cur:
            query = """
                SELECT id, event_type, account_email, cycle_tag, batch, page_number,
                       total_on_page, required_on_page, not_required_on_page,
                       existing_on_page, guardrail_rejected_on_page, collected_total,
                       created_at, breakdown_json
                FROM `extension_activity_log`
                WHERE 1=1
            """
            params = []
            if batch:
                query += " AND `batch` LIKE %s"
                params.append(f"%{batch}%")
            if page:
                query += " AND `page_number` = %s"
                params.append(page)

            query += " ORDER BY id DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, tuple(params))
            rows = cur.fetchall()

    if not rows:
        print("[!] No page logs found matching the criteria.")
        return

    print("=" * 110)
    print(f"{'TIME':19} | {'EVENT':14} | {'PAGE':5} | {'BATCH':15} | {'TOTAL':5} | {'REQ':4} | {'NOT REQ':7} | {'EXIST':5} | {'REJ':4} | {'CUMULATIVE':10}")
    print("-" * 110)

    for r in reversed(rows):
        (log_id, event_type, account, cycle, b_name, pg,
         tot, req, not_req, exist, rej, collected, created, b_json) = r
        
        t_str = str(created)[:19]
        b_disp = (b_name or "")[:15]
        pg_disp = str(pg if pg is not None else "-")
        tot_disp = str(tot or 0)
        req_disp = str(req or 0)
        not_req_disp = str(not_req or 0)
        exist_disp = str(exist or 0)
        rej_disp = str(rej or 0)
        coll_disp = str(collected if collected is not None else "-")

        print(f"{t_str:19} | {event_type:14} | {pg_disp:5} | {b_disp:15} | {tot_disp:5} | {req_disp:4} | {not_req_disp:7} | {exist_disp:5} | {rej_disp:4} | {coll_disp:10}")

        if show_details and b_json:
            try:
                items = json.loads(b_json) if isinstance(b_json, str) else b_json
                if isinstance(items, list) and items:
                    print(f"\n   >>> Page {pg_disp} Contact Breakdown ({len(items)} contacts):")
                    for idx, item in enumerate(items, 1):
                        st = item.get("status", "")
                        symbol = "🟢" if st == "REQUIRED" else ("🔵" if st == "EXISTING" else "⚪")
                        nm = item.get("name", "")[:24]
                        title = item.get("title", "")[:30]
                        comp = item.get("company", "")[:20]
                        reason = item.get("reason", "")
                        print(f"      {idx:2}. {symbol} [{st:12}] {nm:24} | {title:30} | {comp:20} | {reason}")
                    print("   " + "-" * 105)
            except Exception as e:
                print(f"      [Error parsing breakdown_json: {e}]")

    print("=" * 110)


def view_saved_leads_per_page(batch=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT `batch`, `page_number`, COUNT(*) as cnt
                FROM `apollo_saved_leads`
                WHERE 1=1
            """
            params = []
            if batch:
                query += " AND `batch` LIKE %s"
                params.append(f"%{batch}%")
            query += " GROUP BY `batch`, `page_number` ORDER BY `batch`, `page_number`"
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

    if not rows:
        print("[!] No saved leads found.")
        return

    print("=" * 70)
    print("SAVED LEADS BY PAGE (FROM `apollo_saved_leads`):")
    print("-" * 70)
    print(f"{'BATCH':30} | {'PAGE NUMBER':15} | {'SAVED LEADS':12}")
    print("-" * 70)
    for b_name, pg, cnt in rows:
        pg_str = str(pg if pg is not None else "(Not Specified)")
        print(f"{str(b_name)[:30]:30} | {pg_str:15} | {cnt:12}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="View Apollo extension per-page activity logs.")
    parser.add_argument("--batch", type=str, default=None, help="Filter by batch name")
    parser.add_argument("--page", type=int, default=None, help="Filter by page number")
    parser.add_argument("--limit", type=int, default=50, help="Max log records to fetch")
    parser.add_argument("--details", action="store_true", help="Print contact-level details for each page")
    parser.add_argument("--saved-leads", action="store_true", help="Show saved leads count grouped by page")
    args = parser.parse_args()

    if args.saved_leads:
        view_saved_leads_per_page(batch=args.batch)
    else:
        view_logs(batch=args.batch, page=args.page, limit=args.limit, show_details=args.details)


if __name__ == "__main__":
    main()
