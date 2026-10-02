"""Seed definite Indian surnames from Excel into `indian_name_guardrails`."""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api import INDIAN_SURNAMES_XLSX, ensure_indian_surnames_seeded, get_connection, seed_indian_surnames_from_excel


def import_indian_surnames_pipeline(xlsx_path: str | None = None) -> None:
    path = xlsx_path or INDIAN_SURNAMES_XLSX
    if not os.path.isfile(path):
        print(f"Error: Excel not found at {path}")
        return

    print("=" * 70)
    print(">>> [INDIAN SURNAMES PIPELINE] Ingesting Excel into MySQL")
    print(f"    Source: {path}")
    print("=" * 70)

    with get_connection() as conn:
        inserted = seed_indian_surnames_from_excel(conn, path)
        ensure_indian_surnames_seeded(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM `indian_name_guardrails` WHERE `entry_kind` = 'surname'"
            )
            total = int(cur.fetchone()[0] or 0)

    print(f"\nUpserted {inserted} row(s) from Excel.")
    print(f"Total surname rows in `indian_name_guardrails`: {total}")


if __name__ == "__main__":
    custom = sys.argv[1] if len(sys.argv) > 1 else None
    import_indian_surnames_pipeline(custom)
