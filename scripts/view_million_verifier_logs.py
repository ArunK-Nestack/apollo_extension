#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
View MillionVerifier Verification Logs from MySQL
=================================================
Inspect runs recorded in `million_verifier_log` with counts for:
- Leads entered into MillionVerifier
- Good leads
- Discarded leads (risky + catch-all)
- Bad leads
- Freshsales CRM sync statistics
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api import get_connection, ensure_million_verifier_log_table


def main():
    with get_connection() as conn:
        ensure_million_verifier_log_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    `id`, `log_name`, `batch_name`, `cycle`,
                    `leads_entered`, `good_leads`, `discarded_leads`, `bad_leads`,
                    `crm_created`, `crm_updated`, `file_id`, `status`, `created_at`
                FROM `million_verifier_log`
                ORDER BY `id` ASC
            """)
            rows = cur.fetchall()

    print("\n" + "=" * 135)
    print("                              MILLION VERIFIER VERIFICATION LOG DATABASE")
    print("=" * 135)
    print(f"{'ID':<4} | {'Log Name / Login':<32} | {'Batch Name':<30} | {'Cycle':<16} | {'Entered':<7} | {'Good':<6} | {'Discard':<7} | {'Bad':<5} | {'CRM+':<5} | {'CRM Up':<6}")
    print("-" * 135)

    tot_entered = sum(r[4] for r in rows)
    tot_good = sum(r[5] for r in rows)
    tot_discard = sum(r[6] for r in rows)
    tot_bad = sum(r[7] for r in rows)
    tot_crm_new = sum(r[8] for r in rows)
    tot_crm_up = sum(r[9] for r in rows)

    for r in rows:
        rid, log_name, batch, cycle, entered, good, discard, bad, c_new, c_up = r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]
        print(f"{rid:<4} | {str(log_name or '')[:32]:<32} | {str(batch or '')[:30]:<30} | {str(cycle or ''):<16} | {entered:<7,d} | {good:<6,d} | {discard:<7,d} | {bad:<5,d} | {c_new:<5,d} | {c_up:<6,d}")

    print("-" * 135)
    print(f"{'TOTAL':<4} | {f'{len(rows)} batches':<32} | {'-':<30} | {'-':<16} | {tot_entered:<7,d} | {tot_good:<6,d} | {tot_discard:<7,d} | {tot_bad:<5,d} | {tot_crm_new:<5,d} | {tot_crm_up:<6,d}")
    print("=" * 135 + "\n")


if __name__ == "__main__":
    main()
