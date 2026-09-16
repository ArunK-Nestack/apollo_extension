import requests

api_key = 'bF9xb0EUc1WlP5vhY4pxpA'
headers = {'Cache-Control': 'no-cache', 'Content-Type': 'application/json', 'X-Api-Key': api_key}
r = requests.get('https://api.apollo.io/v1/labels', headers=headers, timeout=10)
if r.status_code == 200:
    labels = r.json()
    print(f"Total labels: {len(labels)}")
    for l in labels:
        name = l.get('name', '')
        cnt = l.get('cached_count', 0)
        mod = l.get('modality', '')
        lid = l.get('_id')
        print(f"{mod:<10} | count: {cnt:<6} | {lid} | {name}")
