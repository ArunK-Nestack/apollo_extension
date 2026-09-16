import sys
sys.path.insert(0, '.')
from scripts.apollo_search_direct import load_account_creator_searches, probe_search_total_volume
import json

with open('config/apollo_accounts.json') as f:
    accs = json.load(f)

rec_acc = [a for a in accs if 'recruiting' in a['email'].lower()][0]
searches = load_account_creator_searches(rec_acc['email'])
print(f"Loaded {len(searches)} searches for {rec_acc['email']}:")
for s in searches:
    vol = probe_search_total_volume(rec_acc['api_key'], s['filters'])
    print(f"  * {s['name']} -> Available Volume: {vol:,d} leads")
