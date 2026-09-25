import os
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path("freshsales_agent/.env"))
load_dotenv()

api_key = os.getenv("FRESHSALES_API_KEY", "DwI0oNI1eogOheMd2LAyRw")
domain = os.getenv("FRESHSALES_DOMAIN", "https://nestack.freshsales.io").rstrip("/")

headers = {
    "Authorization": f"Token token={api_key}",
    "Content-Type": "application/json",
}

# 1. Fetch owners / users from Freshsales
endpoints = [
    "api/selector/owners",
    "api/users",
    "api/settings/users"
]

for ep in endpoints:
    try:
        r = requests.get(f"{domain}/{ep}", headers=headers, timeout=15)
        print(f"Endpoint '{ep}' -> Status: {r.status_code}")
        if r.ok:
            data = r.json()
            print("  Type/Keys:", type(data), list(data.keys()) if isinstance(data, dict) else len(data))
            users = data.get("users") or data.get("owners") or (data if isinstance(data, list) else [])
            print(f"  Count: {len(users)}")
            for u in users[:15]:
                print(f"    ID: {u.get('id')}, Name: {u.get('name') or u.get('display_name')}, Email: {u.get('email')}")
            break
    except Exception as e:
        print(f"Error {ep}: {e}")

# 2. Check contact fields in Freshsales to see what fields exist
try:
    r = requests.get(f"{domain}/api/settings/contacts/fields", headers=headers, timeout=15)
    print("\nContact fields status:", r.status_code)
    if r.ok:
        fields = r.json().get("fields", [])
        print(f"Total contact fields: {len(fields)}")
        for f in fields:
            name = f.get("name")
            label = f.get("label")
            if any(k in name.lower() or k in label.lower() for k in ["login", "owner", "source", "user", "tag", "created", "account"]):
                print(f"  Field: name='{name}', label='{label}', type='{f.get('type')}'")
except Exception as e:
    print("Error getting contact fields:", e)
