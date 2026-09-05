import os
import time
import pymysql
from dotenv import load_dotenv

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER', 'nestack'),
    password=os.getenv('DB_PASSWORD', ''),
    database=os.getenv('DB_NAME', 'apollo_scrapers')
)

t0 = time.time()
print("Fetching distinct domains from MySQL...")
with conn.cursor() as cur:
    cur.execute("SELECT DISTINCT domain FROM emails WHERE domain != ''")
    rows = cur.fetchall()

os.makedirs("data", exist_ok=True)
cache_path = os.path.join("data", "domain_slugs_cache.txt")
with open(cache_path, "w", encoding="utf-8") as f:
    for (dom,) in rows:
        if dom:
            f.write(dom.strip().lower() + "\n")

print(f"Saved {len(rows):,} unique domains to {cache_path} in {time.time() - t0:.2f}s!")
