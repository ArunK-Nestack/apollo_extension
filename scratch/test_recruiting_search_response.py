import requests
import json

key = "bF9xb0EUc1WlP5vhY4pxpA"
headers = {"Content-Type": "application/json", "X-Api-Key": key}

url = "https://api.apollo.io/api/v1/mixed_people/api_search"
res = requests.post(url, headers=headers, json={"page": 1, "per_page": 1}, timeout=15)
print("HTTP", res.status_code)
if res.status_code == 200:
    data = res.json()
    print("Top-level keys:", list(data.keys()))
    for k in data.keys():
        if k not in ["people", "contacts"]:
            print(f"{k}: {data[k]}")
    if data.get("breadcrumbs"):
        print("Breadcrumbs:", data["breadcrumbs"])
    if data.get("derived_params"):
        print("Derived params:", data["derived_params"])
