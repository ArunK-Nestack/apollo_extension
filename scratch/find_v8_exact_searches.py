import os
import re
import json

pattern = re.compile(rb'"\x02id"\x18([a-f0-9]{24})"\x04name"([\x01-\x7f])([^\x00]{1,120})"\x08modality"\x06people')

def scan_browser_data(base_path, browser_name):
    if not os.path.exists(base_path):
        return {}
    results = {}
    for p in os.listdir(base_path):
        p_dir = os.path.join(base_path, p)
        if not os.path.isdir(p_dir):
            continue
        # Check all IndexedDB directories in this profile
        idb_root = os.path.join(p_dir, "IndexedDB")
        if not os.path.exists(idb_root):
            continue
        for sub in os.listdir(idb_root):
            if "apollo" in sub.lower():
                full_db = os.path.join(idb_root, sub)
                for f in os.listdir(full_db):
                    if f.endswith((".ldb", ".log")):
                        fp = os.path.join(full_db, f)
                        try:
                            with open(fp, "rb") as f_in:
                                data = f_in.read()
                                for match in pattern.finditer(data):
                                    vid = match.group(1).decode()
                                    n_len = ord(match.group(2))
                                    raw_name = match.group(3)[:n_len]
                                    name = raw_name.decode("utf-8", errors="ignore")
                                    # Grab the userId if present after modality
                                    user_match = re.search(rb'"\x06userId"\x18([a-f0-9]{24})', data[match.start():match.start()+500])
                                    uid = user_match.group(1).decode() if user_match else "unknown"
                                    key = f"{browser_name} - {p}"
                                    results.setdefault(key, {})
                                    results[key][vid] = {
                                        "id": vid,
                                        "name": name,
                                        "user_id": uid,
                                        "file": f
                                    }
                        except Exception:
                            pass
    return results

chrome_results = scan_browser_data(r"C:\Users\test\AppData\Local\Google\Chrome\User Data", "Chrome")
edge_results = scan_browser_data(r"C:\Users\test\AppData\Local\Microsoft\Edge\User Data", "Edge")

all_res = {**chrome_results, **edge_results}

print(f"Total Profiles with Apollo Saved Searches: {len(all_res)}")
for prof, searches in all_res.items():
    print(f"\n[{prof}] ({len(searches)} saved searches):")
    for vid, s in searches.items():
        name_safe = s['name'].encode('ascii', errors='replace').decode()
        print(f"   * '{name_safe}' (ID: {vid}, UserID: {s['user_id']})")

with open(r"scratch\authentic_apollo_saved_views.json", "w", encoding="utf-8") as f:
    json.dump(all_res, f, indent=2)
