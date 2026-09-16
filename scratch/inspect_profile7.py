import sqlite3
import shutil
import tempfile
import os

hp = r'C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 7\History'
tmp = tempfile.mktemp()
try:
    shutil.copy2(hp, tmp)
    conn = sqlite3.connect(tmp)
    c = conn.cursor()
    rows = c.execute("SELECT url, title FROM urls WHERE url LIKE '%app.apollo.io%'").fetchall()
    print(f'Chrome Profile 7 Apollo rows: {len(rows)}')
    for u, t in rows:
        print(f'{u} | {t}')
    conn.close()
finally:
    if os.path.exists(tmp):
        os.remove(tmp)
