import json
import requests
import re

api_key = "q2TdVjnmRk52QSd_AqCiVg"
headers = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "X-Api-Key": api_key
}

with open(r"scratch\extracted_4_searches.json", "r", encoding="utf-8") as f:
    extracted = json.load(f)

def clean_params(params):
    payload = {}
    for k, v in params.items():
        clean_k = k.replace("[]", "")
        snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
        if snake_k in ["page", "recommendation_config_id", "sort_ascending", "sort_by_field", "q_keywords"]:
            continue
        payload[snake_k] = v if len(v) > 1 else v[0]
    return payload

tested_searches = []

# Exact display names matching the user's screenshot
order_and_names = [
    ("Construction Services-saas", "Construction Services-saas"),
    ("Construction service-Regular", "Construction service-Regular"),
    ("Director IT/Others/NA EST", "Director IT/Others/NA EST"),
    ("construction services regular 2", "construction services regular 2")
]

for key, display_name in order_and_names:
    raw = extracted.get(key)
    if not raw:
        continue
    cleaned_filters = clean_params(raw["params"])
    
    # Test with Apollo API
    probe_payload = dict(cleaned_filters)
    probe_payload["page"] = 1
    probe_payload["per_page"] = 1
    
    url = "https://api.apollo.io/api/v1/mixed_people/api_search"
    res = requests.post(url, headers=headers, json=probe_payload, timeout=25)
    total = 0
    if res.status_code == 200:
        total = res.json().get("total_entries", 0)
    print(f"'{display_name}' -> Total Available: {total:,d} leads (Status: {res.status_code})")
    
    tested_searches.append({
        "name": display_name,
        "display_name": display_name,
        "recommendation_config_id": raw.get("rec_id", ""),
        "last_visit": raw.get("visit", ""),
        "filters": cleaned_filters
    })

# Load existing saved_searches.json to preserve other accounts
with open(r"config\saved_searches.json", "r", encoding="utf-8") as f:
    saved_config = json.load(f)

saved_config["rahul@nestack.co.in"] = tested_searches
saved_config["default"] = tested_searches

with open(r"config\saved_searches.json", "w", encoding="utf-8") as f:
    json.dump(saved_config, f, indent=2)

print("\nSuccessfully updated config/saved_searches.json with all 4 Creator Searches!")
