import sqlite3
import shutil
import tempfile
import os

tmp = os.path.join(tempfile.gettempdir(), 'hist_p8.sqlite')
shutil.copy2(r'C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 8\History', tmp)
conn = sqlite3.connect(tmp)
cur = conn.cursor()
cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo%' ORDER BY last_visit_time DESC LIMIT 40")
rows = cur.fetchall()
print(f"Apollo visits in Profile 8: {len(rows)}")
for r in rows:
    print(r[2], r[1], r[0][:120])

cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls ORDER BY last_visit_time DESC LIMIT 15")
rows = cur.fetchall()
print(f"\nGeneral visits in Profile 8:")
for r in rows:
    print(r[2], r[1], r[0][:120])
conn.close()
