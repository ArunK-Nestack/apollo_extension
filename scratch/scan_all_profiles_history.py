import os
import sqlite3
import shutil
import urllib.parse
import json

base_chrome = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

with open(os.path.join(base_chrome, "Local State"), "r", encoding="utf-8") as f:
    local_state = json.load(f)

profiles_info = local_state.get("profile", {}).get("info_cache", {})

discovered_searches = {}

for p_dir, info in profiles_info.items():
    user_email = (info.get("user_name") or "").lower()
    hist_path = os.path.join(base_chrome, p_dir, "History")
    if not os.path.exists(hist_path):
        continue
    
    tmp_hist = f"scratch\\hist_{p_dir}.sqlite"
    try:
        shutil.copy2(hist_path, tmp_hist)
        conn = sqlite3.connect(tmp_hist)
        cur = conn.cursor()
        cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo.io/#/people?%' ORDER BY last_visit_time DESC")
        rows = cur.fetchall()
        conn.close()
        try: os.remove(tmp_hist)
        except: pass
        
        account_searches = []
        seen_keys = set()
        for url, title, visit in rows:
            parsed = urllib.parse.urlparse(url)
            fragment = parsed.fragment
            qs = fragment.split("?", 1)[1] if "?" in fragment else parsed.query
            params = urllib.parse.parse_qs(qs)
            
            rec_id = params.get("recommendationConfigId", [""])[0]
            tags = tuple(sorted(params.get("qOrganizationKeywordTags[]", [])))
            if not tags and not rec_id:
                continue
            
            key = rec_id or str(tags[:5])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            
            account_searches.append({
                "profile": p_dir,
                "email": user_email,
                "visit": visit,
                "rec_id": rec_id,
                "tags_count": len(tags),
                "tags": list(tags),
                "url": url,
                "params": params
            })
        
        if account_searches:
            discovered_searches[user_email or p_dir] = account_searches
    except Exception as e:
        print(f"Error checking {p_dir}: {e}")

print(f"Discovered searches across {len(discovered_searches)} accounts:")
for acc, s_list in discovered_searches.items():
    print(f"\n[{acc}] has {len(s_list)} creator search configurations:")
    for s in s_list:
        print(f"  • Visit: {s['visit']} | RecID: {s['rec_id']} | Tags: {s['tags_count']} tags | URL len: {len(s['url'])}")

with open(r"scratch\all_discovered_searches.json", "w", encoding="utf-8") as f:
    json.dump(discovered_searches, f, indent=2)
print("\nSaved to scratch/all_discovered_searches.json")
