import json
import requests

with open("config/apollo_accounts.json", "r", encoding="utf-8") as f:
    accounts = json.load(f)

# Account 6 is Rahul (rahul@nestack.co.in) who has the 4 saved searches from the screenshot!
acc6 = next(a for a in accounts if "rahul@nestack.co.in" in a["email"].lower())
key = acc6["api_key"]
headers = {"Content-Type": "application/json", "X-Api-Key": key}

endpoints = [
    ("GET", "https://api.apollo.io/api/v1/saved_searches"),
    ("GET", "https://api.apollo.io/api/v1/finder_views"),
    ("GET", "https://api.apollo.io/api/v1/views"),
    ("GET", "https://api.apollo.io/api/v1/searches"),
    ("GET", "https://api.apollo.io/api/v1/saved_search_filters"),
    ("GET", "https://api.apollo.io/api/v1/people_views"),
    ("GET", "https://api.apollo.io/api/v1/contacts/search_views"),
    ("GET", "https://api.apollo.io/api/v1/labels"),
    ("GET", "https://api.apollo.io/api/v1/tags"),
    ("GET", "https://api.apollo.io/api/v1/contact_roles"),
    ("GET", "https://api.apollo.io/api/v1/recommendation_configs"),
    ("GET", "https://api.apollo.io/api/v1/contact_stages"),
    ("POST", "https://api.apollo.io/api/v1/saved_searches/search"),
    ("POST", "https://api.apollo.io/api/v1/finder_views/search"),
]

for method, ep in endpoints:
    try:
        if method == "GET":
            r = requests.get(ep, headers=headers, timeout=5)
        else:
            r = requests.post(ep, headers=headers, json={}, timeout=5)
        print(f"{method} {ep} -> HTTP {r.status_code} | {r.text[:120]}")
    except Exception as e:
        print(f"{method} {ep} -> Error: {e}")
