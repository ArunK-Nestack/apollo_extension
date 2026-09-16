import sys
sys.path.insert(0, ".")
from backend.api import get_connection
import json

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT raw_enrichment_data FROM apollo_saved_leads WHERE batch='rchandran_nestack_biz' AND raw_enrichment_data IS NOT NULL LIMIT 1")
        row = cur.fetchone()
        if row and row[0]:
            d = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            print("Keys:", list(d.keys()) if isinstance(d, dict) else type(d))
            if isinstance(d, dict):
                print("Lists:", d.get("contact_campaign_statuses") or d.get("labels") or d.get("typed_custom_fields"))
                for k in ["keywords", "title", "name", "organization"]:
                    if k in d:
                        print(f"  {k}:", str(d[k])[:100])
