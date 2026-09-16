import os
import sqlite3
import shutil
import tempfile
import json

ff_base = os.path.expanduser('~') + r'\AppData\Roaming\Mozilla\Firefox\Profiles'

def rot_minus1(s):
    try:
        return "".join(chr(b - 1) for b in s)
    except:
        return ""

for prof in os.listdir(ff_base):
    p_dir = os.path.join(ff_base, prof)
    if not os.path.isdir(p_dir):
        continue
    
    # Check places.sqlite
    places_db = os.path.join(p_dir, 'places.sqlite')
    if os.path.exists(places_db):
        tmp = tempfile.mktemp()
        try:
            shutil.copy2(places_db, tmp)
            conn = sqlite3.connect(tmp)
            c = conn.cursor()
            rows = c.execute("SELECT url, title, datetime(last_visit_date/1000000, 'unixepoch') FROM moz_places WHERE url LIKE '%apollo.io%' OR url LIKE '%recruiting%' OR title LIKE '%recruiting%' ORDER BY last_visit_date DESC").fetchall()
            if rows:
                print(f"\n=== Firefox {prof} Places ({len(rows)} rows) ===")
                for u, t, dt in rows[:10]:
                    print(f"  [{dt}] {u[:100]} | {t}")
            conn.close()
        except Exception as e:
            pass
        finally:
            if os.path.exists(tmp): os.remove(tmp)

    # Check IndexedDB and localStorage
    storage_dir = os.path.join(p_dir, 'storage', 'default')
    if os.path.exists(storage_dir):
        for sub in os.listdir(storage_dir):
            if 'apollo' in sub:
                full_sub = os.path.join(storage_dir, sub)
                for root, dirs, files in os.walk(full_sub):
                    for f in files:
                        if f.endswith('.sqlite'):
                            db_path = os.path.join(root, f)
                            tmp = tempfile.mktemp()
                            try:
                                shutil.copy2(db_path, tmp)
                                conn = sqlite3.connect(tmp)
                                c = conn.cursor()
                                tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                                for t in tables:
                                    try:
                                        rows = c.execute(f"SELECT * FROM {t}").fetchall()
                                        for r in rows:
                                            for col in r:
                                                if isinstance(col, bytes):
                                                    dec = rot_minus1(col)
                                                    if 'finder' in dec or 'search' in dec or 'recruiting' in dec:
                                                        print(f"  [FF {prof} / {f} / {t}] ROT-1: {dec[:120]}")
                                    except Exception:
                                        pass
                                conn.close()
                            except Exception:
                                pass
                            finally:
                                if os.path.exists(tmp): os.remove(tmp)
