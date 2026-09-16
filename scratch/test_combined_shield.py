import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import get_connection, generate_candidate_domains
from scripts.domain_resolver_engine import clean_domain, _generate_smart_permutations
from scratch.test_multi_resolver_benchmark_full import verify_homepage_brand, check_clearbit_strict
from concurrent.futures import ThreadPoolExecutor, as_completed

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

def evaluate_lead(l):
    lid, name, company, old_dom, em = l
    edom = em.split("@")[-1].lower().strip() if "@" in em else ""
    is_actual_collision = (em in crm_emails or edom in crm_domains)

    # 1. Multi-candidate CRM check
    cands = set(generate_candidate_domains(company))
    for p in _generate_smart_permutations(company)[:4]:
        cands.add(clean_domain(p))
    if old_dom:
        cands.add(clean_domain(old_dom))

    cand_list = [c for c in cands if c]
    hit_crm = False
    with get_connection() as conn:
        with conn.cursor() as cur:
            if cand_list:
                fmt = ",".join(["%s"] * len(cand_list))
                cur.execute(f"SELECT domain FROM emails WHERE domain IN ({fmt}) LIMIT 1", cand_list)
                if cur.fetchone():
                    hit_crm = True

    if hit_crm:
        return ("BLOCKED_BY_CRM_CANDIDATES", is_actual_collision)

    # 2. Check if verified
    cb = check_clearbit_strict(company)
    is_verified = False
    if cb and verify_homepage_brand(cb, company):
        is_verified = True
    elif old_dom and verify_homepage_brand(old_dom, company):
        is_verified = True

    if not is_verified:
        return ("DROPPED_AS_UNVERIFIED", is_actual_collision)

    # If it passed CRM candidates AND is verified:
    return ("APPROVED_FOR_ENRICHMENT", is_actual_collision)

results = []
with ThreadPoolExecutor(max_workers=25) as executor:
    futures = [executor.submit(evaluate_lead, l) for l in leads]
    for fut in as_completed(futures):
        results.append(fut.result())

total = len(results)
blocked_crm = sum(1 for status, coll in results if status == "BLOCKED_BY_CRM_CANDIDATES")
dropped_unverified = sum(1 for status, coll in results if status == "DROPPED_AS_UNVERIFIED")
approved = [res for res in results if res[0] == "APPROVED_FOR_ENRICHMENT"]
approved_count = len(approved)
approved_net_new = sum(1 for status, coll in approved if not coll)
approved_wasted = sum(1 for status, coll in approved if coll)

# How many of the 179 collided were stopped?
total_actual_collisions = sum(1 for status, coll in results if coll)
collisions_stopped = sum(1 for status, coll in results if coll and status != "APPROVED_FOR_ENRICHMENT")
protection_rate = (collisions_stopped / total_actual_collisions) * 100
efficiency = (approved_net_new / max(1, approved_count)) * 100

print("=" * 80)
print("COMBINED SHIELD PERFORMANCE ON BATCH 'recruiting-sep'")
print("=" * 80)
print(f"Total Leads in Batch:                 {total}")
print(f"Total Collisions in Old System:       {total_actual_collisions} (Wasted {total_actual_collisions} credits)")
print(f"Blocked by Multi-Candidate CRM:       {blocked_crm}")
print(f"Dropped as Unverified / Phantom:      {dropped_unverified}")
print(f"Total Wasted Credits STOPPED:         {collisions_stopped} / {total_actual_collisions} ({protection_rate:.1f}% PROTECTED!)")
print(f"Leads Approved for Enrichment:        {approved_count}")
print(f"  • Verified Net-New:                 {approved_net_new}")
print(f"  • Residual Collisions:              {approved_wasted} (Down from {total_actual_collisions}!)")
print(f"New Enrichment Accuracy / Efficiency: {efficiency:.1f}%")
print("=" * 80)
