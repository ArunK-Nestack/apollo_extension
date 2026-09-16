import json
import requests

with open('config/apollo_accounts.json', 'r', encoding='utf-8') as f:
    accounts = json.load(f)

for acc in accounts:
    email = acc.get('email', '')
    key = acc.get('api_key')
    if not key or str(key).startswith('YOUR_'):
        continue
    try:
        resp = requests.get('https://api.apollo.io/v1/labels', headers={'X-Api-Key': key}, params={'modality': 'contacts'}, timeout=6)
        labels = resp.json()
        if isinstance(labels, list):
            for l in labels:
                name = l.get('name', '').lower()
                if 'vp/president-aug2026-monthly' in name or 'jan2021 to jan 2023' in name:
                    print(f"FOUND MATCHING BROWSER ACCOUNT: {acc.get('name')} | Email: {email} | Key: {key[:8]}... | Matched List: {l.get('name')}")
    except Exception:
        pass
