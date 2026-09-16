import os
import sqlite3
import shutil
import tempfile

temp_dir = tempfile.gettempdir()
base = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles")

for p in os.listdir(base):
    apollo_dir = os.path.join(base, p, "storage", "default", "https+++app.apollo.io")
    if not os.path.exists(apollo_dir):
        continue
    print(f"\n==========================================")
    print(f"Firefox Profile: {p}")
    print(f"==========================================")
    
    # 1. Check LocalStorage (ls/data.sqlite)
    ls_db = os.path.join(apollo_dir, "ls", "data.sqlite")
    if os.path.exists(ls_db):
        tmp = os.path.join(temp_dir, f"ff_ls_{p}.sqlite")
        try:
            shutil.copy2(ls_db, tmp)
            conn = sqlite3.connect(tmp)
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM data")
            for k, v in cur.fetchall():
                k_str = k.decode('utf-8', errors='ignore') if isinstance(k, bytes) else str(k)
                v_str = v.decode('utf-8', errors='ignore') if isinstance(v, bytes) else str(v)
                print(f"  [LS Key]: {k_str[:60]}")
                if any(w in v_str.lower() for w in ['search', 'recruiting', 'view', 'people', 'filter', 'name']):
                    print(f"     Val: {v_str[:300]}")
            conn.close()
        except Exception as e:
            print(f"  Error reading LS: {e}")

    # 2. Check idb/*.sqlite
    idb_dir = os.path.join(apollo_dir, "idb")
    if os.path.exists(idb_dir):
        for f in os.listdir(idb_dir):
            if f.endswith(".sqlite"):
                tmp = os.path.join(temp_dir, f"ff_idb_{p}_{f}")
                try:
                    shutil.copy2(os.path.join(idb_dir, f), tmp)
                    conn = sqlite3.connect(tmp)
                    cur = conn.cursor()
                    # Check tables
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [r[0] for r in cur.fetchall()]
                    for tbl in tables:
                        try:
                            cur.execute(f"SELECT * FROM {tbl}")
                            rows = cur.fetchall()
                            for r in rows:
                                r_str = str(r)
                                if any(w in r_str for w in ['finder-recent-view', 'people-table-state', 'last-selected-view', 'recruiting@nestack.com', '6112b87dd137dd00a4c7833a']):
                                    print(f"  [IDB {f} -> {tbl}]: Found match! {r_str[:300]}")
                        except Exception:
                            pass
                    conn.close()
                except Exception as e:
                    print(f"  Error reading IDB {f}: {e}")
