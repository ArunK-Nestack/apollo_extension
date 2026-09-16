import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import get_connection, generate_candidate_domains, extract_root_domain
from scripts.domain_resolver_engine import clean_domain, _generate_smart_permutations, resolve_domain_multi_tier
from scratch.test_multi_resolver_benchmark_full import verify_homepage_brand, check_clearbit_strict
from concurrent.futures import ThreadPoolExecutor, as_completed

def run_comparison():
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

    total_leads = len(leads)

    def evaluate(l):
        lid, name, company, old_staged_dom, em = l
        em_clean = em.strip().lower()
        edom = em_clean.split("@")[-1].lower().strip() if "@" in em_clean else ""

        # Ground truth: is this lead actually in the CRM?
        is_collision = (em_clean in crm_emails or edom in crm_domains)

        # -------------------------------------------------------------
        # OLD SYSTEM EVALUATION (What happened when batch was enriched)
        # -------------------------------------------------------------
        # Old system only checked old_staged_dom
        old_hit = False
        if old_staged_dom:
            old_root = extract_root_domain(old_staged_dom)
            if old_root in crm_domains:
                old_hit = True

        # -------------------------------------------------------------
        # NEW SYSTEM EVALUATION (Multi-Candidate + Verified-Only Arbiter)
        # -------------------------------------------------------------
        # Step 1: Candidate domain generation & CRM check
        cands = set(generate_candidate_domains(company))
        for p in _generate_smart_permutations(company)[:4]:
            cands.add(clean_domain(p))
        if old_staged_dom:
            cands.add(clean_domain(old_staged_dom))

        cand_roots = [extract_root_domain(c) for c in cands if c]
        cand_roots = [c for c in cand_roots if c]

        new_crm_hit = False
        with get_connection() as conn:
            with conn.cursor() as cur:
                if cand_roots:
                    fmt = ",".join(["%s"] * len(cand_roots))
                    cur.execute(f"SELECT domain FROM emails WHERE domain IN ({fmt}) LIMIT 1", cand_roots)
                    if cur.fetchone():
                        new_crm_hit = True

        # Step 2: Verification check (Never gamble on unverified phantom domains)
        cb = check_clearbit_strict(company)
        is_verified = False
        if cb and verify_homepage_brand(cb, company):
            is_verified = True
        elif old_staged_dom and verify_homepage_brand(old_staged_dom, company):
            is_verified = True

        new_status = "UNKNOWN"
        if new_crm_hit:
            new_status = "BLOCKED_BY_MULTI_CANDIDATE_CRM"
        elif not is_verified:
            new_status = "DROPPED_AS_UNVERIFIED_PHANTOM"
        else:
            new_status = "APPROVED_FOR_ENRICHMENT"

        return {
            "lead_id": lid,
            "company": company,
            "staged_domain": old_staged_dom,
            "actual_email": em,
            "actual_email_domain": edom,
            "is_actual_crm_collision": is_collision,
            "old_system_blocked": old_hit,
            "new_system_status": new_status,
        }

    results = []
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(evaluate, l) for l in leads]
        for fut in as_completed(futures):
            results.append(fut.result())

    # Compile metrics
    actual_collisions = sum(1 for r in results if r["is_actual_crm_collision"])
    actual_net_new = total_leads - actual_collisions

    # Old system
    old_blocked = sum(1 for r in results if r["old_system_blocked"])
    old_burned_credits = actual_collisions  # In old system, all 179 collided leads were enriched!
    old_waste_rate = (actual_collisions / total_leads) * 100

    # New system
    new_blocked_crm = sum(1 for r in results if r["new_system_status"] == "BLOCKED_BY_MULTI_CANDIDATE_CRM")
    new_dropped_phantom = sum(1 for r in results if r["new_system_status"] == "DROPPED_AS_UNVERIFIED_PHANTOM")
    new_approved = [r for r in results if r["new_system_status"] == "APPROVED_FOR_ENRICHMENT"]
    new_approved_count = len(new_approved)
    new_approved_net_new = sum(1 for r in new_approved if not r["is_actual_crm_collision"])
    new_approved_collisions = sum(1 for r in new_approved if r["is_actual_crm_collision"])

    # Protection rate: of the actual CRM collisions, how many were prevented from enrichment?
    collisions_prevented = sum(
        1 for r in results 
        if r["is_actual_crm_collision"] and r["new_system_status"] != "APPROVED_FOR_ENRICHMENT"
    )
    protection_rate = (collisions_prevented / actual_collisions) * 100
    purity_rate = (new_approved_net_new / max(1, new_approved_count)) * 100

    print("=" * 85)
    print("ACCURACY & COMPARISON REPORT: BATCH 'recruiting-sep'")
    print("=" * 85)
    print(f"Total Enriched Leads in Batch:                 {total_leads}")
    print(f"Ground Truth Real CRM Collisions:             {actual_collisions} ({old_waste_rate:.1f}%)")
    print(f"Ground Truth True Net-New Leads:              {actual_net_new} ({100 - old_waste_rate:.1f}%)")
    print("-" * 85)
    print("OLD SYSTEM PERFORMANCE:")
    print(f"  • Apollo Credits Burned on Batch:           {total_leads}")
    print(f"  • Credits Wasted on Existing CRM Records:   {actual_collisions} credits wasted!")
    print(f"  • Credit Waste Rate:                        {old_waste_rate:.1f}%")
    print(f"  • Real Net-New Purity:                      {100 - old_waste_rate:.1f}%")
    print("-" * 85)
    print("NEW MULTI-RESOLVER & CANDIDATE SHIELD PERFORMANCE:")
    print(f"  • Caught by Multi-Candidate CRM Check:      {new_blocked_crm} leads (0 credits)")
    print(f"  • Dropped as Unverified / Phantom:          {new_dropped_phantom} leads (0 credits)")
    print(f"  • Total Collisions Stopped Before Enrich:   {collisions_prevented} / {actual_collisions} ({protection_rate:.1f}% PROTECTED)")
    print(f"  • Apollo Credits Saved:                     {collisions_prevented} credits saved ($0 cost)")
    print(f"  • Leads Approved for Enrichment:            {new_approved_count}")
    print(f"      - Verified Net-New:                     {new_approved_net_new} ({purity_rate:.1f}%)")
    print(f"      - Residual Collisions:                  {new_approved_collisions} (down from {actual_collisions})")
    print(f"  • New Enrichment Accuracy / Purity:         {purity_rate:.1f}% (vs {100 - old_waste_rate:.1f}% previously)")
    print("=" * 85)

if __name__ == "__main__":
    run_comparison()
