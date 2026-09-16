import sqlite3
import shutil
import tempfile
import os

base_chrome = os.path.expanduser('~') + r'\AppData\Local\Google\Chrome\User Data'
base_edge = os.path.expanduser('~') + r'\AppData\Local\Microsoft\Edge\User Data'
base_ff = os.path.expanduser('~') + r'\AppData\Roaming\Mozilla\Firefox\Profiles'

all_histories = []
for b, name in [(base_chrome, 'Chrome'), (base_edge, 'Edge')]:
    if os.path.exists(b):
        for p in os.listdir(b):
            hp = os.path.join(b, p, 'History')
            if os.path.exists(hp):
                all_histories.append((f'{name} {p}', hp, False))

if os.path.exists(base_ff):
    for p in os.listdir(base_ff):
        hp = os.path.join(base_ff, p, 'places.sqlite')
        if os.path.exists(hp):
            all_histories.append((f'FF {p}', hp, True))

for label, hp, is_ff in all_histories:
    tmp = tempfile.mktemp()
    try:
        shutil.copy2(hp, tmp)
        conn = sqlite3.connect(tmp)
        c = conn.cursor()
        if is_ff:
            rows = c.execute("SELECT url, title FROM moz_places WHERE url LIKE '%app.apollo.io%'").fetchall()
        else:
            rows = c.execute("SELECT url, title FROM urls WHERE url LIKE '%app.apollo.io%'").fetchall()
        
        interesting = []
        for u, t in rows:
            if any(k in u for k in ['people', 'search', 'saved', 'view', 'finder', 'recommendationConfigId', 'finderViewId', 'qSearchListId', 'prospectedByCurrentTeam']):
                interesting.append((u, t))
        if interesting:
            print(f'=== {label} ({len(interesting)} interesting apollo URLs) ===')
            for u, t in interesting[:25]:
                print(f'  {u}')
                if t:
                    print(f'    Title: {t}')
        conn.close()
    except Exception as e:
        pass
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
