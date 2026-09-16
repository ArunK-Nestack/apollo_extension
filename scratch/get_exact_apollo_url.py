import sqlite3

conn = sqlite3.connect(r"scratch\profile16_history.sqlite")
cur = conn.cursor()
cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%66c5da1ba9d74501b3a8df93%' ORDER BY last_visit_time DESC LIMIT 5")
rows = cur.fetchall()
for r in rows:
    print(f"VISIT: {r[2]}")
    print(f"TITLE: {r[1]}")
    print(f"URL: {r[0]}\n")
