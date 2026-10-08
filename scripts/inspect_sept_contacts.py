import os
import requests
import json
from datetime import datetime
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

# Check page 1 to 5 of All Contacts sorted created_at desc to see the rate of creation
r = requests.get(
    f"{domain}/api/contacts/view/12001258277",
    headers=headers,
    params={"sort": "created_at", "sort_type": "desc", "per_page": 25},
    timeout=20
)
if r.ok:
    data = r.json()
    contacts = data.get("contacts", [])
    print(f"Total contacts on page: {len(contacts)}")
    for c in contacts[:10]:
        custom = c.get("custom_field", {})
        login_owner = custom.get("cf_apollo_login_owner") or custom.get("apollo_login_owner")
        print(f"ID: {c.get('id')} | Name: {c.get('first_name')} {c.get('last_name')} | Created: {c.get('created_at')} | Owner: {login_owner} | Tags: {c.get('tags')}")
        
    print(f"Oldest on page: {contacts[-1].get('created_at')}")
else:
    print("Error:", r.status_code, r.text[:200])
