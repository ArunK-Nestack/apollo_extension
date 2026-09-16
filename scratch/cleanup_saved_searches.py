import json
import os

with open("config/saved_searches.json", "r", encoding="utf-8") as f:
    current = json.load(f)

# Load discovered browser searches
with open(r"scratch\all_browser_discovered.json", "r", encoding="utf-8") as f:
    all_discovered = json.load(f)

def clean_params(params):
    import re
    payload = {}
    for k, v in params.items():
        clean_k = k.replace("[]", "")
        snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
        if snake_k in ["page", "recommendation_config_id", "sort_ascending", "sort_by_field", "q_keywords"]:
            continue
        payload[snake_k] = v if len(v) > 1 else v[0]
    return payload

cleaned_searches = {}

# 1. rahul@nestack.co.in (The 4 verified searches from user's screenshot)
cleaned_searches["rahul@nestack.co.in"] = current.get("rahul@nestack.co.in", [])

# 2. vijay@nestacktech.com (Exact names from LevelDB: 'Automotive' and '*CHRO/HR MANAGER OTHERS/IT/NA usa/aus/nz/sg')
v4_raw = all_discovered.get("vijay@nestacktech.com", [])
v4_searches = []
if v4_raw:
    s_auto = next((s for s in v4_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 66), None)
    if s_auto:
        v4_searches.append({
            "name": "Automotive",
            "display_name": "Automotive",
            "filters": clean_params(s_auto["params"])
        })
    s_chro = next((s for s in v4_raw if s.get("params", {}).get("personTitles[]")), None)
    if s_chro:
        v4_searches.append({
            "name": "*CHRO/HR MANAGER OTHERS/IT/NA usa/aus/nz/sg",
            "display_name": "*CHRO/HR MANAGER OTHERS/IT/NA usa/aus/nz/sg",
            "filters": clean_params(s_chro["params"])
        })
cleaned_searches["vijay@nestacktech.com"] = v4_searches

# 3. jith@nestack.info (Exact name from LevelDB: 'CSO/CPO OTHERS ')
jith_raw = all_discovered.get("jith@nestack.info", [])
jith_searches = []
if jith_raw:
    s_cso = next((s for s in jith_raw if s.get("params", {}).get("personTitles[]")), None)
    if s_cso:
        jith_searches.append({
            "name": "CSO/CPO OTHERS",
            "display_name": "CSO/CPO OTHERS",
            "filters": clean_params(s_cso["params"])
        })
cleaned_searches["jith@nestack.info"] = jith_searches

# 4. rahul.chandran@nestack-tech.com (Exact name from LevelDB: 'real estate saas')
rc_raw = all_discovered.get("rahul.chandran@nestack-tech.com", [])
rc_searches = []
if rc_raw:
    s_re = next((s for s in rc_raw if "real estate" in str(s.get("params", {}).get("qOrganizationKeywordTags[]", [])).lower()), None)
    if s_re:
        rc_searches.append({
            "name": "real estate saas",
            "display_name": "real estate saas",
            "filters": clean_params(s_re["params"])
        })
cleaned_searches["rahul.chandran@nestack-tech.com"] = rc_searches

# 5. rahul@nestaktechnology.com / rahul@nestacktechnology.com (Exact name from LevelDB: 'Advertising, Marketing & Communications-regular')
p17_raw = all_discovered.get("Profile 17", [])
r17_searches = []
if p17_raw:
    s_ad = next((s for s in p17_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 99), None)
    if s_ad:
        r17_searches.append({
            "name": "Advertising, Marketing & Communications-regular",
            "display_name": "Advertising, Marketing & Communications-regular",
            "filters": clean_params(s_ad["params"])
        })
cleaned_searches["rahul@nestaktechnology.com"] = r17_searches
cleaned_searches["rahul@nestacktechnology.com"] = r17_searches

with open("config/saved_searches.json", "w", encoding="utf-8") as f:
    json.dump(cleaned_searches, f, indent=2)

print("Saved cleaned authentic searches to config/saved_searches.json:")
for k, v in cleaned_searches.items():
    print(f"  {k}: {[s['name'] for s in v]}")
