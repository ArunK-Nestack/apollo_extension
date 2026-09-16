import json
import requests

with open('config/apollo_accounts.json', 'r', encoding='utf-8') as f:
    accounts = json.load(f)

rec = [a for a in accounts if a.get('email', '').lower() == 'recruiting@nestack.com'][0]
key = rec['api_key']

r = requests.get('https://api.apollo.io/v1/labels', headers={'X-Api-Key': key})
labels = r.json()

target = None
for l in labels:
    if l.get('name') == 'vp/president-aug2026-monthly':
        target = l
        break

biz = None
for l in labels:
    if l.get('name') == 'biz_sample':
        biz = l
        break

print("vp/president-aug2026-monthly:")
print("  user_id:", target.get('user_id') if target else None)
print("  team_id:", target.get('team_id') if target else None)
print("  created_at:", target.get('created_at') if target else None)

print("\nbiz_sample:")
print("  user_id:", biz.get('user_id') if biz else None)
print("  team_id:", biz.get('team_id') if biz else None)
print("  created_at:", biz.get('created_at') if biz else None)
