import requests
import json

key = "bF9xb0EUc1WlP5vhY4pxpA"
r = requests.get("https://api.apollo.io/api/v1/labels", headers={"X-Api-Key": key})
labels = r.json()
print(f"Total labels: {len(labels)}")
for l in labels:
    lid = l.get("_id") or l.get("id")
    mod = l.get("modality")
    cnt = l.get("cached_count")
    name = l.get("name")
    print(f"  [{mod}] {name} ({cnt} records, ID: {lid})")
