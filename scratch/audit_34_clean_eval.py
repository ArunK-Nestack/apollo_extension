import os
import sys
sys.path.insert(0, ".")
import pymysql
from dotenv import load_dotenv
import backend.api as api

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER', 'nestack'),
    password=os.getenv('DB_PASSWORD', ''),
    database=os.getenv('DB_NAME', 'apollo_scrapers')
)

with conn.cursor(pymysql.cursors.DictCursor) as cur:
    cur.execute("""
        SELECT id, apollo_id, name, first_name, last_name, job_title, company, company_domain, website_link, location, linkedin_url, apollo_profile_url, segment, created_at
        FROM apollo_saved_leads
        WHERE batch = 'batch_1' AND id >= 46069
        ORDER BY id ASC
    """)
    raw_leads = cur.fetchall()

print(f"Loaded {len(raw_leads)} leads for Clean Simulation.")

# Query CRM emails table directly for each domain to check if any exist in the 7.22M CRM database
domains = list(set(r['company_domain'] for r in raw_leads if r['company_domain']))
crm_hits = {}
with conn.cursor() as cur:
    fmt = ",".join(["%s"] * len(domains))
    cur.execute(f"SELECT domain, full_name, email FROM emails WHERE domain IN ({fmt})", tuple(domains))
    for dom, fn, em in cur.fetchall():
        crm_hits[dom] = (fn, em)

print(f"\n--- CRM 7.22M DB Domain Hits: {len(crm_hits)} out of {len(domains)} unique domains ---")
for dom, (fn, em) in crm_hits.items():
    print(f"  [Existing in CRM DB] Domain '{dom}' found in emails table! (Contact: {fn}, Email: {em})")

# Now check title guardrails for each lead
print("\n--- Title & Demographic Guardrail Evaluation for all 34 leads ---")
for idx, r in enumerate(raw_leads, 1):
    title = r['job_title']
    name = r['name']
    comp = r['company']
    dom = r['company_domain']
    
    # 1. Demographic check
    is_ind, ind_reason = api.is_unambiguous_pure_indian_name(name, connection=conn)
    
    # 2. Title check
    t_info = api.lookup_job_titles_batch([title], connection=conn).get(title, {})
    
    # 3. CRM check
    in_crm = dom in crm_hits
    
    status = "QUALIFIED (★ Required Lead)"
    if in_crm:
        status = f"EXISTING IN CRM (Domain '{dom}' already owned)"
    elif is_ind:
        status = f"DISQUALIFIED DEMOGRAPHIC ({ind_reason})"
    elif not t_info.get("required"):
        status = f"DISQUALIFIED TITLE ({t_info.get('segment')} - {t_info.get('reason')})"
        
    print(f"{idx:02d}. {name} | {title} | {comp} | Domain: {dom} --> {status}")
