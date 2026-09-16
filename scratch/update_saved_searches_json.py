import json
import re

with open(r"scratch\all_discovered_searches.json", "r", encoding="utf-8") as f:
    discovered = json.load(f)

saved_searches = {}

def clean_params(params):
    payload = {}
    for k, v in params.items():
        clean_k = k.replace("[]", "")
        snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
        if snake_k in ["page", "recommendation_config_id", "sort_ascending", "sort_by_field", "q_keywords"]:
            continue
        payload[snake_k] = v if len(v) > 1 else v[0]
    return payload

# 1. rahul@nestack.co.in
with open(r"scratch\parsed_construction_search.json", "r", encoding="utf-8") as f:
    c_data = json.load(f)

c_payload = clean_params(c_data["parsed_params"])

saved_searches["rahul@nestack.co.in"] = [
    {
        "name": "Construction Services Regular",
        "display_name": "Construction Services Regular",
        "description": "Commercial & Residential Construction, Infrastructure, Scaffolding, Restoration (US, NZ, AU, SG)",
        "recommendation_config_id": "66c5da1ba9d74501b3a8df93",
        "filters": c_payload
    }
]

# Add other accounts from discovered
account_names_map = {
    "vijay@nestacktech.com": "Commercial Fleet & Automotive Services",
    "vijay.raghavan@nestack.com": "Insurance & Financial Services",
    "abel.abraham@nestacktechnologies.com": "Industrial Manufacturing & Equipment",
    "rahul.chandran@nestack-tech.com": "Real Estate Services & Development",
    "rahul@nestacktechnology.com": "Media, Advertising & PR Services",
    "jith@nestack.info": "Industrial Contracting & Supplies"
}

for email, searches in discovered.items():
    if email == "rahul@nestack.co.in" or not email.endswith("@nestack.com") and not "@nestack" in email:
        continue
    
    # Pick the richest search (most tags)
    best_search = max(searches, key=lambda s: s.get("tags_count", 0))
    if best_search.get("tags_count", 0) > 0:
        cleaned = clean_params(best_search.get("params", {}))
        s_name = account_names_map.get(email, f"Creator Search ({best_search['tags_count']} Tags)")
        saved_searches[email] = [
            {
                "name": s_name,
                "display_name": s_name,
                "description": f"Targeted Creator Search ({best_search['tags_count']} Industry Tags)",
                "recommendation_config_id": best_search.get("rec_id", ""),
                "filters": cleaned
            }
        ]

# Add default fallback
saved_searches["default"] = saved_searches["rahul@nestack.co.in"]

with open(r"config\saved_searches.json", "w", encoding="utf-8") as f:
    json.dump(saved_searches, f, indent=2)

print(f"Updated config/saved_searches.json with {len(saved_searches)} accounts.")
for acc, s_list in saved_searches.items():
    print(f"  • {acc}: {[s['name'] for s in s_list]}")
