"""Create run-level enrichment audit and non-destructive lead decision tables."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api import (
    ensure_enrichment_run_audit_table,
    ensure_lead_enrichment_state_table,
    get_connection,
)


def migrate() -> None:
    with get_connection() as conn:
        ensure_enrichment_run_audit_table(conn)
        ensure_lead_enrichment_state_table(conn)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM `enrichment_run_audit`")
            audit_columns = [row[0] for row in cur.fetchall()]
            cur.execute("SHOW COLUMNS FROM `lead_enrichment_state`")
            state_columns = [row[0] for row in cur.fetchall()]

    print("Created/verified `enrichment_run_audit`: " + ", ".join(audit_columns))
    print("Created/verified `lead_enrichment_state`: " + ", ".join(state_columns))


if __name__ == "__main__":
    migrate()
