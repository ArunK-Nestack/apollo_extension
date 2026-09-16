import sqlite3

conn = sqlite3.connect(r"scratch\profile16_history.sqlite")
cur = conn.cursor()
cur.execute("SELECT url, title FROM urls WHERE url LIKE '%apollo.io%people%'")
rows = cur.fetchall()
print(f"Total People URLs: {len(rows)}")
for url, title in rows:
    if any(k in url.lower() or k in (title or "").lower() for k in ["construct", "search", "view", "regular", "filter"]):
        print(f"TITLE: {title}\nURL: {url}\n")
    elif len(url) > 40:
        print(f"TITLE: {title}\nURL: {url}\n")
