import requests

key = "bF9xb0EUc1WlP5vhY4pxpA"
headers = {"Content-Type": "application/json", "X-Api-Key": key}

endpoints = [
    "https://api.apollo.io/api/v1/people/views",
    "https://api.apollo.io/api/v1/people/saved_searches",
    "https://api.apollo.io/api/v1/contacts/saved_searches",
    "https://api.apollo.io/api/v1/contacts/search_views",
    "https://api.apollo.io/api/v1/user_preferences",
    "https://api.apollo.io/api/v1/user_preferences/current",
    "https://api.apollo.io/api/v1/users/current",
    "https://api.apollo.io/api/v1/teams/current",
]

for ep in endpoints:
    try:
        r = requests.get(ep, headers=headers, timeout=5)
        print(f"GET {ep} -> {r.status_code} | {r.text}")
    except Exception as e:
        print(f"Error {ep}: {e}")
