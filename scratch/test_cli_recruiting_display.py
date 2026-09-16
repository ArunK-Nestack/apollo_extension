import sys
sys.path.insert(0, '.')
from scripts.apollo_search_direct import load_account_creator_searches, probe_search_total_volume
import json

with open('config/apollo_accounts.json') as f:
    accs = json.load(f)

rec_acc = [a for a in accs if 'recruiting' in a['email'].lower()][0]
searches = load_account_creator_searches(rec_acc['email'])
print(f"Available Creator Searches for {rec_acc['email']}:")
for i, s in enumerate(searches, 1):
    vol = probe_search_total_volume(rec_acc['api_key'], s['filters'])
    print(f"  [{i:2d}] ★ [Creator Search] {s['name']} ({vol:,d} leads)")
