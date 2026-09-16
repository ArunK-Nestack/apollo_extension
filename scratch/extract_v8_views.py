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
    found_names = set()
    
    for f in os.listdir(idb_dir):
        if f.endswith((".ldb", ".log")):
            fp = os.path.join(idb_dir, f)
            try:
                with open(fp, "rb") as bfp:
                    data = bfp.read()
                
                # Search for b'name"' in V8 format
                pos = 0
                while True:
                    idx = data.find(b'name"', pos)
                    if idx == -1:
                        break
                    pos = idx + 5
                    
                    # Look for string following name"
                    # In V8 serializer: name" followed by string content
                    sub = data[pos:pos+100]
                    # Find printable string up to next special char or quote
                    # Try regex on sub
                    m = re.match(rb'[\x00-\x20]*"?([a-zA-Z0-9 _\-\/\.,\(\)]+)"?', sub)
                    if m:
                        candidate = m.group(1).decode("utf-8", errors="ignore").strip()
                        # Verify nearby has "people" or "finder" or "modality" or "userId"
                        surrounding = data[max(0, idx - 100):min(len(data), idx + 400)]
                        if any(w in surrounding for w in [b"modality", b"people", b"finder", b"starredBy", b"userId"]):
                            if len(candidate) > 3 and not candidate.startswith("http"):
                                found_names.add(candidate)
            except Exception:
                pass
    
    if found_names:
        print(f"[{p_dir}] {email}:")
        for n in sorted(found_names):
            print(f"   * {n}")
        all_views[email] = list(found_names)

with open(r"scratch\all_v8_views.json", "w", encoding="utf-8") as f:
    json.dump(all_views, f, indent=2)
