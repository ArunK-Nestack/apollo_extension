import os
import re
import json
import sqlite3
import shutil
import urllib.parse

chrome_base = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

# Load local state to map profiles to user emails
with open(os.path.join(chrome_base, "Local State"), "r", encoding="utf-8") as f:
    ls = json.load(f)

profile_info = ls.get("profile", {}).get("info_cache", {})
profile_to_email = {}
for p, info in profile_info.items():
    em = (info.get("user_name") or "").lower()
    if em:
        profile_to_email[p] = em

print("Profile to Email Mapping:")
for p, em in profile_to_email.items():
    print(f"  {p} -> {em}")

# Load the 19 accounts
with open(r"config\apollo_accounts.json", "r", encoding="utf-8") as f:
    accounts = json.load(f)

account_emails = [a["email"].lower() for a in accounts]

discovered_searches = {}

# Helper to clean params
def clean_params(params):
    payload = {}
    for k, v in params.items():
        clean_k = k.replace("[]", "")
        snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
        if snake_k in ["page", "recommendation_config_id", "sort_ascending", "sort_by_field", "q_keywords"]:
            continue
        payload[snake_k] = v if len(v) > 1 else v[0]
    return payload

# Process each profile
for p_dir, email in profile_to_email.items():
    full_p = os.path.join(chrome_base, p_dir)
    print(f"\n=======================================================")
    print(f"Processing {p_dir} ({email})...")
    print(f"=======================================================")
    
    searches_for_email = {}
    
    # 1. Check IndexedDB leveldb for saved view names
    idb_dir = os.path.join(full_p, "IndexedDB", "https_app.apollo.io_0.indexeddb.leveldb")
    if os.path.exists(idb_dir):
        for f in os.listdir(idb_dir):
            if f.endswith((".ldb", ".log")):
                fp = os.path.join(idb_dir, f)
                try:
                    with open(fp, "rb") as bfp:
                        content = bfp.read()
                        # Find all "name" "..." "modality" "people"
                        # Or regex for "name":"..." with modality people
                        matches = re.finditer(rb'"id"\s*:\s*"([a-f0-9]{24})"[^}]{0,200}"name"\s*:\s*"([^"]+)"', content)
                        for m in matches:
                            s_id = m.group(1).decode()
                            s_name = m.group(2).decode("utf-8", errors="ignore")
                            if s_name not in searches_for_email:
                                searches_for_email[s_name] = {"id": s_id, "name": s_name, "source": "IndexedDB"}
                                print(f"  [IndexedDB View] {s_name} (ID: {s_id})")
                except Exception as e:
                    pass
    
    # 2. Check Chrome History for People search URLs
    hist_path = os.path.join(full_p, "History")
    if os.path.exists(hist_path):
        tmp_h = f"scratch\\hist_{p_dir}.sqlite"
        try:
            shutil.copy2(hist_path, tmp_h)
            conn = sqlite3.connect(tmp_h)
            cur = conn.cursor()
            cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo.io/#/people?%' ORDER BY last_visit_time DESC")
            rows = cur.fetchall()
            conn.close()
            os.remove(tmp_h)
            
            for url, title, visit in rows:
                parsed = urllib.parse.urlparse(url)
                fragment = parsed.fragment
                qs = fragment.split("?", 1)[1] if "?" in fragment else parsed.query
                params = urllib.parse.parse_qs(qs)
                
                tags = params.get("qOrganizationKeywordTags[]", [])
                titles = params.get("personTitles[]", [])
                locs = params.get("personLocations[]", [])
                rec_id = params.get("recommendationConfigId", [""])[0]
                finder_id = params.get("finderViewId", [""])[0]
                
                if not tags and not titles and not locs and not rec_id:
                    continue
                
                # Check if this matches a known name, or create a descriptive name
                key = rec_id or finder_id or (tuple(sorted(tags[:5])), tuple(sorted(titles[:3])))
                
                # Let's clean the parameters
                cleaned = clean_params(params)
                
                # Try to name it intelligently
                name = None
                # Check if title or any tag gives a name
                if tags:
                    tag_summary = tags[0].title()
                    if len(tags) > 1:
                        tag_summary += f" & {tags[1].title()}"
                    name = f"{tag_summary} ({len(tags)} Tags)"
                elif titles:
                    name = f"{titles[0]} Search"
                elif rec_id:
                    name = f"Saved Search {rec_id[:8]}"
                else:
                    name = f"Custom Search"
                
                # Save if distinct
                s_key = str(key)
                if s_key not in searches_for_email:
                    searches_for_email[s_key] = {
                        "name": name,
                        "display_name": name,
                        "visit": visit,
                        "rec_id": rec_id,
                        "finder_id": finder_id,
                        "filters": cleaned,
                        "tags_count": len(tags),
                        "titles_count": len(titles),
                        "locations": locs,
                        "url": url
                    }
                    print(f"  [History Search] {name} (Visited: {visit}) - {len(tags)} tags, {len(titles)} titles")
        except Exception as e:
            print(f"  Error reading history for {p_dir}: {e}")

    discovered_searches[email] = searches_for_email

with open(r"scratch\all_profiles_extracted_searches.json", "w", encoding="utf-8") as f:
    json.dump(discovered_searches, f, indent=2)

print("\nDumped all extracted searches to scratch/all_profiles_extracted_searches.json")
