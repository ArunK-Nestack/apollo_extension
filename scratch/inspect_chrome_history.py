import sqlite3
import shutil
import os

temp_hist = r"scratch\profile16_history.sqlite"
orig_hist = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\History"

os.makedirs("scratch", exist_ok=True)
shutil.copy2(orig_hist, temp_hist)
conn = sqlite3.connect(temp_hist)
cur = conn.cursor()
cur.execute("SELECT url, title, visit_count FROM urls WHERE url LIKE '%apollo.io%' ORDER BY last_visit_time DESC LIMIT 100")
rows = cur.fetchall()
print(f"Total Apollo URLs in Profile 16 history: {len(rows)}")
for url, title, count in rows:
    print(f"\nURL: {url}\nTitle: {title} (visits: {count})")
