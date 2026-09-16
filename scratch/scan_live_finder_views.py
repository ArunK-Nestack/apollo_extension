import os
import re
import json

chrome_base = os.path.expanduser('~') + r'\AppData\Local\Google\Chrome\User Data'

# Get mapping from profile to email
profile_map = {}
for p in os.listdir(chrome_base):
    p_dir = os.path.join(chrome_base, p)
    if not os.path.isdir(p_dir):
        continue
    pref_path = os.path.join(p_dir, 'Preferences')
    if os.path.exists(pref_path):
        try:
            with open(pref_path, 'r', encoding='utf-8', errors='ignore') as f:
                pref = json.load(f)
                email = pref.get('account_info', [{}])[0].get('email') or pref.get('profile', {}).get('name')
                if email and '@' in email:
                    profile_map[p] = email.lower()
        except Exception:
            pass

print(f"Discovered {len(profile_map)} Chrome Profiles with Google/Apollo accounts:")
for p, em in sorted(profile_map.items()):
    print(f"  {p:<12} -> {em}")

# Scan IndexedDB for each profile
results = {}
v8_pattern = re.compile(rb'"\x02id"\x18([a-f0-9]{24})"\x04name"([\x01-\x7f])([^\x00]{1,120})')

for p, em in profile_map.items():
    idb_dir = os.path.join(chrome_base, p, 'IndexedDB', 'https_app.apollo.io_0.indexeddb.leveldb')
    if not os.path.exists(idb_dir):
        continue
    results[em] = {}
    for f in os.listdir(idb_dir):
        if not f.endswith(('.ldb', '.log')):
            continue
        fp = os.path.join(idb_dir, f)
        try:
            with open(fp, 'rb') as fl:
                data = fl.read()
                for vm in v8_pattern.finditer(data):
                    vid = vm.group(1).decode()
                    nlen = ord(vm.group(2))
                    vname = vm.group(3)[:nlen].decode('utf-8', errors='ignore')
                    if vname not in ['Default view', 'People Auto-Score', 'Scoring v2 Autogen', 'Companies Auto-Score']:
                        results[em][vname] = vid
        except Exception as e:
            pass

print("\n" + "=" * 80)
print("LIVE FINDER VIEWS DISCOVERED PER LOGIN:")
print("=" * 80)
for em, views in sorted(results.items()):
    if views:
        print(f"\nAccount: {em} ({len(views)} saved searches)")
        for name, vid in views.items():
            print(f"  • {name} [ID: {vid}]")
