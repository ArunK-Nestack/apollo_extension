import requests

key = "bF9xb0EUc1WlP5vhY4pxpA"
headers = {"Content-Type": "application/json", "X-Api-Key": key}

endpoints = [
    # Search / View endpoints
    "https://api.apollo.io/api/v1/saved_searches",
    "https://api.apollo.io/api/v1/finder_views",
    "https://api.apollo.io/api/v1/people/views",
    "https://api.apollo.io/api/v1/people/saved_searches",
    "https://api.apollo.io/api/v1/contacts/saved_searches",
    "https://api.apollo.io/api/v1/users/current",
    "https://api.apollo.io/api/v1/teams/current",
    "https://api.apollo.io/api/v1/account",
    "https://api.apollo.io/api/v1/user_preferences",
    "https://api.apollo.io/api/v1/user_preferences/current",
    "https://api.apollo.io/api/v1/configs",
    "https://api.apollo.io/api/v1/recommendation_configs",
    "https://api.apollo.io/api/v1/recommendations/presets",
    "https://api.apollo.io/api/v1/prospected_runs",
    "https://api.apollo.io/api/v1/analytics/dashboards",
    "https://api.apollo.io/api/v1/reports",
    "https://api.apollo.io/api/v1/exports",
    "https://api.apollo.io/api/v1/csv_exports",
    "https://api.apollo.io/api/v1/imports",
    "https://api.apollo.io/api/v1/contacts/search_views",
    # app.apollo.io domain
    "https://app.apollo.io/api/v1/users/current",
    "https://app.apollo.io/api/v1/user_preferences/current",
]

for ep in endpoints:
    try:
        r = requests.get(ep, headers=headers, timeout=5)
        print(f"GET {ep} -> {r.status_code}")
        if r.status_code == 200:
            print("   Response:", r.text[:300])
    except Exception as e:
        print(f"Error {ep}: {e}")
