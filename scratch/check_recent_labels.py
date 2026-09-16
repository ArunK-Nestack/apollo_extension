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
            headers={'Content-Type': 'application/json', 'Cache-Control': 'no-cache', 'X-Api-Key': key},
            params={'modality': 'contacts'},
            timeout=8
        )
        if resp.status_code == 200:
            labels = resp.json()
            sorted_labels = sorted(labels, key=lambda x: x.get('created_at', ''), reverse=True)
            print(f"Account: {name} ({email}) - Total Labels: {len(labels)}")
            for l in sorted_labels[:5]:
                print(f"   • '{l.get('name')}' | ID: {l.get('id')} | Created: {l.get('created_at')} | Count: {l.get('cached_count')}")
    except Exception as e:
        print(f"Account: {name} error: {e}")
