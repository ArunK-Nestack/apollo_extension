import json
import requests

with open('config/apollo_accounts.json', 'r', encoding='utf-8') as f:
    accounts = json.load(f)

for acc in accounts:
    key = acc.get('api_key')
    if not key or str(key).startswith('YOUR_'):
        continue
    name = acc.get('name', 'Unknown')
    email = acc.get('email', '')
    try:
        resp = requests.get(
            'https://api.apollo.io/v1/labels',
            headers={'X-Api-Key': key},
            params={'modality': 'contacts'},
            timeout=8
        )
        if resp.status_code == 200:
            labels = resp.json()
            if labels and isinstance(labels, list):
                l0 = labels[0]
                print(f"Account: {name:<25} | Email: {email:<35} | Team ID: {l0.get('team_id')} | User ID: {l0.get('user_id')}")
    except Exception as e:
        print(f"{name} error: {e}")
