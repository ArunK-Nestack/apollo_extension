import sqlite3
import shutil
import tempfile
import os

base = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles")
p = "ywDxpe5A.Profile 2"
places = os.path.join(base, p, "places.sqlite")
tmp = os.path.join(tempfile.gettempdir(), "ff_p2_places.sqlite")
shutil.copy2(places, tmp)
conn = sqlite3.connect(tmp)
cur = conn.cursor()
cur.execute("SELECT url, title, datetime(last_visit_date/1000000, 'unixepoch') FROM moz_places WHERE url LIKE '%apollo.io%' ORDER BY last_visit_date DESC")
rows = cur.fetchall()
print(f"Profile 2 has {len(rows)} Apollo URLs:")
for u, t, v in rows:
    print(f"[{v}] {t} -> {u[:150]}")
conn.close()
