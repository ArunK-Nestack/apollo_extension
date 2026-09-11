"""Create batch_enrichment_ledger and backfill from apollo_saved_leads.enriched_at rows."""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import (
    backfill_enrichment_ledger_from_saved_leads,
    ensure_batch_enrichment_ledger_table,
    get_connection,
)


def migrate() -> None:
    print("=" * 70)
    print(">>> MIGRATING `batch_enrichment_ledger`")
    print("=" * 70)
    with get_connection() as conn:
        ensure_batch_enrichment_ledger_table(conn)
        conn.commit()
        print("  Table ready.")
        backfilled = backfill_enrichment_ledger_from_saved_leads(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM `batch_enrichment_ledger`")
            total = int(cur.fetchone()[0] or 0)
    print(f"  Backfill touched {backfilled} row(s). Ledger total: {total}")
    print("=" * 70)


if __name__ == "__main__":
    migrate()
