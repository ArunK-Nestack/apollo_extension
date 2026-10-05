"""Index successful enrichment domains for extension matching."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api import get_connection


def migrate() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = 'batch_enrichment_ledger'
                     AND INDEX_NAME = 'idx_company_domain_outcome'"""
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "ALTER TABLE `batch_enrichment_ledger` "
                    "ADD INDEX `idx_company_domain_outcome` (`company_domain`, `outcome`)"
                )
        conn.commit()


if __name__ == "__main__":
    migrate()
