import sqlite3
import shutil
import os

temp_cookies = r"scratch\profile16_cookies.sqlite"
orig_cookies = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\Network\Cookies"

shutil.copy2(orig_cookies, temp_cookies)
conn = sqlite3.connect(temp_cookies)
cur = conn.cursor()
cur.execute("SELECT host_key, name, path, expires_utc FROM cookies WHERE host_key LIKE '%apollo.io%'")
rows = cur.fetchall()
print(f"Total cookies for apollo.io: {len(rows)}")
for r in rows:
    print(f"Host: {r[0]} | Name: {r[1]}")
conn.close()
os.remove(temp_cookies)
