import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import requests
from backend.api import get_connection, ensure_apollo_saved_leads_table
from scripts.apollo_saved_search_inspector import fetch_live_apollo_searches, save_leads_to_mysql_batch

def pull_remaining_vraghavan():
    print("=== Pulling Remaining Unpulled Leads for VRAGHAVAN@NESTACKTECH.COM ===")
    
    # 1. Load account
    with open('config/apollo_accounts.json', 'r', encoding='utf-8') as f:
        accounts = json.load(f)
    acc = [a for a in accounts if a.get('email', '').strip().lower() == 'vraghavan@nestacktech.com'][0]
    api_key = acc['api_key']
    account_email = 'VRAGHAVAN@NESTACKTECH.COM'
    batch_name = 'vraghvan_nestacktech_com_sep'
    headers = {'Content-Type': 'application/json', 'X-Api-Key': api_key}
    
    # 2. Find search
    searches = fetch_live_apollo_searches(api_key)
    target_search = None
    for s in searches:
        if 'Owner Others' in s.get('name', ''):
            target_search = s
            break
            
    if not target_search:
        print("[!] Target search 'Owner Others/IT/NA PST/MT/AUS/NZ/SING' not found!")
        return

    print(f"Target Search: {target_search.get('name')}")
    filters = dict(target_search.get('filters', {}))
    filters['prospected_by_current_team'] = ['yes']
    filters['per_page'] = 100

    # 3. Check existing apollo_ids in DB
    existing_ids = set()
    with get_connection() as conn:
        ensure_apollo_saved_leads_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT apollo_id FROM apollo_saved_leads 
                WHERE batch = %s
            """, (batch_name,))
            existing_ids = set(r[0] for r in cur.fetchall() if r[0])
            
    print(f"Existing leads in DB batch '{batch_name}': {len(existing_ids):,d}")

    # 4. Fetch all pages and collect unpulled leads
    unpulled_leads = []
    page = 1
    total_entries = None
    
    while True:
        filters['page'] = page
        resp = requests.post('https://api.apollo.io/api/v1/contacts/search', headers=headers, json=filters, timeout=15)
        if resp.status_code != 200:
            print(f"[!] Error fetching page {page}: {resp.status_code} {resp.text}")
            break
            
        data = resp.json()
        if total_entries is None:
            total_entries = data.get('pagination', {}).get('total_entries', 0)
            total_pages = data.get('pagination', {}).get('total_pages', 0)
            print(f"Apollo Vault Total: {total_entries:,d} leads across {total_pages} pages")

        contacts = data.get('contacts', [])
        if not contacts:
            break

        for c in contacts:
            cid = c.get('id') or c.get('person_id')
            if cid and cid not in existing_ids:
                unpulled_leads.append(c)
                existing_ids.add(cid)

        print(f"  Page {page:>2}/{total_pages}: Processed {len(contacts)} contacts (Unpulled collected: {len(unpulled_leads)})")
        
        if page >= total_pages:
            break
        page += 1

    print(f"\n[+] Total new unpulled leads collected: {len(unpulled_leads):,d}")

    if not unpulled_leads:
        print("[✓] All leads are already present in local database.")
        return

    # 5. Insert directly into MySQL apollo_saved_leads
    print(f"Saving {len(unpulled_leads):,d} leads to batch '{batch_name}'...")
    save_leads_to_mysql_batch(
        leads=unpulled_leads,
        batch_tag=batch_name,
        account_email=account_email,
        target_table='apollo_saved_leads'
    )

    # 6. Final verification
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), 
                       COUNT(DISTINCT email),
                       SUM(CASE WHEN email_status = 'verified' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN email IS NOT NULL AND email != '' THEN 1 ELSE 0 END)
                FROM apollo_saved_leads
                WHERE batch = %s
            """, (batch_name,))
            tot, dist_em, ver_em, has_em = cur.fetchone()
            print("\n=======================================================")
            print(f"UPDATED BATCH STATS: {batch_name}")
            print(f"Total Leads:             {tot:,d}")
            print(f"Unique Emails:           {dist_em:,d}")
            print(f"Verified Email Status:   {ver_em:,d}")
            print(f"Leads with Email:        {has_em:,d}")
            print("=======================================================")

if __name__ == '__main__':
    pull_remaining_vraghavan()
