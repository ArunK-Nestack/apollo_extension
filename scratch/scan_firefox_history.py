import os
import sqlite3
import shutil
import urllib.parse
import json

ff_base = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles")
print("Scanning Firefox profiles for Apollo history...")

for p_dir in os.listdir(ff_base):
    full_p = os.path.join(ff_base, p_dir)
    places = os.path.join(full_p, "places.sqlite")
    if os.path.exists(places):
        tmp = f"scratch\\ff_hist_{p_dir}.sqlite"
        try:
            shutil.copy2(places, tmp)
            conn = sqlite3.connect(tmp)
            cur = conn.cursor()
            cur.execute("SELECT url, title, datetime(last_visit_date/1000000, 'unixepoch') FROM moz_places WHERE url LIKE '%apollo.io%' ORDER BY last_visit_date DESC LIMIT 50")
            rows = cur.fetchall()
            conn.close()
            os.remove(tmp)
            if rows:
                print(f"\n[Firefox Profile: {p_dir}] has {len(rows)} Apollo URLs:")
                for u, t, v in rows[:5]:
                    print(f"  Visited: {v} | Title: {t}")
                    print(f"  URL: {u[:120]}...")
        except Exception as e:
            print(f"Error checking {p_dir}: {e}")
