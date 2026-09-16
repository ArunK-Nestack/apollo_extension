import json
import requests

def convert_filters_v2_to_payload(filters_v2):
    payload = {}
    if not filters_v2:
        return payload
    
    if filters_v2.get('prospected_by_current_team'):
        payload['prospected_by_current_team'] = filters_v2['prospected_by_current_team']
        
    expr = filters_v2.get('filter_expression') or {}
    operands = expr.get('operands') or []
    
    # Collect all rule dictionaries
    rules = []
    for op in operands:
        if isinstance(op, dict):
            if 'filters' in op:
                rules.extend(op['filters'])
            elif 'filter_id' in op:
                rules.append(op)
            elif 'children' in op:
                for ch in op['children']:
                    if isinstance(ch, dict) and 'children' in ch:
                        rules.extend(ch['children'])
                    elif isinstance(ch, dict):
                        rules.append(ch)

    for r in rules:
        if not isinstance(r, dict):
            continue
        fid = r.get('filter_id')
        op = r.get('operator')
        val = r.get('value')
        
        if fid == 'filter.person.location' and op == 'is_any_of':
            payload['person_locations'] = val
        elif fid == 'filter.person.title':
            if op == 'is_any_of':
                payload['person_titles'] = val
            elif op == 'is_none_of':
                payload['person_not_titles'] = val
        elif fid == 'filter.contact.email_status':
            payload['contact_email_status_v2'] = val
        elif fid == 'filter.account.number_of_employees':
            payload['organization_num_employees_ranges'] = val
        elif fid == 'filter.account.industry_tags':
            if op == 'is_any_of':
                payload['organization_industry_tag_ids'] = val
            elif op == 'is_none_of':
                payload['organization_not_industry_tag_ids'] = val
        elif fid in ('filter.account.organization_keyword_tags', 'filter.account.keyword_tags'):
            payload['q_organization_keyword_tags'] = val
        elif fid == 'filter.account.labels':
            if op == 'is_none_of':
                payload['not_account_label_ids'] = val
            elif op == 'is_any_of':
                payload['account_label_ids'] = val
        elif fid == 'filter.contact.labels':
            if op == 'is_none_of':
                payload['not_contact_label_ids'] = val
            elif op == 'is_any_of':
                payload['contact_label_ids'] = val
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
                if name in ('Default view', 'People Auto-Score', 'Scoring v2 Autogen'):
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
        print(f"\nLive Searches for {name} ({len(searches)} found):")
        for s in searches:
            t_count = len(s['filters'].get('person_titles', []))
            kw_count = len(s['filters'].get('q_organization_keyword_tags', []))
            loc_count = len(s['filters'].get('person_locations', []))
            not_t = len(s['filters'].get('person_not_titles', []))
            not_acc = len(s['filters'].get('not_account_label_ids', []))
            not_con = len(s['filters'].get('not_contact_label_ids', []))
            print(f"  • {s['name']} [ID: {s['id']}]")
            print(f"      Titles: {t_count} (Excluded: {not_t}) | Tags: {kw_count} | Locs: {loc_count} | Excl Lists: {not_acc} acc, {not_con} people")
