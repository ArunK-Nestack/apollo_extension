import json
import os
import re

with open(r"config\apollo_accounts.json", "r", encoding="utf-8") as f:
    accounts = json.load(f)

with open(r"scratch\all_browser_discovered.json", "r", encoding="utf-8") as f:
    discovered = json.load(f)

print(f"Total Accounts: {len(accounts)}")
for idx, acc in enumerate(accounts, 1):
    email = acc["email"].lower()
    name = acc["name"]
    searches = discovered.get(email, [])
    print(f"[{idx:>2}] {acc['email']:<35} ({name}): {len(searches)} search visits")
