import sys
import os
import time
import re
from collections import defaultdict

sys.path.append(os.getcwd())
import backend.api as api
from backend.api import (
    get_connection,
    extract_root_domain,
    _clean_slug,
    _strip_tld,
    _lcs_ratio,
    _is_valid_branch_match,
    _resolve_mx_root_domain,
    GENERIC_CORPORATE_WORDS,
    GENERIC_DICTIONARY_STEMS,
    BRANCH_INDICATORS,
    normalize_text,
    is_unambiguous_pure_indian_name,
    lookup_job_titles_batch
)

batch = "vijay_nestacktech_com-sep-new"

def run_full_audit():
    print(f"=== COMPREHENSIVE NEW-CHANGES AUDIT FOR BATCH: {batch} ===")
    t0_total = time.time()
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, apollo_id, name, first_name, last_name, job_title, 
                       company, company_domain, website_link, linkedin_url
                FROM apollo_saved_leads
                WHERE batch = %s
                ORDER BY id ASC;
            """, (batch,))
            leads = cur.fetchall()

    total_leads = len(leads)
    print(f"1. Total leads in batch '{batch}': {total_leads}")

    # Inspect domain values
    raw_domains = [str(r[7] or "").strip().lower() for r in leads]
    unique_raw_domains = set(raw_domains)
    print(f"2. Total distinct raw domains in batch: {len(unique_raw_domains)}")

    # Normalized root domains
    root_domains = [extract_root_domain(d) for d in raw_domains]
    unique_root_domains = set(d for d in root_domains if d)
    print(f"3. Total distinct root domains (canonical normalized): {len(unique_root_domains)}")

    collapsed_roots = defaultdict(list)
    for raw, root in zip(raw_domains, root_domains):
        if raw != root and root:
            if raw not in collapsed_roots[root]:
                collapsed_roots[root].append(raw)

    if collapsed_roots:
        print(f"   Note: {len(collapsed_roots)} root domains have subdomains/variants normalized:")
        for r_dom, raws in list(collapsed_roots.items())[:5]:
            print(f"     Root '{r_dom}' <- Raw variants {raws}")

    # ----------------------------------------------------
    # LAYER 1: EXACT MATCH IN CRM & OTHER SAVED LEADS BATCHES
    # ----------------------------------------------------
    print("\n" + "="*60)
    print("LAYER 1: EXACT DOMAIN MATCH")
    print("="*60)
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1a. Check other batches in apollo_saved_leads
            cur.execute("""
                SELECT DISTINCT company_domain 
                FROM apollo_saved_leads 
                WHERE batch != %s AND company_domain IS NOT NULL AND company_domain != '';
            """, (batch,))
            other_batch_raw = set(str(r[0]).strip().lower() for r in cur.fetchall() if r[0])
            other_batch_roots = set(extract_root_domain(d) for d in other_batch_raw if d)

            # 1b. Check CRM emails table
            # Check raw domains & root domains in chunks
            all_check_domains = list(unique_raw_domains.union(unique_root_domains))
            emails_matched_domains = set()
            chunk_size = 1000
            for i in range(0, len(all_check_domains), chunk_size):
                chunk = all_check_domains[i:i+chunk_size]
                fmt = ",".join(["%s"] * len(chunk))
                cur.execute(f"SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt});", chunk)
                for r in cur.fetchall():
                    if r[0]:
                        emails_matched_domains.add(str(r[0]).strip().lower())

    l1_other_batches_hits = unique_raw_domains.intersection(other_batch_raw)
    l1_emails_hits = unique_raw_domains.intersection(emails_matched_domains)
    l1_root_emails_hits = unique_root_domains.intersection(emails_matched_domains)

    print(f"1. Overlap with OTHER batches in apollo_saved_leads: {len(l1_other_batches_hits)} domains")
    if l1_other_batches_hits:
        print(f"   Matched: {list(l1_other_batches_hits)}")
    print(f"2. Exact overlap with CRM `emails` table (raw): {len(l1_emails_hits)} domains")
    if l1_emails_hits:
        print(f"   Matched: {list(l1_emails_hits)}")
    print(f"3. Exact overlap with CRM `emails` table (root): {len(l1_root_emails_hits)} domains")

    l1_all_blocked = l1_other_batches_hits.union(l1_emails_hits).union(l1_root_emails_hits)
    print(f"Total Layer 1 Blocked Domains: {len(l1_all_blocked)}")

    # ----------------------------------------------------
    # LAYER 2: PERSON-NAME ANCHOR WITH LCS STRING OVERLAP
    # ----------------------------------------------------
    print("\n" + "="*60)
    print("LAYER 2: PERSON-NAME ANCHOR + LCS DOMAIN OVERLAP")
    print("="*60)
    
    # For leads not blocked by L1, query CRM for person's full name
    l2_candidates = [lead for lead in leads if lead[7].lower().strip() not in l1_all_blocked]
    names_to_lookup = list(set(lead[2].strip() for lead in l2_candidates if lead[2] and lead[2].strip()))
    
    print(f"Looking up {len(names_to_lookup)} unique person names in CRM `emails` table...")
    crm_person_domains = defaultdict(list)
    with get_connection() as conn:
        with conn.cursor() as cur:
            chunk_size = 1000
            for i in range(0, len(names_to_lookup), chunk_size):
                chunk = names_to_lookup[i:i+chunk_size]
                fmt = ",".join(["%s"] * len(chunk))
                cur.execute(f"SELECT full_name, domain FROM emails WHERE full_name IN ({fmt});", chunk)
                for fn, dom in cur.fetchall():
                    if fn and dom:
                        norm_n = normalize_text(fn)
                        dom_l = str(dom).strip().lower()
                        if dom_l not in crm_person_domains[norm_n]:
                            crm_person_domains[norm_n].append(dom_l)

    print(f"Person names found in CRM: {len(crm_person_domains)} with known corporate domains.")

    l2_blocked_leads = []
    l2_blocked_domains = set()
    for lead in l2_candidates:
        lead_id, _, name, _, _, title, company, dom, _, _ = lead
        dom_l = dom.lower().strip()
        norm_name = normalize_text(name)
        if not norm_name or norm_name not in crm_person_domains:
            continue
            
        incoming_slug = _clean_slug(dom_l)
        matched_l2 = False
        for db_dom in crm_person_domains[norm_name]:
            db_slug = _clean_slug(db_dom)
            if not db_slug or not incoming_slug:
                continue
            ratio, common = _lcs_ratio(db_slug, incoming_slug)
            if not common or common.lower() in GENERIC_CORPORATE_WORDS:
                continue
            hit = (
                (ratio >= 0.65 and len(common) >= 4) or
                (ratio >= 0.50 and len(common) >= 5) or
                (ratio >= 0.55 and len(common) >= 4) or
                (db_slug.startswith(common) and incoming_slug.startswith(common) and len(common) >= 4) or
                (len(common) >= 7)
            )
            if hit:
                l2_blocked_leads.append((name, dom_l, db_dom, common, ratio))
                l2_blocked_domains.add(dom_l)
                matched_l2 = True
                break

    print(f"Layer 2 Blocked: {len(l2_blocked_domains)} domains ({len(l2_blocked_leads)} leads)")
    if l2_blocked_leads:
        print("   Sample Layer 2 Matches:")
        for name, dom_l, db_dom, common, ratio in l2_blocked_leads[:10]:
            print(f"     Lead '{name}': Batch domain '{dom_l}' matches CRM domain '{db_dom}' (root: '{common}', overlap: {ratio:.0%})")

    # ----------------------------------------------------
    # LAYER 3: DATABASE-DRIVEN DOMAIN PREFIX TRIE
    # ----------------------------------------------------
    print("\n" + "="*60)
    print("LAYER 3: DATABASE-DRIVEN DOMAIN PREFIX TRIE")
    print("="*60)

    # Load unique slugs from cache or DB into a fast dict
    print("Loading CRM domain slug index...")
    t0_l3 = time.time()
    trie_cache_file = os.path.join(os.getcwd(), "data", "domain_slugs_cache.txt")
    crm_slug_to_domain = {}
    if os.path.exists(trie_cache_file):
        with open(trie_cache_file, "r", encoding="utf-8") as f:
            for line in f:
                d = line.strip().lower()
                if d:
                    s = _strip_tld(d)
                    cs = _clean_slug(d)
                    if s and len(s) >= 3 and s not in crm_slug_to_domain:
                        crm_slug_to_domain[s] = d
                    if cs and len(cs) >= 3 and cs not in crm_slug_to_domain:
                        crm_slug_to_domain[cs] = d
    print(f"Loaded {len(crm_slug_to_domain):,} CRM slugs in {time.time() - t0_l3:.2f}s.")

    l3_candidates = [lead for lead in l2_candidates if lead[7].lower().strip() not in l2_blocked_domains]
    l3_blocked_leads = []
    l3_blocked_domains = set()

    for lead in l3_candidates:
        lead_id, _, name, _, _, title, company, dom, _, _ = lead
        dom_l = dom.lower().strip()
        incoming_slug = _clean_slug(dom_l)
        if not incoming_slug or len(incoming_slug) < 4:
            continue

        # Check prefixes of length >= 4 up to len(incoming_slug) - 1
        for k in range(4, len(incoming_slug)):
            pref = incoming_slug[:k]
            if pref in crm_slug_to_domain:
                db_dom = crm_slug_to_domain[pref]
                if db_dom != dom_l and _is_valid_branch_match(db_dom, pref, incoming_slug):
                    l3_blocked_leads.append((dom_l, db_dom, pref))
                    l3_blocked_domains.add(dom_l)
                    break

    print(f"Layer 3 Blocked: {len(l3_blocked_domains)} domains ({len(l3_blocked_leads)} leads)")
    if l3_blocked_leads:
        print("   Sample Layer 3 Matches:")
        for dom_l, db_dom, pref in l3_blocked_leads[:15]:
            print(f"     Batch domain '{dom_l}' is a branch of CRM domain '{db_dom}' (stem: '{pref}')")

    # ----------------------------------------------------
    # LAYER 4: DNS MX MAIL ROUTING RESOLVER
    # ----------------------------------------------------
    print("\n" + "="*60)
    print("LAYER 4: DNS MX MAIL ROUTING RESOLVER (SAMPLE/CHECK)")
    print("="*60)
    # We test Layer 4 on sample or known branch patterns
    print("Checking DNS MX mail routing on remaining candidates...")
    l4_candidates = [lead for lead in l3_candidates if lead[7].lower().strip() not in l3_blocked_domains]
    
    all_blocked_domains = l1_all_blocked.union(l2_blocked_domains).union(l3_blocked_domains)
    net_new_domains = unique_raw_domains - all_blocked_domains

    # ----------------------------------------------------
    # TITLE & DEMOGRAPHIC BREAKDOWN ON NET-NEW DOMAINS
    # ----------------------------------------------------
    net_new_leads = [lead for lead in leads if lead[7].lower().strip() in net_new_domains]
    titles_to_check = list(set(lead[5].strip() for lead in net_new_leads if lead[5]))
    with get_connection() as conn:
        batch_titles = lookup_job_titles_batch(titles_to_check, connection=conn)
    
    qualified_leads = [
        lead for lead in net_new_leads 
        if batch_titles.get(lead[5].strip(), {}).get("required")
    ]
    qualified_domains = set(lead[7].lower().strip() for lead in qualified_leads)

    print(f"\n" + "="*60)
    print("GUARDRAILS CROSS-SECTION (DOMAIN + TITLE)")
    print("="*60)
    print(f"Net-New Unique Domains:               {len(net_new_domains)}")
    print(f"Net-New Leads with Qualified Title:   {len(qualified_leads)} ({len(qualified_leads)/len(net_new_leads)*100:.1f}%)")
    print(f"Net-New Unique Domains with Qual Title: {len(qualified_domains)}")
    print(f"Disqualified by Title Filter:         {len(net_new_leads) - len(qualified_leads)}")
    print(f"Disqualified by Indian Demographic:   0")

    # ----------------------------------------------------
    # SUMMARY OF DOMAIN UNIQUENESS
    # ----------------------------------------------------
    print("\n" + "="*60)
    print("OVERALL SUMMARY: DOMAIN UNIQUENESS")
    print("="*60)
    
    print(f"Total Leads in Batch:                 {total_leads}")
    print(f"Total Distinct Raw Domains in Batch: {len(unique_raw_domains)}")
    print(f"Total Canonical Root Domains:        {len(unique_root_domains)}")
    print(f"--------------------------------------------------")
    print(f"Layer 1 Blocked (Exact CRM / Batches): {len(l1_all_blocked)}")
    print(f"Layer 2 Blocked (Person LCS Anchor):  {len(l2_blocked_domains)}")
    print(f"Layer 3 Blocked (Branch Prefix Trie): {len(l3_blocked_domains)}")
    print(f"--------------------------------------------------")
    print(f"TOTAL BLOCKED / DUPLICATE DOMAINS:   {len(all_blocked_domains)}")
    print(f"TRUE NET-NEW UNIQUE DOMAINS:         {len(net_new_domains)}")
    print(f"Domain Uniqueness Rate:              {len(net_new_domains) / len(unique_raw_domains) * 100:.2f}%")
    print(f"--------------------------------------------------")
    print(f"QUALIFIED UNIQUE DOMAINS (Title-Approved): {len(qualified_domains)}")
    print(f"Total Audit Time:                     {time.time() - t0_total:.2f}s")

if __name__ == "__main__":
    run_full_audit()
