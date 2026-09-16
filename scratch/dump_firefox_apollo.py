import sqlite3
import shutil
import tempfile
import os
import json

ff_base = os.path.expanduser('~') + r'\AppData\Roaming\Mozilla\Firefox\Profiles'

for prof in os.listdir(ff_base):
    s_dir = os.path.join(ff_base, prof, 'storage', 'default')
    if not os.path.exists(s_dir):
        continue
    for sub in os.listdir(s_dir):
        if 'apollo' in sub:
            full_sub = os.path.join(s_dir, sub)
            for root, dirs, files in os.walk(full_sub):
                for f in files:
                    if f.endswith('.sqlite'):
                        db_path = os.path.join(root, f)
                        tmp = tempfile.mktemp()
                        try:
                            shutil.copy2(db_path, tmp)
                            conn = sqlite3.connect(tmp)
                            c = conn.cursor()
                            # Get tables
                            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                            for t in tables:
                                try:
                                    rows = c.execute(f"SELECT * FROM {t}").fetchall()
                                    # print info if interesting
                                    for r in rows:
                                        r_str = str(r)
                                        if any(k in r_str.lower() for k in ['search', 'view', 'people', 'filter', 'recruiting', 'madhava']):
                                            print(f"[{prof} / {f} / {t}]:")
                                            # Print snippet
                                            print(r_str[:500])
                                            print("-" * 40)
                                except Exception:
                                    pass
                            conn.close()
                        except Exception as e:
                            pass
                        finally:
                            if os.path.exists(tmp):
                                os.remove(tmp)
