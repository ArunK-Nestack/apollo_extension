import sqlite3
import urllib.parse
import json

conn = sqlite3.connect(r"scratch\profile16_history.sqlite")
cur = conn.cursor()
cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%66c5da1ba9d74501b3a8df93%' ORDER BY last_visit_time DESC LIMIT 1")
row = cur.fetchone()

if row:
    url = row[0]
    parsed = urllib.parse.urlparse(url)
    fragment = parsed.fragment
    qs = fragment.split("?", 1)[1] if "?" in fragment else parsed.query
    params = urllib.parse.parse_qs(qs)
    
    with open(r"scratch\parsed_construction_search.json", "w", encoding="utf-8") as f:
        json.dump({
            "visit": row[2],
            "title": row[1],
            "raw_url": url,
            "parsed_params": params
        }, f, indent=2)
    print("Successfully wrote scratch/parsed_construction_search.json")
