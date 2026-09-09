import os
import sys
import csv
sys.path.insert(0, ".")

import backend.api as api
from scripts.apollo_export_formatter import APOLLO_75_HEADERS, format_apollo_lead_row

def main():
    batch_name = 'abel_abraham_nestacktechnologies_com-sep'
    account_email = 'abel.abraham@nestacktechnologies.com'
    
    # 6 Layer-4 Alias Domains that route to existing CRM companies
    LAYER4_BLOCKED = {
        'eletta.se',
        'hagarparts.com',
        'installationservicesil.com',
        'radiallusa.com',
        'techmold.com',
        'trespicosos.com'
    }

    print(f"Fetching all leads for batch '{batch_name}' from MySQL...", flush=True)
    with api.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id, batch, apollo_id, name, first_name, last_name, job_title, email, email_status,
                    company, company_domain, website_link, annual_revenue, employee_count,
                    industry, tech_stack, keywords, company_phone, hq_address, location, linkedin_url,
                    company_linkedin_url, apollo_profile_url, segment, account_used, credits_charged,
                    raw_enrichment_data, enriched_at, created_at
                FROM apollo_saved_leads
                WHERE batch = %s
                ORDER BY id ASC;
            """, (batch_name,))
            cols = [c[0] for c in cur.description]
            raw_rows = cur.fetchall()
            leads = [dict(zip(cols, r)) for r in raw_rows]

    total_leads = len(leads)
    print(f"Total leads retrieved from DB: {total_leads:,d}", flush=True)

    # 1. Filter out Layer-4 Alias / Shared MX domains
    valid_leads = []
    layer4_filtered = []
    for l in leads:
        d = (l.get('company_domain') or '').strip().lower()
        if d in LAYER4_BLOCKED:
            layer4_filtered.append(l)
        else:
            valid_leads.append(l)

    print(f"Filtered out {len(layer4_filtered)} leads belonging to 6 Layer-4 shared/alias domains.", flush=True)

    # 2. Intra-batch Domain Deduplication: keep 1 lead per unique domain based on seniority ranking
    domain_to_best_lead = {}
    for l in valid_leads:
        dom = (l.get('company_domain') or '').strip().lower()
        comp = (l.get('company') or '').strip().lower()
        key = f"dom:{dom}" if dom else (f"comp:{comp}" if comp else f"id:{l['id']}")
        
        score = api.get_seniority_score(str(l.get('job_title') or ""))
        if l.get('linkedin_url'):
            score += 5

        if key not in domain_to_best_lead or score > domain_to_best_lead[key][0]:
            domain_to_best_lead[key] = (score, l)

    unique_leads = [v[1] for v in domain_to_best_lead.values()]
    print(f"Unique decision-maker leads after 1-per-domain deduplication: {len(unique_leads):,d}", flush=True)

    # 3. Export to CSV in official 75-column Apollo contact schema
    os.makedirs("exports", exist_ok=True)
    out_file = os.path.join("exports", f"{batch_name}_4197_verified_unique_leads.csv")

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(APOLLO_75_HEADERS)
        for l in unique_leads:
            writer.writerow(format_apollo_lead_row(l, account_email))

    print(f"\n[SUCCESS] Successfully generated unique leads CSV:")
    print(f"  • File:     {os.path.abspath(out_file)}")
    print(f"  • Rows:     {len(unique_leads):,d} leads (1 per unique company domain)")
    print(f"  • Columns:  {len(APOLLO_75_HEADERS)} official Apollo export columns")

if __name__ == "__main__":
    main()
