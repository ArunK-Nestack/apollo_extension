import os
import sqlite3
import shutil
import tempfile

temp_dir = tempfile.gettempdir()
edge_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")

if os.path.exists(edge_dir):
    for p in os.listdir(edge_dir):
        p_path = os.path.join(edge_dir, p)
        if not os.path.isdir(p_path):
            continue
        cookies_path = os.path.join(p_path, "Network", "Cookies")
        if os.path.exists(cookies_path):
            tmp = os.path.join(temp_dir, f"edge_cookie_{p}.sqlite")
            try:
                shutil.copy2(cookies_path, tmp)
                conn = sqlite3.connect(tmp)
                cur = conn.cursor()
                cur.execute("SELECT name, host_key FROM cookies WHERE host_key LIKE '%apollo.io%'")
                rows = cur.fetchall()
                if rows:
                    print(f"[Edge {p}] Has {len(rows)} Apollo cookies: {[r[0] for r in rows[:6]]}")
                conn.close()
            except Exception:
                pass
