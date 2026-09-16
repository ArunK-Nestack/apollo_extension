import os
import sqlite3
import shutil
import urllib.parse
import json
import re

accounts_path = r"config\apollo_accounts.json"
with open(accounts_path, "r", encoding="utf-8") as f:
    accounts = json.load(f)

account_emails = [acc["email"].lower() for acc in accounts]
print(f"Loaded {len(account_emails)} target accounts from {accounts_path}.")

discovered_by_account = {e: [] for e in account_emails}

def clean_params(params):
    payload = {}
    for k, v in params.items():
        clean_k = k.replace("[]", "")
        snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
        if snake_k in ["page", "recommendation_config_id", "sort_ascending", "sort_by_field", "q_keywords"]:
            continue
        payload[snake_k] = v if len(v) > 1 else v[0]
    return payload

def parse_apollo_url_to_search(url, title, visit, source_info):
    if "apollo.io/#/people?" not in url and "apollo.io/people?" not in url:
        return None
    parsed = urllib.parse.urlparse(url)
    fragment = parsed.fragment
    qs = fragment.split("?", 1)[1] if "?" in fragment else parsed.query
    params = urllib.parse.parse_qs(qs)
    
    tags = params.get("qOrganizationKeywordTags[]", [])
    titles = params.get("personTitles[]", [])
    locs = params.get("personLocations[]", [])
    rec_id = params.get("recommendationConfigId", [""])[0]
    finder_id = params.get("finderViewId", [""])[0]
    
    # Must have some substantial search criteria
    if not tags and not titles and not locs and not rec_id and not finder_id:
        return None
    
    return {
        "url": url,
        "title": title,
        "visit": visit,
        "source": source_info,
        "rec_id": rec_id,
        "finder_id": finder_id,
        "tags": tags,
        "titles": titles,
        "locations": locs,
        "params": params
    }

# =========================================================================
# 1. SCAN CHROME PROFILES
# =========================================================================
chrome_base = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"
if os.path.exists(chrome_base):
    # Map profile to user email
    local_state_path = os.path.join(chrome_base, "Local State")
    p_map = {}
    if os.path.exists(local_state_path):
        with open(local_state_path, "r", encoding="utf-8") as f:
            ls = json.load(f)
        for p, info in ls.get("profile", {}).get("info_cache", {}).items():
            u = (info.get("user_name") or "").lower()
            if u:
                p_map[p] = u
    
    for p_dir in os.listdir(chrome_base):
        full_p = os.path.join(chrome_base, p_dir)
        if not os.path.isdir(full_p):
            continue
        mapped_email = p_map.get(p_dir, "")
        
        # History
        hist_path = os.path.join(full_p, "History")
        if os.path.exists(hist_path):
            tmp = f"scratch\\tmp_chrome_{p_dir}.sqlite"
            try:
                shutil.copy2(hist_path, tmp)
                conn = sqlite3.connect(tmp)
                cur = conn.cursor()
                cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo.io%people?%' ORDER BY last_visit_time DESC")
                rows = cur.fetchall()
                conn.close()
                os.remove(tmp)
                for url, title, visit in rows:
                    res = parse_apollo_url_to_search(url, title, visit, f"Chrome/{p_dir}")
                    if res:
                        # If profile maps to email, assign it
                        if mapped_email in discovered_by_account:
                            discovered_by_account[mapped_email].append(res)
                        else:
                            # Try to see if URL or context has email
                            discovered_by_account.setdefault(p_dir, []).append(res)
            except Exception as e:
                pass

# =========================================================================
# 2. SCAN FIREFOX PROFILES
# =========================================================================
ff_base = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles")
if os.path.exists(ff_base):
    for p_dir in os.listdir(ff_base):
        full_p = os.path.join(ff_base, p_dir)
        places = os.path.join(full_p, "places.sqlite")
        if os.path.exists(places):
            tmp = f"scratch\\tmp_ff_{p_dir}.sqlite"
            try:
                shutil.copy2(places, tmp)
                conn = sqlite3.connect(tmp)
                cur = conn.cursor()
                cur.execute("SELECT url, title, datetime(last_visit_date/1000000, 'unixepoch') FROM moz_places WHERE url LIKE '%apollo.io%people?%' ORDER BY last_visit_date DESC")
                rows = cur.fetchall()
                conn.close()
                os.remove(tmp)
                for url, title, visit in rows:
                    res = parse_apollo_url_to_search(url, title, visit, f"Firefox/{p_dir}")
                    if res:
                        discovered_by_account.setdefault(f"Firefox_{p_dir}", []).append(res)
            except Exception as e:
                pass

# =========================================================================
# 3. SCAN EDGE PROFILES
# =========================================================================
edge_base = r"C:\Users\test\AppData\Local\Microsoft\Edge\User Data"
if os.path.exists(edge_base):
    for p_dir in os.listdir(edge_base):
        full_p = os.path.join(edge_base, p_dir)
        hist = os.path.join(full_p, "History")
        if os.path.exists(hist):
            tmp = f"scratch\\tmp_edge_{p_dir}.sqlite"
            try:
                shutil.copy2(hist, tmp)
                conn = sqlite3.connect(tmp)
                cur = conn.cursor()
                cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo.io%people?%' ORDER BY last_visit_time DESC")
                rows = cur.fetchall()
                conn.close()
                os.remove(tmp)
                for url, title, visit in rows:
                    res = parse_apollo_url_to_search(url, title, visit, f"Edge/{p_dir}")
                    if res:
                        discovered_by_account.setdefault(f"Edge_{p_dir}", []).append(res)
            except Exception as e:
                pass

print("\nScan Results Summary:")
for k, searches in discovered_by_account.items():
    if searches:
        print(f"[{k}] found {len(searches)} search visits")

with open(r"scratch\all_browser_discovered.json", "w", encoding="utf-8") as f:
    json.dump(discovered_by_account, f, indent=2)
print("Saved raw discovery to scratch/all_browser_discovered.json")
