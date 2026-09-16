import sqlite3
import shutil
import os

tmp = "scratch/ff_p3.sqlite"
p = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles\l7YKREgj.Profile 3\places.sqlite")
shutil.copy2(p, tmp)
conn = sqlite3.connect(tmp)
cur = conn.cursor()
cur.execute("SELECT url, title, datetime(last_visit_date/1000000, 'unixepoch') FROM moz_places WHERE url LIKE '%people?%'")
for r in cur.fetchall():
    print(r[2], r[1])
    print(" ", r[0])
conn.close()
os.remove(tmp)
