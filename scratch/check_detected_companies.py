import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.api import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM detected_companies WHERE created_at >= '2026-09-15 18:15:00'")
        print('Inserted since 18:15 (during recruiting search):', cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM detected_companies WHERE created_at >= '2026-09-15 12:00:00'")
        print('Inserted since 12:00 today:', cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM detected_companies WHERE created_at >= '2026-09-15 00:00:00'")
        print('Inserted all of today (Sep 15):', cur.fetchone()[0])
