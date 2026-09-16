import sqlite3
import shutil
import tempfile
import os

files = [
    r'C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 7\History',
    r'C:\Users\test\AppData\Local\Microsoft\Edge\User Data\Default\History',
    r'C:\Users\test\AppData\Local\Microsoft\Edge\User Data\Profile 4\History',
    r'C:\Users\test\AppData\Roaming\Mozilla\Firefox\Profiles\ywDxpe5A.Profile 2\places.sqlite'
]

for fp in files:
    print('=== Checking:', fp)
    tmp = tempfile.mktemp()
    try:
        shutil.copy2(fp, tmp)
        conn = sqlite3.connect(tmp)
        c = conn.cursor()
        if 'places.sqlite' in fp:
            rows = c.execute("SELECT url, title FROM moz_places WHERE url LIKE '%app.apollo.io%'").fetchall()
        else:
            rows = c.execute("SELECT url, title FROM urls WHERE url LIKE '%app.apollo.io%'").fetchall()
        print(f'  Found {len(rows)} rows:')
        for u, t in rows:
            print(f'    URL: {u}')
            print(f'    Title: {t}')
        conn.close()
    except Exception as e:
        print('  Error:', e)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
