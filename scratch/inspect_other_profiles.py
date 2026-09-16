import os
import sqlite3
import shutil
import tempfile
import json

# Check Edge Profiles 1, 2, 3
edge_base = os.path.expanduser('~') + r'\AppData\Local\Microsoft\Edge\User Data'
for p in ['Profile 1', 'Profile 2', 'Profile 3']:
    p_dir = os.path.join(edge_base, p)
    if not os.path.exists(p_dir):
        continue
    pref_file = os.path.join(p_dir, 'Preferences')
    if os.path.exists(pref_file):
        try:
            with open(pref_file, 'r', encoding='utf-8', errors='ignore') as f:
                data = json.load(f)
                name_prof = data.get('profile', {}).get('name', '')
                print(f'Edge {p}: name="{name_prof}"')
        except Exception as e:
            pass

# Check Firefox Profiles
ff_base = os.path.expanduser('~') + r'\AppData\Roaming\Mozilla\Firefox\Profiles'
if os.path.exists(ff_base):
    for p in os.listdir(ff_base):
        p_dir = os.path.join(ff_base, p)
        # Check logins.json or cookies.sqlite or formhistory.sqlite
        cookies_db = os.path.join(p_dir, 'cookies.sqlite')
        form_db = os.path.join(p_dir, 'formhistory.sqlite')
        logins_json = os.path.join(p_dir, 'logins.json')
        print(f'FF Profile: {p}')
        if os.path.exists(logins_json):
            try:
                with open(logins_json, 'r') as f:
                    ld = json.load(f)
                    logins = ld.get('logins', [])
                    for lg in logins:
                        print(f'   login hostname: {lg.get("hostname")}, user: {lg.get("encryptedUsername")}')
            except Exception:
                pass
        if os.path.exists(form_db):
            tmp = tempfile.mktemp()
            try:
                shutil.copy2(form_db, tmp)
                conn = sqlite3.connect(tmp)
                c = conn.cursor()
                rows = c.execute("SELECT fieldname, value FROM moz_formhistory WHERE value LIKE '%nestack%' OR value LIKE '%apollo%'").fetchall()
                for fn, val in rows:
                    print(f'   form: {fn} = {val}')
                conn.close()
            except Exception as e:
                pass
            finally:
                if os.path.exists(tmp): os.remove(tmp)
