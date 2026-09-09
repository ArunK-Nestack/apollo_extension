import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")

import backend.api as api

def main():
    batch_name = 'abel_abraham_nestacktechnologies_com-sep'
    print(f"===============================================================================================", flush=True)
    print(f"FORENSIC 4-LAYER DEDUPLICATION AUDIT FOR BATCH: {batch_name}", flush=True)
    print(f"===============================================================================================", flush=True)

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
    domain_counts = {}
    for r in leads:
        d = (r[4] or "").strip().lower()
        if d:
            domain_counts[d] = domain_counts.get(d, 0) + 1

    unique_domains = list(domain_counts.keys())
    intra_batch_dupes = {d: cnt for d, cnt in domain_counts.items() if cnt > 1}

    print(f"Total Leads in Batch:          {total_leads:,d}", flush=True)
    print(f"Unique Domains in Batch:       {len(unique_domains):,d}", flush=True)
    print(f"Intra-batch Shared Domains:    {len(intra_batch_dupes):,d} domains ({total_leads - len(unique_domains):,d} redundant leads)", flush=True)
    print("-" * 95, flush=True)

    conn = api.get_connection()

    # Load MARISA Trie
    if not api._domain_trie.loaded:
        api.load_domain_trie()

    # =========================================================================
    # LAYER 1: Exact Domain Match (CRM emails + other apollo batches)
    # =========================================================================
    print("\n[LAYER 1] Checking Exact Domain Matches in Master CRM (`emails` table, 7.47M rows) & Cross-Batches...", flush=True)
    t0 = time.time()
    l1_crm_hits = {}
    l1_batch_hits = {}

    with conn.cursor() as cur:
        chunk_size = 500
        for i in range(0, len(unique_domains), chunk_size):
            chunk = unique_domains[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            # 1a. Master CRM check
            cur.execute(f"SELECT domain, full_name, email FROM emails WHERE domain IN ({fmt})", tuple(chunk))
            for dom, fn, em in cur.fetchall():
                l1_crm_hits[dom] = (fn, em)
            # 1b. Other batch check
            cur.execute(f"SELECT DISTINCT company_domain, batch FROM apollo_saved_leads WHERE company_domain IN ({fmt}) AND batch != %s", tuple(chunk + [batch_name]))
            for dom, b in cur.fetchall():
                if dom not in l1_batch_hits:
                    l1_batch_hits[dom] = []
                l1_batch_hits[dom].append(b)

    t1 = time.time()
    print(f"  ✓ Layer 1 Complete in {t1 - t0:.2f}s", flush=True)
    print(f"    • Exact CRM Matches:         {len(l1_crm_hits):,d} / {len(unique_domains):,d}", flush=True)
    print(f"    • Cross-Batch Collisions:    {len(l1_batch_hits):,d} / {len(unique_domains):,d}", flush=True)

    # =========================================================================
    # LAYER 2: Person-Name Anchor + LCS Domain Overlap
    # =========================================================================
    print("\n[LAYER 2] Checking Person-Name Anchor & LCS Domain Overlaps...", flush=True)
    t0 = time.time()
    l2_hits = {}
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

    person_in_db_count = 0
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
    print(f"  ✓ Layer 2 Complete in {t1 - t0:.2f}s", flush=True)
    print(f"    • Names present in CRM:      {person_in_db_count:,d}", flush=True)
    print(f"    • Fuzzy LCS Person Overlaps: {len(l2_hits):,d} / {len(unique_domains):,d}", flush=True)

    # =========================================================================
    # LAYER 3: Database-Driven Prefix Trie (MARISA Trie - 2.56M stems)
    # =========================================================================
    print("\n[LAYER 3] Checking MARISA-Trie Prefix & Branch Matches (2.56M Unique Root Slugs)...", flush=True)
    t0 = time.time()
    l3_hits = {}

    for dom in unique_domains:
        slug = api._clean_slug(dom)
        trie_match, prefix = api._domain_trie.find_prefix_match(slug, min_prefix_len=4)
        if trie_match and trie_match != dom and api._is_valid_branch_match(trie_match, prefix, slug):
            l3_hits[dom] = (trie_match, prefix)

    t1 = time.time()
    print(f"  ✓ Layer 3 Complete in {t1 - t0:.2f}s", flush=True)
    print(f"    • Brand-Branch / Trie Conflicts: {len(l3_hits):,d} / {len(unique_domains):,d}", flush=True)

    # =========================================================================
    # LAYER 4: Multi-Threaded DNS MX Mail Routing Resolver
    # =========================================================================
    print("\n[LAYER 4] Resolving DNS MX Mail Routing across 4,203 domains (50 concurrent workers)...", flush=True)
    t0 = time.time()
    l4_hits = {}
    mx_domain_map = {}

    if api._DNS_AVAILABLE:
        def resolve_single(d):
            try:
                mx_r = api._resolve_mx_root_domain(d)
                return (d, mx_r) if mx_r else None
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=50) as executor:
            results = list(executor.map(resolve_single, unique_domains))
            for res in results:
                if res:
                    mx_domain_map[res[0]] = res[1]

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
    print(f"  ✓ Layer 4 Complete in {t1 - t0:.2f}s", flush=True)
    print(f"    • DNS MX Records Queried:    {len(mx_domain_map):,d}", flush=True)
    print(f"    • Shared Mail Infrastructure: {len(l4_hits):,d} / {len(unique_domains):,d}", flush=True)

    # =========================================================================
    # CONSOLIDATED SUMMARY & METRICS
    # =========================================================================
    print("\n" + "=" * 95, flush=True)
    print(f"FINAL AUDIT SCORECARD: BATCH '{batch_name}'", flush=True)
    print("=" * 95, flush=True)
    print(f"  • Total Leads in Batch:                  {total_leads:,d}", flush=True)
    print(f"  • Total Unique Domains:                  {len(unique_domains):,d}", flush=True)
    print(f"  • Redundant Leads within Batch:          {total_leads - len(unique_domains):,d} (same company, duplicate contacts)", flush=True)
    print("  " + "-" * 91, flush=True)
    print(f"  • Layer 1 (Exact CRM DB Overlap):        {len(l1_crm_hits):4d} collisions  ({(1 - len(l1_crm_hits)/len(unique_domains))*100:6.2f}% clean)", flush=True)
    print(f"  • Layer 1b (Cross-Batch Overlap):        {len(l1_batch_hits):4d} collisions  ({(1 - len(l1_batch_hits)/len(unique_domains))*100:6.2f}% clean)", flush=True)
    print(f"  • Layer 2 (Person-Name Anchor & LCS):    {len(l2_hits):4d} collisions  ({(1 - len(l2_hits)/len(unique_domains))*100:6.2f}% clean)", flush=True)
    print(f"  • Layer 3 (Database Prefix Trie):        {len(l3_hits):4d} collisions  ({(1 - len(l3_hits)/len(unique_domains))*100:6.2f}% clean)", flush=True)
    print(f"  • Layer 4 (DNS MX Mail Routing):         {len(l4_hits):4d} collisions  ({(1 - len(l4_hits)/len(unique_domains))*100:6.2f}% clean)", flush=True)
    print("  " + "-" * 91, flush=True)

    all_blocked_domains = set(l1_crm_hits.keys()) | set(l1_batch_hits.keys()) | set(l2_hits.keys()) | set(l3_hits.keys()) | set(l4_hits.keys())
    clean_domains_count = len(unique_domains) - len(all_blocked_domains)
    final_pass_rate = (clean_domains_count / len(unique_domains)) * 100

    print(f"  • VERIFIED NET-NEW UNIQUE DOMAINS:       {clean_domains_count:,d} / {len(unique_domains):,d} ({final_pass_rate:.2f}%)", flush=True)
    print(f"  • TOTAL CONFLICTING DOMAINS FLAGGED:     {len(all_blocked_domains):,d}", flush=True)
    print("=" * 95, flush=True)

    if all_blocked_domains:
        print("\nFlagged Domains Sample (First 20):", flush=True)
        for i, d in enumerate(sorted(all_blocked_domains)[:20], 1):
            reasons = []
            if d in l1_crm_hits: reasons.append(f"L1 CRM: {l1_crm_hits[d][0]} ({l1_crm_hits[d][1]})")
            if d in l1_batch_hits: reasons.append(f"L1 Cross-Batch: {l1_batch_hits[d]}")
            if d in l2_hits: reasons.append(f"L2 LCS: {l2_hits[d][0]} @ {l2_hits[d][1]}")
            if d in l3_hits: reasons.append(f"L3 Trie: matches root '{l3_hits[d][1]}'")
            if d in l4_hits: reasons.append(f"L4 DNS MX: routes to '{l4_hits[d]}'")
            print(f"  [{i:02d}] {d:<30} -> {', '.join(reasons)}", flush=True)

if __name__ == '__main__':
    main()
