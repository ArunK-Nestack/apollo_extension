import requests
import json
import urllib.parse
import re

api_key = "q2TdVjnmRk52QSd_AqCiVg"
headers = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "X-Api-Key": api_key
}

with open(r"scratch\parsed_construction_search.json", "r", encoding="utf-8") as f:
    raw_data = json.load(f)

params = raw_data["parsed_params"]
payload = {}

for k, v in params.items():
    clean_k = k.replace("[]", "")
    snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
    if snake_k in ["page", "recommendation_config_id", "sort_ascending", "sort_by_field"]:
        continue
    payload[snake_k] = v if len(v) > 1 else v[0]

payload["page"] = 1
payload["per_page"] = 10

url = "https://api.apollo.io/api/v1/mixed_people/api_search"
res = requests.post(url, headers=headers, json=payload, timeout=20)
print(f"Status: {res.status_code}")
if res.status_code == 200:
    data = res.json()
    total = data.get("total_entries", 0)
    people = data.get("people", [])
    print(f"Total entries found: {total:,d}")
    print(f"Sample leads returned: {len(people)}")
    for p in people[:3]:
        org = p.get("organization") or {}
        print(f"  • {p.get('name')} | {p.get('title')} | {org.get('name')} | {org.get('primary_domain')}")
else:
    print(f"Error: {res.text[:300]}")
