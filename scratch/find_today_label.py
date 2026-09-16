import json
import requests

with open('config/apollo_accounts.json', 'r', encoding='utf-8') as f:
    accounts = json.load(f)

found = []
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
            timeout=8
        )
        if resp.status_code == 200:
            labels = resp.json()
            if isinstance(labels, list):
                for l in labels:
                    created = l.get('created_at', '')
                    if '2026-09-16' in created or '2026-09-15' in created:
                        found.append({
                            "account": name,
                            "email": email,
                            "label_name": l.get('name'),
                            "id": l.get('id'),
                            "cached_count": l.get('cached_count'),
                            "created_at": created
                        })
    except Exception as e:
        pass

print(f"Total matching labels created recently: {len(found)}")
for item in found:
    print(f"Account: {item['account']} ({item['email']}) -> Label: '{item['label_name']}' | ID: {item['id']} | Count: {item['cached_count']} | Created: {item['created_at']}")
