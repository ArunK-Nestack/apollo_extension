import sqlite3
import shutil
import os

p = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles\l7YKREgj.Profile 3\cookies.sqlite")
if os.path.exists(p):
    tmp = "scratch/ff_cookies.sqlite"
    shutil.copy2(p, tmp)
    conn = sqlite3.connect(tmp)
    cur = conn.cursor()
    cur.execute("SELECT name, value FROM moz_cookies WHERE host LIKE '%apollo.io%'")
    for r in cur.fetchall():
        print(r[0], r[1][:50])
    conn.close()
    os.remove(tmp)
