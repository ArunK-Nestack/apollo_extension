import os
import pymysql
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
import backend.api as api

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER', 'nestack'),
    password=os.getenv('DB_PASSWORD', ''),
    database=os.getenv('DB_NAME', 'apollo_scrapers')
)

# Fetch the exact 34 contacts
with conn.cursor(pymysql.cursors.DictCursor) as cur:
    cur.execute("""
        SELECT id, apollo_id, name, first_name, last_name, job_title, company, company_domain, website_link, location, linkedin_url, apollo_profile_url, segment, created_at
        FROM apollo_saved_leads
        WHERE batch = 'batch_1' AND id >= 46069
        ORDER BY id ASC
    """)
    raw_leads = cur.fetchall()

print(f"Loaded {len(raw_leads)} leads from DB batch_1.")

# Convert to ApolloContact objects
contacts = []
for idx, r in enumerate(raw_leads, 1):
    contacts.append(api.ApolloContact(
        key=f"lead_{idx}_{r['id']}",
        apollo_id=r['apollo_id'],
        name=r['name'],
        first_name=r['first_name'],
        last_name=r['last_name'],
        job_title=r['job_title'],
        company=r['company'],
        company_domain=r['company_domain'],
        website_link=r['website_link'],
        location=r['location'],
        linkedin_url=r['linkedin_url'],
        apollo_profile_url=r['apollo_profile_url']
    ))

# 1. Test against the matching engine
req = api.ApolloMatchRequest(
    contacts=contacts,
    batch="test_34_audit",
    title_guardrail_enabled=True,
    indian_name_guardrail_enabled=True
)

res = api.match_apollo(req)

print("\n" + "="*80)
print("EVALUATION OF THE 34 LEADS UNDER NEW GUARDRAILS")
print("="*80)

required_leads = []
disqualified_leads = []
existing_crm_leads = []

for c in contacts:
    r = res["results"][c.key]
    item = {
        "name": c.name,
        "title": c.job_title,
        "company": c.company,
        "domain": c.company_domain,
        "result": r
    }
    if r.get("exists"):
        existing_crm_leads.append(item)
    elif r.get("required"):
        required_leads.append(item)
    else:
        disqualified_leads.append(item)

print(f"\nTotal Tested: {len(contacts)}")
print(f"  🟢 Qualified & Required:    {len(required_leads)}")
print(f"  ⊘ Existing in CRM/DB:        {len(existing_crm_leads)}")
print(f"  ❌ Disqualified / Filtered:  {len(disqualified_leads)}")

print("\n--- 🟢 QUALIFIED & REQUIRED LEADS ---")
for idx, item in enumerate(required_leads, 1):
    print(f"{idx:02d}. {item['name']} | {item['title']} | {item['company']} | {item['domain']} -> Segment: {item['result'].get('segment')}")

print("\n--- ⊘ EXISTING IN CRM (DUPLICATES BLOCKED) ---")
for idx, item in enumerate(existing_crm_leads, 1):
    print(f"{idx:02d}. {item['name']} | {item['title']} | {item['company']} | {item['domain']} -> Matched: {item['result'].get('matched_domain')} ({item['result'].get('guardrail_reason')})")

print("\n--- ❌ DISQUALIFIED / EXCLUDED BY GUARDRAILS ---")
for idx, item in enumerate(disqualified_leads, 1):
    print(f"{idx:02d}. {item['name']} | {item['title']} | {item['company']} | Status: {item['result'].get('guardrail_status')} | Reason: {item['result'].get('guardrail_reason')}")
