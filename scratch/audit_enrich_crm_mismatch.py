import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pandas as pd
from backend.api import get_connection, extract_root_domain

downloads_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
f_good = os.path.join(downloads_dir, 'rahul@nestack.co.in(aug 13 - sep 13) - good - 2092 good - 3993 total.csv')

df = pd.read_csv(f_good)

with get_connection() as conn:
    with conn.cursor() as cur:
        email_doms = df['Email'].dropna().astype(str).apply(lambda e: e.split('@')[-1].strip().lower() if '@' in e else '')
        unique_edoms = list(email_doms.unique())
        
        chunk_size = 1000
        crm_matches = set()
        for i in range(0, len(unique_edoms), chunk_size):
            chunk = unique_edoms[i:i+chunk_size]
            fmt = ','.join(['%s'] * len(chunk))
            cur.execute(f'SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt})', chunk)
            for r in cur.fetchall():
                crm_matches.add(r[0].lower().strip())

        # Also root domains
        root_map = {extract_root_domain(d): d for d in unique_edoms}
        root_chunks = list(root_map.keys())
        for i in range(0, len(root_chunks), chunk_size):
            chunk = root_chunks[i:i+chunk_size]
            fmt = ','.join(['%s'] * len(chunk))
            cur.execute(f'SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt})', chunk)
            for r in cur.fetchall():
                crm_matches.add(root_map[r[0].lower().strip()])

        print(f'Total CRM matched email domains: {len(crm_matches)}')

        # Join with apollo_saved_leads to see what company_domain was stored during scraping
        cur.execute('''
            SELECT apollo_id, name, company, company_domain, website_link, email
            FROM apollo_saved_leads
            WHERE batch = 'rahul_nestack_co_in-sep'
        ''')
        db_leads = {r[5].strip().lower(): r for r in cur.fetchall() if r[5]}

        mismatch_count = 0
        same_domain_count = 0
        case_studies = []

        for _, row in df.iterrows():
            em = str(row.get('Email', '')).strip().lower()
            if '@' not in em:
                continue
            edom = em.split('@')[-1].strip()
            if edom in crm_matches:
                db_lead = db_leads.get(em)
                if db_lead:
                    scraped_comp_dom = (db_lead[3] or '').strip().lower()
                    if scraped_comp_dom != edom and extract_root_domain(scraped_comp_dom) != extract_root_domain(edom):
                        mismatch_count += 1
                        if len(case_studies) < 10:
                            case_studies.append({
                                'name': db_lead[1],
                                'company': db_lead[2],
                                'scraped_domain': scraped_comp_dom,
                                'enriched_email_domain': edom,
                                'email': em
                            })
                    else:
                        same_domain_count += 1

        print(f'Of the colliding leads in 2092_good:')
        print(f'  • Email domain was DIFFERENT from scraped company domain: {mismatch_count} ({mismatch_count/max(1, mismatch_count+same_domain_count)*100:.1f}%)')
        print(f'  • Email domain was SAME as scraped company domain: {same_domain_count} ({same_domain_count/max(1, mismatch_count+same_domain_count)*100:.1f}%)')
        print('\nCase studies of why it bypassed scrape check:')
        for cs in case_studies:
            print(f"  - {cs['name']} @ {cs['company']}")
            print(f"      Scraped Website/Domain: {cs['scraped_domain']} (Checked against CRM: NOT FOUND! Allowed.)")
            print(f"      Enriched Actual Email:  {cs['email']} -> Domain: {cs['enriched_email_domain']} (ALREADY IN CRM!)")
