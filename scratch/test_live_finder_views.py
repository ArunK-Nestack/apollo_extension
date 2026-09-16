import json
import requests

accs = json.load(open('config/apollo_accounts.json'))
key = accs[7]['api_key'] # recruiting@nestack.com

r = requests.post('https://api.apollo.io/api/v1/finder_views/people/search', headers={'X-Api-Key': key, 'Content-Type': 'application/json'}, json={})
data = r.json()
print('Top-level keys in response:', list(data.keys()))
fvs = data.get('finder_views', [])
print(f'Total finder_views returned: {len(fvs)}')
for fv in fvs:
    print(f"  • {fv.get('name')} | ID: {fv.get('id')} | system: {fv.get('system')} | shared: {fv.get('shared')}")
