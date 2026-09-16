import sys
import os
sys.path.insert(0, os.path.abspath("."))
import json
import requests
from scripts.apollo_search_direct import probe_search_total_volume

def convert_filters_v2_to_payload(filters_v2):
    payload = {}
    if not filters_v2:
        return payload
    
    if filters_v2.get('prospected_by_current_team'):
        payload['prospected_by_current_team'] = filters_v2['prospected_by_current_team']
        
    expr = filters_v2.get('filter_expression') or {}
    operands = expr.get('operands') or []
    
    rules = []
    for op in operands:
        if isinstance(op, dict):
            if 'filters' in op:
                rules.extend(op['filters'])
            elif 'filter_id' in op:
                rules.append(op)
            elif 'children' in op:
                for ch in op['children']:
                    if isinstance(ch, dict) and 'filters' in ch:
                        rules.extend(ch['filters'])
                    elif isinstance(ch, dict) and 'children' in ch:
                        rules.extend(ch['children'])
                    elif isinstance(ch, dict):
                        rules.append(ch)

    for r in rules:
        if not isinstance(r, dict):
            continue
        fid = r.get('filter_id') or ''
        op = r.get('operator') or ''
        val = r.get('value')
        
        # Locations
        if fid in ('filter.contact.location', 'filter.person.location'):
            if op == 'is_any_of' and isinstance(val, list):
                payload['person_locations'] = val
        # Titles
        elif fid in ('filter.contact.title', 'filter.person.title'):
            if op == 'is_any_of' and isinstance(val, list):
                payload['person_titles'] = val
            elif op == 'is_none_of' and isinstance(val, list):
                payload['person_not_titles'] = val
        # Email status
        elif fid in ('filter.contact.email_status', 'filter.email_status'):
            if isinstance(val, list):
                payload['contact_email_status_v2'] = val
            elif isinstance(val, str):
                payload['contact_email_status_v2'] = [val]
        # Employee count / Headcount
        elif fid in ('filter.account.number_of_employees', 'filter.number_of_employees'):
            if isinstance(val, list):
                payload['organization_num_employees_ranges'] = val
            elif isinstance(val, dict):
                min_v = val.get('min', '1')
                max_v = val.get('max', '')
                payload['organization_num_employees_ranges'] = [f"{min_v},{max_v}" if max_v else f"{min_v}"]
        # Industry Tags
        elif fid in ('filter.account.industry_tags', 'filter.industry_tags'):
            if op == 'is_any_of':
                payload['organization_industry_tag_ids'] = val
            elif op == 'is_none_of':
                payload['organization_not_industry_tag_ids'] = val
        # Keyword tags / Organization keywords
        elif fid in ('filter.account.keywords', 'filter.account.organization_keyword_tags', 'filter.account.keyword_tags'):
            if isinstance(val, list):
                payload['q_organization_keyword_tags'] = val
            elif isinstance(val, str):
                payload['q_organization_keyword_tags'] = [val]
        # Account / Company lists
        elif fid in ('filter.account.labels', 'filter.account_labels'):
            if op == 'is_none_of' and isinstance(val, list):
                payload['not_account_label_ids'] = val
            elif op == 'is_any_of' and isinstance(val, list):
                payload['account_label_ids'] = val
        # Contact / People lists
        elif fid in ('filter.contact.labels', 'filter.contact_labels'):
            if op == 'is_none_of' and isinstance(val, list):
                payload['not_contact_label_ids'] = val
            elif op == 'is_any_of' and isinstance(val, list):
                payload['contact_label_ids'] = val
        # Direct keywords
        elif fid in ('filter.q_keywords', 'filter.keywords'):
            payload['q_keywords'] = val
            
    return payload

def fetch_live_apollo_searches(api_key):
    url = 'https://api.apollo.io/api/v1/finder_views/people/search'
    headers = {'X-Api-Key': api_key, 'Content-Type': 'application/json'}
    try:
        r = requests.post(url, headers=headers, json={}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            views = data.get('finder_views') or []
            live_searches = []
            for v in views:
                if v.get('system') is True or v.get('archived') is True:
                    continue
                name = (v.get('name') or 'Untitled View').strip()
                if name in ('Default view', 'People Auto-Score', 'Scoring v2 Autogen', 'Companies Auto-Score'):
                    continue
                filters_v2 = v.get('filters_v2') or {}
                payload = convert_filters_v2_to_payload(filters_v2)
                if not payload and v.get('filters'):
                    payload = v.get('filters')
                live_searches.append({
                    'id': v.get('id'),
                    'name': name,
                    'display_name': name,
                    'filters': payload,
                    'is_live': True
                })
            return live_searches
    except Exception as e:
        print('Error fetching live searches:', e)
    return []

if __name__ == '__main__':
    accs = json.load(open('config/apollo_accounts.json'))
    for idx, name in [(7, 'recruiting@nestack.com'), (5, 'rahul@nestack.co.in'), (3, 'vijay@nestacktech.com')]:
        key = accs[idx]['api_key']
        searches = fetch_live_apollo_searches(key)
        print(f"\n=======================================================")
        print(f"LIVE SEARCHES FROM APOLLO FOR: {name} ({len(searches)} found)")
        print(f"=======================================================")
        for s in searches:
            vol = probe_search_total_volume(key, s['filters'])
            t_count = len(s['filters'].get('person_titles', []))
            kw_count = len(s['filters'].get('q_organization_keyword_tags', []))
            loc_count = len(s['filters'].get('person_locations', []))
            print(f"  ★ {s['name']:<45} -> {vol:>10,d} leads available | Titles: {t_count} | Tags: {kw_count} | Locs: {loc_count}")
