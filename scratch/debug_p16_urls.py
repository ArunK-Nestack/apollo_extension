import sqlite3
import os
import urllib.parse
import tempfile
import shutil

temp_dir = tempfile.gettempdir()
hp = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\History"
tmp = os.path.join(temp_dir, "hist_p16_debug.sqlite")
shutil.copy2(hp, tmp)
conn = sqlite3.connect(tmp)
cur = conn.cursor()
cur.execute("SELECT url FROM urls WHERE url LIKE '%apollo.io/#/people%'")
rows = cur.fetchall()
print(f"Total people URLs in p16: {len(rows)}")
for r in rows[:15]:
    print(r[0][:200])
conn.close()
