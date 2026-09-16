import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import get_connection
from scratch.test_multi_resolver_benchmark_full import verify_homepage_brand, check_clearbit_strict

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, company, company_domain, email
            FROM apollo_saved_leads
            WHERE batch = 'recruiting-sep' AND email IS NOT NULL AND email != ''
        """)
        leads = cur.fetchall()

        all_emails = [r[4].strip().lower() for r in leads if r[4]]
        fmt_ems = ",".join(["%s"] * len(all_emails))
        cur.execute(f"SELECT email, domain FROM emails WHERE email IN ({fmt_ems})", all_emails)
        crm_emails = set(r[0].lower().strip() for r in cur.fetchall())

        all_doms = list(set(e.split("@")[-1].lower().strip() for e in all_emails if "@" in e))
        fmt_doms = ",".join(["%s"] * len(all_doms))
        cur.execute(f"SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt_doms})", all_doms)
        crm_domains = set(r[0].lower().strip() for r in cur.fetchall())

from concurrent.futures import ThreadPoolExecutor, as_completed

def check_lead(l):
    lid, name, company, dom, em = l
    is_verified = False
    cb = check_clearbit_strict(company)
    if cb and verify_homepage_brand(cb, company):
        is_verified = True
    elif dom and verify_homepage_brand(dom, company):
        is_verified = True
    return (l, is_verified)

verified_leads = []
unverified_leads = []

with ThreadPoolExecutor(max_workers=25) as executor:
    futures = [executor.submit(check_lead, l) for l in leads]
    for fut in as_completed(futures):
        l, is_verified = fut.result()
        if is_verified:
            verified_leads.append(l)
        else:
            unverified_leads.append(l)

print(f"Total leads: {len(leads)}")
print(f"Verified companies: {len(verified_leads)}")
print(f"Unverified (phantom/guessed) companies: {len(unverified_leads)}")

# In unverified companies, how many were collisions?
unverified_collisions = sum(1 for l in unverified_leads if l[4].lower().strip() in crm_emails or l[4].split('@')[-1].lower().strip() in crm_domains)
print(f"Collisions inside unverified leads: {unverified_collisions} / {len(unverified_leads)} ({unverified_collisions/max(1, len(unverified_leads))*100:.1f}%)")

# In verified companies, how many were net new?
verified_net_new = sum(1 for l in verified_leads if l[4].lower().strip() not in crm_emails and l[4].split('@')[-1].lower().strip() not in crm_domains)
verified_collisions = len(verified_leads) - verified_net_new
print(f"In verified companies: {verified_net_new} net-new, {verified_collisions} collisions.")
if verified_leads:
    print(f"Verified pool credit efficiency: {verified_net_new/len(verified_leads)*100:.1f}%!")
