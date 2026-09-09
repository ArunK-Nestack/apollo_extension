import sys
import os
sys.path.insert(0, ".")

import backend.api as api

def audit_388():
    batch_name = 'abel_abraham_nestacktechnologies_com-sep'
    with api.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, job_title, company, company_domain, website_link, segment, created_at
                FROM apollo_saved_leads
                WHERE batch = %s
                ORDER BY id ASC
            """, (batch_name,))
            leads = cur.fetchall()

    total_leads = len(leads)
    domains = [r[4] for r in leads if r[4]]
    unique_domains = list(dict.fromkeys(domains))
    
    print("=" * 95)
    print(f"AUDIT OF BATCH '{batch_name}': {len(unique_domains)} UNIQUE DOMAINS ({total_leads} LEADS)")
    print("=" * 95)

    conn = api.get_connection()

    # 1. Multi-contact per company analysis (why 393 leads vs 388 domains)
    domain_to_leads = {}
    for r in leads:
        d = r[4]
        if d not in domain_to_leads:
            domain_to_leads[d] = []
        domain_to_leads[d].append(r)

    multi_leads = {d: l for d, l in domain_to_leads.items() if len(l) > 1}
    print(f"\n1. INTERNAL BATCH REPETITION (Leads vs Domains):")
    print(f"   • Total Leads Saved:           {total_leads}")
    print(f"   • Distinct Company Domains:    {len(unique_domains)}")
    print(f"   • Companies with >1 Lead:      {len(multi_leads)}")
    for d, l_list in multi_leads.items():
        names = [f"{item[1]} ({item[2]})" for item in l_list]
        print(f"     - Domain '{d}': {len(l_list)} leads -> {', '.join(names)}")

    # 2. Check CRM emails table (7.47M rows)
    print(f"\n2. LAYER 1: DIRECT CRM DATABASE CHECK (7,469,053 records):")
    crm_matches = {}
    with conn.cursor() as cur:
        chunk_size = 500
        for i in range(0, len(unique_domains), chunk_size):
            chunk = unique_domains[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT domain, full_name, email FROM emails WHERE domain IN ({fmt})", tuple(chunk))
            for dom, fn, em in cur.fetchall():
                crm_matches[dom] = (fn, em)

    print(f"   • Matches in CRM Database:     {len(crm_matches)} / {len(unique_domains)}")
    for dom, (fn, em) in list(crm_matches.items())[:10]:
        print(f"     ❌ Found in CRM: '{dom}' (Contact: {fn}, Email: {em})")
    if not crm_matches:
        print("   ✅ ZERO OVERLAP: 100% of the 388 domains are completely net-new to your CRM database!")

    # 3. Check Cross-Batch Overlaps (other team batches in apollo_saved_leads)
    print(f"\n3. CROSS-BATCH CHECK (Other scraping batches in apollo_saved_leads):")
    other_batch_matches = {}
    with conn.cursor() as cur:
        chunk_size = 500
        for i in range(0, len(unique_domains), chunk_size):
            chunk = unique_domains[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT DISTINCT company_domain, batch FROM apollo_saved_leads WHERE company_domain IN ({fmt}) AND batch != %s", tuple(chunk + [batch_name]))
            for dom, b in cur.fetchall():
                if dom not in other_batch_matches:
                    other_batch_matches[dom] = []
                other_batch_matches[dom].append(b)

    print(f"   • Collisions with Other Batches: {len(other_batch_matches)} / {len(unique_domains)}")
    for dom, b_list in list(other_batch_matches.items())[:10]:
        print(f"     ⚠️ Found in other batch: '{dom}' in {b_list}")
    if not other_batch_matches:
        print("   ✅ ZERO CROSS-BATCH OVERLAP: 0 domains collide with any other team batch!")

    # 4. Layer 3: Database Prefix Trie Check
    print(f"\n4. LAYER 3: DATABASE PREFIX TRIE (2.56M unique domain roots):")
    if not api._domain_trie.loaded:
        api.load_domain_trie()

    trie_branch_matches = {}
    for d in unique_domains:
        slug = api._clean_slug(d)
        trie_match, prefix = api._domain_trie.find_prefix_match(slug, min_prefix_len=4)
        if trie_match and trie_match != d and api._is_valid_branch_match(trie_match, prefix, slug):
            trie_branch_matches[d] = (trie_match, prefix)

    print(f"   • Branch / Subsidiary Matches:  {len(trie_branch_matches)} / {len(unique_domains)}")
    for d, (mdb, pref) in list(trie_branch_matches.items())[:10]:
        print(f"     ⚠️ Branch match: '{d}' stems from known DB domain '{mdb}' (root: '{pref}')")
    if not trie_branch_matches:
        print("   ✅ ZERO BRANCH COLLISIONS: 0 domains conflict with known dealership or company roots!")

    # 5. Job Title Categories Breakdown across all 393 leads
    print(f"\n5. JOB TITLE CATEGORIES & SENIORITY BREAKDOWN:")
    titles = [r[2] for r in leads]
    title_evals = api.lookup_job_titles_batch(titles, connection=conn)

    category_counts = {}
    required_count = 0
    non_required_count = 0

    for r in leads:
        t = r[2]
        ev = title_evals.get(t, {})
        seg = ev.get('segment', 'Unknown')
        is_req = ev.get('required', False)
        if is_req:
            required_count += 1
        else:
            non_required_count += 1
        category_counts[seg] = category_counts.get(seg, 0) + 1

    for seg, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"     - {seg:35s}: {count:3d} leads ({count/total_leads*100:4.1f}%)")

    # 6. Overall Accuracy Calculation
    print("\n" + "=" * 95)
    print("EXECUTIVE ACCURACY SCORECARD FOR THE 388 UNIQUE DOMAINS:")
    print("=" * 95)
    clean_domains_crm = len(unique_domains) - len(crm_matches)
    clean_domains_all = len(unique_domains) - len(crm_matches) - len(other_batch_matches) - len(trie_branch_matches)
    crm_accuracy = clean_domains_crm / len(unique_domains) * 100
    overall_net_new_accuracy = clean_domains_all / len(unique_domains) * 100
    high_priority_ratio = (required_count / total_leads) * 100

    print(f"  1. CRM Domain Net-New Rate:       {clean_domains_crm} / {len(unique_domains)} ({crm_accuracy:.2f}%)")
    print(f"  2. Cross-Batch Independence:      {len(unique_domains) - len(other_batch_matches)} / {len(unique_domains)} ({(len(unique_domains)-len(other_batch_matches))/len(unique_domains)*100:.2f}%)")
    print(f"  3. Prefix Trie Branch Independence:{len(unique_domains) - len(trie_branch_matches)} / {len(unique_domains)} ({(len(unique_domains)-len(trie_branch_matches))/len(unique_domains)*100:.2f}%)")
    print(f"  4. True 100% Net-New Domains:     {clean_domains_all} / {len(unique_domains)} ({overall_net_new_accuracy:.2f}%)")
    print(f"  5. Qualified Decision Maker Ratio: {required_count} / {total_leads} ({high_priority_ratio:.2f}%)")
    print("=" * 95)

if __name__ == '__main__':
    audit_388()
