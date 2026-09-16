import os
import sqlite3
import shutil
import tempfile

temp_dir = tempfile.gettempdir()
chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

for p in os.listdir(chrome_dir):
    p_path = os.path.join(chrome_dir, p)
    if not os.path.isdir(p_path):
        continue
    # Check Network/Cookies
    cookies_path = os.path.join(p_path, "Network", "Cookies")
    if os.path.exists(cookies_path):
        tmp = os.path.join(temp_dir, f"cookie_{p}.sqlite")
        try:
            shutil.copy2(cookies_path, tmp)
            conn = sqlite3.connect(tmp)
            cur = conn.cursor()
            cur.execute("SELECT name, host_key FROM cookies WHERE host_key LIKE '%apollo.io%'")
            rows = cur.fetchall()
            if rows:
                print(f"[{p}] Has {len(rows)} Apollo cookies: {[r[0] for r in rows[:6]]}")
            conn.close()
        except Exception:
            pass
