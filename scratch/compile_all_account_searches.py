import json
import os
import re
import urllib.parse

with open(r"config\apollo_accounts.json", "r", encoding="utf-8") as f:
    accounts = json.load(f)

with open(r"scratch\all_browser_discovered.json", "r", encoding="utf-8") as f:
    discovered = json.load(f)

with open(r"scratch\all_v8_views.json", "r", encoding="utf-8") as f:
    v8_views = json.load(f)

def clean_params(params):
    payload = {}
    for k, v in params.items():
        clean_k = k.replace("[]", "")
        snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
        if snake_k in ["page", "recommendation_config_id", "sort_ascending", "sort_by_field", "q_keywords"]:
            continue
        payload[snake_k] = v if len(v) > 1 else v[0]
    return payload

# Let's inspect each account
all_account_searches = {}

for acc in accounts:
    email = acc["email"].lower()
    name = acc["name"]
    raw_searches = discovered.get(email, [])
    
    account_searches = []
    seen_keys = set()
    
    for s in raw_searches:
        params = s.get("params", {})
        tags = params.get("qOrganizationKeywordTags[]", [])
        titles = params.get("personTitles[]", [])
        locs = params.get("personLocations[]", [])
        rec_id = s.get("rec_id", "")
        
        if not tags and not titles and not locs:
            continue
        
        # Unique signature of this search
        sig = (tuple(sorted(tags)), tuple(sorted(titles)), tuple(sorted(locs)))
        if sig in seen_keys:
            continue
        seen_keys.add(sig)
        
        # Name the search cleanly
        search_title = s.get("title", "")
        if "apollo" in search_title.lower():
            search_title = ""
        
        if tags:
            tag_name = tags[0].title()
            if len(tags) > 1:
                tag_name += f" & {tags[1].title()}"
            s_name = f"{tag_name}"
        elif titles:
            s_name = f"{', '.join(titles[:2])} Executive Search"
        else:
            s_name = f"Direct Search ({', '.join(locs[:2])})"
        
        account_searches.append({
            "name": s_name,
            "display_name": s_name,
            "recommendation_config_id": rec_id,
            "last_visit": s.get("visit", ""),
            "filters": clean_params(params)
        })
    
    all_account_searches[email] = account_searches
    print(f"[{acc['id']:>2}] {email:<35}: {len(account_searches)} unique searches")
    for s in account_searches[:3]:
        print(f"     • {s['name']}")

with open(r"scratch\compiled_account_searches.json", "w", encoding="utf-8") as f:
    json.dump(all_account_searches, f, indent=2)
