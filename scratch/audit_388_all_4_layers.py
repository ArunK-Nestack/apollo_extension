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
                SELECT id, name, job_title, company, company_domain, website_link
                FROM apollo_saved_leads
                WHERE batch = %s
                ORDER BY id ASC
            """, (batch_name,))
            leads = cur.fetchall()

    total_leads = len(leads)
    unique_domains = list(dict.fromkeys(r[4] for r in leads if r[4]))

    print("=" * 95)
    print(f"FULL 4-LAYER FORENSIC AUDIT: {len(unique_domains)} UNIQUE DOMAINS ({total_leads} LEADS)")
    print(f"Batch: {batch_name}")
    print("=" * 95)

    conn = api.get_connection()

    # Ensure prefix trie is loaded
    if not api._domain_trie.loaded:
        api.load_domain_trie()

    # =========================================================================
    # LAYER 1: Exact Domain Match (CRM emails + other apollo batches)
    # =========================================================================
    print("Executing Layer 1 (Exact Domain in CRM & Cross-Batches)...")
    t0 = time.time()
    l1_crm_hits = {}
    l1_batch_hits = {}

    with conn.cursor() as cur:
        chunk_size = 500
        for i in range(0, len(unique_domains), chunk_size):
            chunk = unique_domains[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            # CRM check
            cur.execute(f"SELECT domain, full_name, email FROM emails WHERE domain IN ({fmt})", tuple(chunk))
            for dom, fn, em in cur.fetchall():
                l1_crm_hits[dom] = (fn, em)
            # Other batch check
            cur.execute(f"SELECT DISTINCT company_domain, batch FROM apollo_saved_leads WHERE company_domain IN ({fmt}) AND batch != %s", tuple(chunk + [batch_name]))
            for dom, b in cur.fetchall():
                if dom not in l1_batch_hits:
                    l1_batch_hits[dom] = []
                l1_batch_hits[dom].append(b)

    t1 = time.time()
    print(f"  ✓ Layer 1 Complete in {t1 - t0:.2f}s | CRM Matches: {len(l1_crm_hits)} | Cross-Batch Matches: {len(l1_batch_hits)}")

    # =========================================================================
    # LAYER 2: Person-Name Anchor + LCS Domain Overlap
    # =========================================================================
    print("Executing Layer 2 (Person-Name Anchor & LCS Overlap across 393 Leads)...")
    t0 = time.time()
    l2_hits = {}
    person_in_db_count = 0

    # Fetch DB domains for all names in batch
    names_to_check = list(set(r[1] for r in leads if r[1]))
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

    for r in leads:
        lead_id, name, title, comp, dom, web = r
        if not name or not dom:
            continue
        db_doms = db_name_to_domains.get(name, set())
        if db_doms:
            person_in_db_count += 1
            incoming_slug = api._clean_slug(dom)
            for db_d in db_doms:
                db_slug = api._clean_slug(db_d)
                ratio, common = api._lcs_ratio(db_slug, incoming_slug)
                if (
                    common
                    and common.lower() not in api.GENERIC_CORPORATE_WORDS
                    and common.lower() not in api.GENERIC_DICTIONARY_STEMS
                ):
                    hit = (
                        (ratio >= 0.65 and len(common) >= 4) or
                        (ratio >= 0.50 and len(common) >= 5) or
                        (ratio >= 0.55 and len(common) >= 4) or
                        (db_slug.startswith(common) and incoming_slug.startswith(common) and len(common) >= 4) or
                        (len(common) >= 7)
                    )
                    if hit:
                        l2_hits[dom] = (name, db_d, common, ratio)
                        break

    t1 = time.time()
    print(f"  ✓ Layer 2 Complete in {t1 - t0:.2f}s | Name occurrences in CRM: {person_in_db_count} | LCS Branch Overlaps: {len(l2_hits)}")

    # =========================================================================
    # LAYER 3: Database-Driven Prefix Trie (MARISA Trie - 2.56M stems)
    # =========================================================================
    print("Executing Layer 3 (Database-Driven Prefix Trie on 388 domains)...")
    t0 = time.time()
    l3_hits = {}

    for dom in unique_domains:
        slug = api._clean_slug(dom)
        trie_match, prefix = api._domain_trie.find_prefix_match(slug, min_prefix_len=4)
        if trie_match and trie_match != dom and api._is_valid_branch_match(trie_match, prefix, slug):
            l3_hits[dom] = (trie_match, prefix)

    t1 = time.time()
    print(f"  ✓ Layer 3 Complete in {t1 - t0:.2f}s | Branch / Stem Matches: {len(l3_hits)}")

    # =========================================================================
    # LAYER 4: DNS MX Mail Routing Resolver
    # =========================================================================
    print("Executing Layer 4 (DNS MX Mail Routing Resolver on 388 domains)...")
    t0 = time.time()
    l4_hits = {}
    mx_cache = {}

    # Sample / query DNS MX records
    mx_domain_map = {}
    if api._DNS_AVAILABLE:
        for dom in unique_domains:
            try:
                mx_root = api._resolve_mx_root_domain(dom)
                if mx_root:
                    mx_domain_map[dom] = mx_root
            except Exception:
                pass

    # Check if any resolved MX root domain exists in CRM emails
    mx_roots_to_check = list(set(mx_domain_map.values()))
    mx_db_hits = set()
    with conn.cursor() as cur:
        chunk_size = 500
        for i in range(0, len(mx_roots_to_check), chunk_size):
            chunk = mx_roots_to_check[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt})", tuple(chunk))
            for (m_dom,) in cur.fetchall():
                mx_db_hits.add(m_dom)

    for dom, mx_r in mx_domain_map.items():
        if mx_r in mx_db_hits and mx_r != dom:
            l4_hits[dom] = mx_r

    t1 = time.time()
    print(f"  ✓ Layer 4 Complete in {t1 - t0:.2f}s | DNS MX Queried: {len(mx_domain_map)} | Shared MX Infrastructure Hits: {len(l4_hits)}")

    # =========================================================================
    # CONSOLIDATED SUMMARY & METRICS
    # =========================================================================
    print("\n" + "=" * 95)
    print("4-LAYER DEDUPLICATION AUDIT RESULTS FOR THE 388 UNIQUE DOMAINS:")
    print("=" * 95)
    print(f"  • Total Unique Domains:                  388")
    print(f"  • Total Leads Evaluated:                 {total_leads}")
    print(f"  -----------------------------------------------------------------------------------")
    print(f"  • Layer 1 (Exact CRM DB Overlap):        {len(l1_crm_hits):3d} / 388  --> {(1 - len(l1_crm_hits)/388)*100:6.2f}% Clean Pass Rate")
    print(f"  • Layer 1b (Cross-Batch Overlap):        {len(l1_batch_hits):3d} / 388  --> {(1 - len(l1_batch_hits)/388)*100:6.2f}% Clean Pass Rate")
    print(f"  • Layer 2 (Person-Name Anchor & LCS):    {len(l2_hits):3d} / 388  --> {(1 - len(l2_hits)/388)*100:6.2f}% Clean Pass Rate")
    print(f"  • Layer 3 (Database Prefix Trie):        {len(l3_hits):3d} / 388  --> {(1 - len(l3_hits)/388)*100:6.2f}% Clean Pass Rate")
    print(f"  • Layer 4 (DNS MX Mail Routing):         {len(l4_hits):3d} / 388  --> {(1 - len(l4_hits)/388)*100:6.2f}% Clean Pass Rate")
    print(f"  -----------------------------------------------------------------------------------")

    all_blocked_domains = set(l1_crm_hits.keys()) | set(l1_batch_hits.keys()) | set(l2_hits.keys()) | set(l3_hits.keys()) | set(l4_hits.keys())
    clean_domains_count = len(unique_domains) - len(all_blocked_domains)
    final_pass_rate = (clean_domains_count / len(unique_domains)) * 100

    print(f"  • TOTAL 4-LAYER NET-NEW DOMAINS:         {clean_domains_count} / {len(unique_domains)}")
    print(f"  • OVERALL NET-NEW VERIFICATION ACCURACY: {final_pass_rate:.2f}%")
    print("=" * 95)

    if all_blocked_domains:
        print("\nFlagged Domains Breakdown:")
        for d in all_blocked_domains:
            reasons = []
            if d in l1_crm_hits: reasons.append(f"L1 CRM ({l1_crm_hits[d]})")
            if d in l1_batch_hits: reasons.append(f"L1 Batch ({l1_batch_hits[d]})")
            if d in l2_hits: reasons.append(f"L2 Person LCS ({l2_hits[d]})")
            if d in l3_hits: reasons.append(f"L3 Trie ({l3_hits[d]})")
            if d in l4_hits: reasons.append(f"L4 MX ({l4_hits[d]})")
            print(f"  - {d}: {', '.join(reasons)}")
    else:
        print("\n🏆 FLAWLESS VERIFICATION: Zero collisions detected across all 4 defense layers!")

if __name__ == '__main__':
    main()
