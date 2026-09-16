import os
import re
import json

chrome_base = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

with open(os.path.join(chrome_base, "Local State"), "r", encoding="utf-8") as f:
    ls = json.load(f)

profile_info = ls.get("profile", {}).get("info_cache", {})
p_map = {p: (info.get("user_name") or "").lower() for p, info in profile_info.items()}

all_views = {}

for p_dir in os.listdir(chrome_base):
    full_p = os.path.join(chrome_base, p_dir)
    idb_dir = os.path.join(full_p, "IndexedDB", "https_app.apollo.io_0.indexeddb.leveldb")
    if not os.path.exists(idb_dir):
        continue
    
    email = p_map.get(p_dir, p_dir)
    print(f"\nChecking IndexedDB for {p_dir} ({email})...")
    
    views_found = []
    for f in os.listdir(idb_dir):
        if f.endswith((".ldb", ".log")):
            fp = os.path.join(idb_dir, f)
            try:
                with open(fp, "rb") as bfp:
                    data = bfp.read()
                
                # Search for json with name and modality
                for m in re.finditer(rb'"name"\s*:\s*"([^"]+)"[^{}]*?"modality"\s*:\s*"people"', data):
                    name = m.group(1).decode("utf-8", errors="ignore")
                    idx = m.start()
                    chunk = data[max(0, idx - 50):min(len(data), idx + 2000)]
                    text = "".join(chr(c) if 32 <= c <= 126 else " " for c in chunk)
                    views_found.append({"name": name, "text": text[:300]})
                
                # Search for finder-recent-view
                for m in re.finditer(rb'finder-(?:recent-)?view-people-[a-f0-9]+', data):
                    idx = m.start()
                    chunk = data[max(0, idx - 50):min(len(data), idx + 2000)]
                    text = "".join(chr(c) if 32 <= c <= 126 else " " for c in chunk)
                    # Look for "name"
                    name_m = re.search(r'"name"\s*:\s*"([^"]+)"', text)
                    if name_m:
                        views_found.append({"name": name_m.group(1), "text": text[:300]})
            except Exception:
                pass
    
    # Deduplicate
    unique = {}
    for v in views_found:
        if v["name"] not in unique:
            unique[v["name"]] = v
            print(f"  [Found View] {v['name']}")
    
    if unique:
        all_views[email] = list(unique.values())

with open(r"scratch\all_idb_views.json", "w", encoding="utf-8") as f:
    json.dump(all_views, f, indent=2)

print("\nFinished scan. Summary of views found:")
for em, vs in all_views.items():
    print(f"  {em}: {[v['name'] for v in vs]}")
