import sys
import os
import time
sys.path.insert(0, ".")

import backend.api as api

def main():
    batch_name = 'abel_abraham_nestacktechnologies_com-sep'
    with api.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, apollo_id, name, first_name, last_name, job_title, company, company_domain, website_link, segment
                FROM apollo_saved_leads
                WHERE batch = %s
                ORDER BY id ASC
            """, (batch_name,))
            all_leads = cur.fetchall()

    total_leads = len(all_leads)
    first_17_leads = all_leads[:17]
    unique_domains = list(dict.fromkeys(r[7] for r in all_leads if r[7]))

    print("=" * 100)
    print(f"LEAD ACCURACY & MULTI-LAYER GUARDRAIL AUDIT")
    print(f"Batch: {batch_name} | Total Saved Leads: {total_leads} | Unique Domains: {len(unique_domains)}")
    print("=" * 100)

    conn = api.get_connection()

    if not api._domain_trie.loaded:
        api.load_domain_trie()

    # Pre-fetch CRM domain and person matches
    print("\n[Phase 1] Evaluating All Leads Against 7.47M CRM Records & Cross-Batches...")
    
    # 1. Check if any contact (full_name + domain) or domain is in CRM
    crm_exact_contacts = set()
    crm_exact_domains = set()
    with conn.cursor() as cur:
        chunk_size = 500
        for i in range(0, len(unique_domains), chunk_size):
            chunk = unique_domains[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT full_name, domain FROM emails WHERE domain IN ({fmt})", tuple(chunk))
            for fn, dom in cur.fetchall():
                crm_exact_domains.add(dom)
                if fn:
                    crm_exact_contacts.add((api.normalize_text(fn), dom))

    # 2. Check Cross-Batch overlap
    other_batch_contacts = set()
    other_batch_domains = set()
    with conn.cursor() as cur:
        chunk_size = 500
        for i in range(0, len(unique_domains), chunk_size):
            chunk = unique_domains[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT name, company_domain, batch FROM apollo_saved_leads WHERE company_domain IN ({fmt}) AND batch != %s", tuple(chunk + [batch_name]))
            for n, dom, b in cur.fetchall():
                other_batch_domains.add(dom)
                if n:
                    other_batch_contacts.add((api.normalize_text(n), dom))

    # 3. Person Name Anchor Cache
    names_to_check = list(set(r[2] for r in all_leads if r[2]))
    db_name_to_domains = {}
    with conn.cursor() as cur:
        chunk_size = 300
        for i in range(0, len(names_to_check), chunk_size):
            chunk = names_to_check[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT full_name, domain FROM emails WHERE full_name IN ({fmt})", tuple(chunk))
            for fn, dom in cur.fetchall():
                if dom:
                    if fn not in db_name_to_domains:
                        db_name_to_domains[fn] = set()
                    db_name_to_domains[fn].add(dom)

    # 4. Job Title Evaluations
    all_titles = list(set(r[5] for r in all_leads if r[5]))
    title_evals = api.lookup_job_titles_batch(all_titles, connection=conn)

    print(f"  ✓ CRM Domains in Database: {len(crm_exact_domains)}")
    print(f"  ✓ Other Batches Overlap:   {len(other_batch_domains)}")
    print(f"  ✓ Cached Unique Titles:    {len(title_evals)}")

    # Function to audit a subset of leads
    def evaluate_lead_set(leads_subset, label):
        print("\n" + "=" * 100)
        print(f"AUDIT SUMMARY: {label} ({len(leads_subset)} Leads)")
        print("=" * 100)
        
        passed_dedup = 0
        passed_titles = 0
        passed_demographics = 0
        flawless_leads = 0

        title_categories = {}
        disqualified_reasons = []

        for idx, r in enumerate(leads_subset, 1):
            lead_id, apollo_id, name, fn, ln, title, comp, dom, web, seg = r
            norm_name = api.normalize_text(name)
            slug = api._clean_slug(dom)

            # --- Layer 1: CRM & Batch Dupes ---
            in_crm = dom in crm_exact_domains or (norm_name, dom) in crm_exact_contacts
            in_other_batch = dom in other_batch_domains or (norm_name, dom) in other_batch_contacts

            # --- Layer 2: Person LCS Overlap ---
            l2_overlap = False
            for db_d in db_name_to_domains.get(name, []):
                db_slug = api._clean_slug(db_d)
                ratio, common = api._lcs_ratio(db_slug, slug)
                if common and common.lower() not in api.GENERIC_CORPORATE_WORDS and common.lower() not in api.GENERIC_DICTIONARY_STEMS:
                    if (ratio >= 0.65 and len(common) >= 4) or (ratio >= 0.50 and len(common) >= 5) or len(common) >= 7:
                        l2_overlap = True
                        break

            # --- Layer 3: Prefix Trie ---
            trie_match, prefix = api._domain_trie.find_prefix_match(slug, min_prefix_len=4)
            l3_branch = bool(trie_match and trie_match != dom and api._is_valid_branch_match(trie_match, prefix, slug))

            dedup_clean = not (in_crm or in_other_batch or l2_overlap or l3_branch)
            if dedup_clean:
                passed_dedup += 1

            # --- Layer 5: Job Title Hierarchy ---
            ev = title_evals.get(title, {})
            is_title_req = ev.get('required', False)
            title_seg = ev.get('segment', 'Unknown')
            title_categories[title_seg] = title_categories.get(title_seg, 0) + 1
            if is_title_req:
                passed_titles += 1

            # --- Layer 6: Demographics ---
            is_indian, ind_reason = api.is_unambiguous_pure_indian_name(name)
            if not is_indian:
                passed_demographics += 1

            # --- Flawless Criteria: Net-New Domain + Qualified Title + Clean Demography ---
            if dedup_clean and is_title_req and not is_indian:
                flawless_leads += 1
            else:
                disqualified_reasons.append({
                    "name": name, "title": title, "comp": comp, "dom": dom,
                    "dedup_clean": dedup_clean, "is_title_req": is_title_req,
                    "title_seg": title_seg, "is_indian": is_indian
                })

        print(f"1. DEDUPLICATION ACCURACY (Zero CRM / Cross-Batch Duplicates): {passed_dedup} / {len(leads_subset)} ({passed_dedup/len(leads_subset)*100:.2f}%)")
        print(f"2. DEMOGRAPHIC ACCURACY (Passed Indian Name Guardrail):       {passed_demographics} / {len(leads_subset)} ({passed_demographics/len(leads_subset)*100:.2f}%)")
        print(f"3. DECISION-MAKER TITLE ACCURACY (Qualified Hierarchy Tier):    {passed_titles} / {len(leads_subset)} ({passed_titles/len(leads_subset)*100:.2f}%)")
        print(f"4. OVERALL FLAWLESS QUALIFIED LEADS (Clean + Qualified):        {flawless_leads} / {len(leads_subset)} ({flawless_leads/len(leads_subset)*100:.2f}%)")
        
        print("\nTop Job Title Categories:")
        for s, count in sorted(title_categories.items(), key=lambda x: x[1], reverse=True)[:8]:
            print(f"   • {s:35s}: {count:4d} ({count/len(leads_subset)*100:5.1f}%)")

        if disqualified_reasons and len(leads_subset) <= 20:
            print("\nNon-Qualified / Secondary Leads in Sample:")
            for d in disqualified_reasons:
                reasons = []
                if not d['dedup_clean']: reasons.append("Duplicate in CRM/Batch")
                if not d['is_title_req']: reasons.append(f"Title: {d['title_seg']}")
                if d['is_indian']: reasons.append("Demographic Filter")
                print(f"   - {d['name']} ({d['title']}) at {d['comp']} -> {', '.join(reasons)}")

        return {
            "total": len(leads_subset),
            "passed_dedup": passed_dedup,
            "passed_titles": passed_titles,
            "passed_demographics": passed_demographics,
            "flawless_leads": flawless_leads,
            "title_categories": title_categories
        }

    # Run for the initial 17 leads
    res_17 = evaluate_lead_set(first_17_leads, "INITIAL 17 LEADS")

    # Run for the full current batch (1,415 leads)
    res_all = evaluate_lead_set(all_leads, f"ENTIRE BATCH ({total_leads} LEADS)")

if __name__ == '__main__':
    main()
