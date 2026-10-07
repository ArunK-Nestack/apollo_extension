"""Fetch minimal Freshsales sales-account evidence from a creation date onward.

Reads existing freshsales_agent/.env. Results contain account IDs, timestamps,
websites, tags, and two matching custom fields; no API key is saved.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent


def settings():
    result = {}
    for line in (ROOT / "freshsales_agent" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            name, value = line.split("=", 1)
            result[name] = value.strip().strip('"\'')
    return result


def fetch_json(url, key):
    request = Request(url, headers={"Authorization": f"Token token={key}", "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=lambda s: datetime.fromisoformat(s).date(), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = settings()
    base = config["FRESHSALES_DOMAIN"].rstrip("/")
    key = config["FRESHSALES_API_KEY"]
    filters = fetch_json(base + "/api/sales_accounts/filters", key)["filters"]
    view_id = next(f["id"] for f in filters if f["name"].lower() == "all accounts")
    accounts = {}
    contacts = {}
    page = 1
    while True:
        def fetch_page(number):
            query = urlencode({"sort": "created_at", "sort_type": "desc", "page": number,
                               "per_page": 100, "include": "contacts"})
            return fetch_json(f"{base}/api/sales_accounts/view/{view_id}?{query}", key)

        with ThreadPoolExecutor(max_workers=4) as executor:
            pages = list(executor.map(fetch_page, range(page, page + 10)))
        reached_start = False
        oldest = ""
        for data in pages:
            batch = data.get("sales_accounts") or []
            if not batch:
                reached_start = True
                break
            contacts.update({str(c["id"]): c.get("email", "") for c in data.get("contacts", []) if c.get("id")})
            for account in batch:
                created_at = account.get("created_at", "")
                created_day = datetime.fromisoformat(created_at[:10]).date()
                if created_day < args.start:
                    reached_start = True
                    break
                custom = account.get("custom_field") or {}
                accounts[str(account["id"])] = {
                    "id": str(account["id"]),
                    "created_at": created_at,
                    "website": account.get("website"),
                    "tags": account.get("tags") or [],
                    "contact_ids": [str(i) for i in (account.get("contact_ids") or [])],
                    "apollo_account_id": custom.get("cf_apollo_account_id"),
                    "account_label": custom.get("cf_account_label"),
                }
            oldest = batch[-1].get("created_at", "")
            if reached_start:
                break
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"accounts": list(accounts.values()), "contacts": contacts}), encoding="utf-8")
        print(f"pages={page}-{page+9} accounts={len(accounts)} oldest={oldest}", flush=True)
        if reached_start:
            break
        page += 10


if __name__ == "__main__":
    main()
