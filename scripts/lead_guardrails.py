"""
Lead Guardrails Engine
======================
Executes the comprehensive 4-layer deduplication guardrail pipeline:
  • Layer 0: Intra-batch Seniority Deduplication (1 decision-maker lead per domain)
  • Layer 1: Exact Domain Match in Master CRM (7.47M records) & Cross-Batches
  • Layer 2: Person-Name Anchor + LCS Fuzzy Domain Matching
  • Layer 3: MARISA Prefix Trie (2.56M Unique Root Slugs)
  • Layer 4: Multi-Threaded DNS MX Mail Routing Resolver (Alias & Holding Company Detection)
"""

import time
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor

import backend.api as api

def apply_4_layer_guardrails(
    leads: List[Dict[str, Any]], 
    batch_name: str, 
    conn=None,
    verbose: bool = True
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Applies all 4 deduplication defense layers to a list of leads from apollo_saved_leads.
    Returns:
      (verified_leads, audit_metrics_dict)
    """
    if not leads:
        return [], {}

    t_start = time.time()
    total_input = len(leads)
    
    if conn is None:
        conn = api.get_connection()

    # -------------------------------------------------------------------------
    # LAYER 0: Intra-Batch Seniority Deduplication (1 Lead / Unique Domain)
    # -------------------------------------------------------------------------
    domain_to_lead = {}
    for l in leads:
        dom = (l.get("company_domain") or "").strip().lower()
        comp = (l.get("company") or "").strip().lower()
        key = f"dom:{dom}" if dom else (f"comp:{comp}" if comp else f"id:{l.get('id')}")
        
        score = api.get_seniority_score(str(l.get("job_title") or ""))
        if l.get("linkedin_url"):
            score += 5

        if key not in domain_to_lead or score > domain_to_lead[key][0]:
            domain_to_lead[key] = (score, l)

    l0_leads = [v[1] for v in domain_to_lead.values()]
    l0_domains = list(set((l.get("company_domain") or "").strip().lower() for l in l0_leads if l.get("company_domain")))
    l0_removed = total_input - len(l0_leads)

    # -------------------------------------------------------------------------
    # LAYER 1: Exact Domain Match (Master CRM emails table & Cross-Batches)
    # -------------------------------------------------------------------------
    l1_crm_hits = set()
    l1_batch_hits = set()

    with conn.cursor() as cur:
        chunk_size = 500
        for i in range(0, len(l0_domains), chunk_size):
            chunk = l0_domains[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            
            # CRM emails table (7.47M rows)
            cur.execute(f"SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt})", tuple(chunk))
            for (d,) in cur.fetchall():
                l1_crm_hits.add(str(d or "").strip().lower())

            # Cross-batch check
            cur.execute(f"SELECT DISTINCT company_domain FROM apollo_saved_leads WHERE company_domain IN ({fmt}) AND batch != %s", tuple(chunk + [batch_name]))
            for (d,) in cur.fetchall():
                l1_batch_hits.add(str(d or "").strip().lower())

    l1_blocked = l1_crm_hits | l1_batch_hits
    l1_leads = [l for l in l0_leads if (l.get("company_domain") or "").strip().lower() not in l1_blocked]
    l1_domains = list(set((l.get("company_domain") or "").strip().lower() for l in l1_leads if l.get("company_domain")))

    # -------------------------------------------------------------------------
    # LAYER 2: Person-Name Anchor + LCS Domain Overlap
    # -------------------------------------------------------------------------
    names_to_check = list(set(l.get("name") for l in l1_leads if l.get("name")))
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

    l2_blocked = set()
    for l in l1_leads:
        name = l.get("name")
        dom = (l.get("company_domain") or "").strip().lower()
        if not name or not dom:
            continue
        db_doms = db_name_to_domains.get(name, set())
        if db_doms:
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
                        l2_blocked.add(dom)
                        break

    l2_leads = [l for l in l1_leads if (l.get("company_domain") or "").strip().lower() not in l2_blocked]
    l2_domains = list(set((l.get("company_domain") or "").strip().lower() for l in l2_leads if l.get("company_domain")))

    # -------------------------------------------------------------------------
    # LAYER 3: Database-Driven Prefix Trie (MARISA Trie - 2.56M stems)
    # -------------------------------------------------------------------------
    if not api._domain_trie.loaded:
        api.load_domain_trie()

    l3_blocked = set()
    for dom in l2_domains:
        slug = api._clean_slug(dom)
        trie_match, prefix = api._domain_trie.find_prefix_match(slug, min_prefix_len=4)
        if trie_match and trie_match != dom and api._is_valid_branch_match(trie_match, prefix, slug):
            l3_blocked.add(dom)

    l3_leads = [l for l in l2_leads if (l.get("company_domain") or "").strip().lower() not in l3_blocked]
    l3_domains = list(set((l.get("company_domain") or "").strip().lower() for l in l3_leads if l.get("company_domain")))

    # -------------------------------------------------------------------------
    # LAYER 4: Multi-Threaded DNS MX Mail Routing Resolver
    # -------------------------------------------------------------------------
    l4_blocked = set()
    mx_domain_map = {}

    if api._DNS_AVAILABLE and l3_domains:
        def resolve_single(d):
            try:
                mx_r = api._resolve_mx_root_domain(d)
                return (d, mx_r) if mx_r else None
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=50) as executor:
            results = list(executor.map(resolve_single, l3_domains))
            for res in results:
                if res:
                    mx_domain_map[res[0]] = res[1]

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
                l4_blocked.add(dom)

    final_verified_leads = [l for l in l3_leads if (l.get("company_domain") or "").strip().lower() not in l4_blocked]
    dur = time.time() - t_start

    metrics = {
        "total_input": total_input,
        "l0_unique": len(l0_leads),
        "l0_removed": l0_removed,
        "l1_crm_hits": len(l1_crm_hits),
        "l1_batch_hits": len(l1_batch_hits),
        "l2_hits": len(l2_blocked),
        "l3_hits": len(l3_blocked),
        "l4_hits": len(l4_blocked),
        "final_verified": len(final_verified_leads),
        "duration_sec": dur
    }

    if verbose:
        print("\n" + "=" * 95, flush=True)
        print(f"APPLYING 4-LAYER DEDUPLICATION GUARDRAILS: BATCH '{batch_name}'", flush=True)
        print("=" * 95, flush=True)
        print(f"  • Total Input Leads:                      {total_input:,d}", flush=True)
        print(f"  • Layer 0 (Intra-batch 1-Lead/Domain):    {len(l0_leads):,d}  (-{l0_removed:,d} redundant leads)", flush=True)
        print(f"  • Layer 1 (Exact CRM & Cross-Batches):    {len(l1_leads):,d}  (-{len(l1_blocked):,d} collisions)", flush=True)
        print(f"  • Layer 2 (Person Anchor & LCS):          {len(l2_leads):,d}  (-{len(l2_blocked):,d} collisions)", flush=True)
        print(f"  • Layer 3 (MARISA Prefix Trie):           {len(l3_leads):,d}  (-{len(l3_blocked):,d} collisions)", flush=True)
        print(f"  • Layer 4 (DNS MX Mail Routing):          {len(final_verified_leads):,d}  (-{len(l4_blocked):,d} alias domains)", flush=True)
        print("  " + "-" * 91, flush=True)
        print(f"✓ 100% VERIFIED NET-NEW UNIQUE LEADS:       {len(final_verified_leads):,d} in {dur:.2f}s", flush=True)
        print("=" * 95 + "\n", flush=True)

    return final_verified_leads, metrics
