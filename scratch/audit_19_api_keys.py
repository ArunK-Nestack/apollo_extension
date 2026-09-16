import json
import requests

with open(r"config\apollo_accounts.json", "r", encoding="utf-8") as f:
    accounts = json.load(f)

print(f"Auditing {len(accounts)} Apollo API keys...")
for acc in accounts:
    key = acc.get("api_key", "")
    email = acc.get("email", "")
    headers = {"Content-Type": "application/json", "X-Api-Key": key}
    try:
        # Check health and user
        r_user = requests.get("https://api.apollo.io/api/v1/users/search", headers=headers, timeout=6)
        user_name = ""
        user_id = ""
        if r_user.status_code == 200:
            users = r_user.json().get("users", [])
            if users:
                user_name = f"{users[0].get('first_name', '')} {users[0].get('last_name', '')}".strip()
                user_id = users[0].get("id", "")
        print(f"[{acc['id']:>2}] {email:<35} | Status: {r_user.status_code} | User: {user_name} (ID: {user_id})")
    except Exception as e:
        print(f"[{acc['id']:>2}] {email:<35} | Error: {e}")
