import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pymysql
from backend.api import get_connection

print("--- SEARCHING DB FOR 668 ---")
with get_connection() as conn:
    with conn.cursor() as cur:
        # Check counts in all tables
        cur.execute("SHOW TABLES;")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM `{t}`;")
                cnt = cur.fetchone()[0]
                if cnt in (668, 1200) or abs(cnt - 668) < 10:
                    print(f"MATCH TABLE COUNT: {t} -> {cnt}")
            except Exception:
                pass
                
        # Check apollo_saved_leads batches
        cur.execute("SELECT batch, count(*) FROM apollo_saved_leads GROUP BY batch;")
        for r in cur.fetchall():
            if r[1] in (668, 1200) or abs(r[1] - 668) < 20:
                print(f"MATCH BATCH COUNT: {r[0]} -> {r[1]}")

print("\n--- SEARCHING CSV FILES FOR 668 ROWS ---")
for root, dirs, files in os.walk("."):
    for f in files:
        if f.endswith(".csv"):
            p = os.path.join(root, f)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fl:
                    lines = sum(1 for _ in fl)
                    if lines in (668, 669, 1200, 1201) or abs(lines - 668) < 10:
                        print(f"MATCH CSV FILE: {p} -> {lines} lines")
            except Exception:
                pass
