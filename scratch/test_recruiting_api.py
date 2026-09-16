import requests
import json

key = "bF9xb0EUc1WlP5vhY4pxpA"
headers = {"Content-Type": "application/json", "X-Api-Key": key}

endpoints = [
    "https://api.apollo.io/api/v1/auth/health",
    "https://api.apollo.io/api/v1/users/search",
    "https://api.apollo.io/api/v1/labels",
    "https://api.apollo.io/api/v1/contact_stages",
    "https://api.apollo.io/api/v1/email_accounts",
    "https://api.apollo.io/api/v1/emailer_campaigns/search",
    "https://api.apollo.io/api/v1/sequences/search",
]

for ep in endpoints:
    try:
        r = requests.get(ep, headers=headers, timeout=10)
        print(f"GET {ep} -> {r.status_code}")
        if r.status_code == 200:
            print("  ", r.text[:300])
    except Exception as e:
        print(f"Error {ep}: {e}")
