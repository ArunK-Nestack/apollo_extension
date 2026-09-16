import json
import requests

accs = json.load(open('config/apollo_accounts.json'))
key = accs[7]['api_key'] # recruiting@nestack.com

r = requests.post('https://api.apollo.io/api/v1/finder_views/people/search', headers={'X-Api-Key': key, 'Content-Type': 'application/json'}, json={})
data = r.json()
fvs = data.get('finder_views', [])

for fv in fvs:
    if fv.get('system') is not True:
        print(f"\n==========================================")
        print(f"Name: {fv.get('name')} (ID: {fv.get('id')})")
        print(f"Top-level keys: {list(fv.keys())}")
        filters = fv.get('filters') or {}
        filters_v2 = fv.get('filters_v2') or {}
        print(f"filters: {json.dumps(filters, indent=2)}")
        if filters_v2:
            print(f"filters_v2 keys: {list(filters_v2.keys())}")
