import sqlite3
import shutil
import tempfile
import os
import re

def rot_minus1(s):
    try:
        return "".join(chr(b - 1) for b in s)
    except:
        return ""

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
                            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                            for t in tables:
                                try:
                                    rows = c.execute(f"SELECT * FROM {t}").fetchall()
                                    for r in rows:
                                        # inspect every column
                                        for col in r:
                                            if isinstance(col, bytes):
                                                # Check raw bytes for text or view
                                                text = col.decode('utf-8', errors='ignore')
                                                # Look for finder view names or search names
                                                if 'name' in text and ('modality' in text or 'people' in text or 'view' in text):
                                                    print(f"[{prof} / {f} / {t}] Found match in raw bytes:")
                                                    print(text[:300])
                                                    print("=" * 60)
                                                
                                                # Also check decoded rot-1
                                                dec = rot_minus1(col)
                                                if any(k in dec for k in ['finder', 'people', 'search', 'view', 'title', 'filter']):
                                                    print(f"[{prof} / {f} / {t}] ROT-1 decoded match:")
                                                    print(dec[:300])
                                                    print("=" * 60)
                                except Exception:
                                    pass
                            conn.close()
                        except Exception as e:
                            pass
                        finally:
                            if os.path.exists(tmp):
                                os.remove(tmp)
