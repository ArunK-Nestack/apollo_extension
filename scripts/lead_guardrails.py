"""
Lead Guardrails Engine
======================
  Layer 0: Intra-batch seniority deduplication (configurable leads per domain)
  Layers 1-4: Shared api.check_person_and_domains_in_crm_batch engine
"""

import os
import time
from typing import List, Dict, Any, Tuple

import backend.api as api


def apply_4_layer_guardrails(
    leads: List[Dict[str, Any]],
    batch_name: str,
    conn=None,
    verbose: bool = True,
    table_name: str = "apollo_saved_leads",
    max_leads_per_domain: int = 1,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not leads:
        return [], {}

    # Check environment variable override for Layer 0 domain cap if default is passed
    env_cap = (
        os.getenv("MAX_LEADS_PER_DOMAIN")
        or os.getenv("MAX_CONTACTS_PER_COMPANY")
        or os.getenv("LEAD_GUARDRAILS_MAX_PER_DOMAIN")
    )
    if env_cap and str(env_cap).strip().isdigit() and max_leads_per_domain == 1:
        max_leads_per_domain = max(1, int(str(env_cap).strip()))

    target_table = "enrich_saved_leads" if table_name == "enrich_saved_leads" else "apollo_saved_leads"
    t_start = time.time()
    total_input = len(leads)

    if conn is None:
        conn = api.get_connection()

    if not api._domain_trie.loaded:
        api.load_domain_trie(connection=conn)

    # Layer 0: Dynamic N leads per canonical domain (or company fallback)
    # Allows any positive contact limit per domain based on outreach capacity.
    domain_to_leads: dict[str, list[dict]] = {}
    for lead in leads:
        key = f"lead_{lead.get('id', lead.get('name', ''))}"
        contact = api.lead_dict_to_contact(lead, key)
        dom = api.canonical_lookup_domain(contact)
        comp = (lead.get("company") or "").strip().lower()
        dedupe_key = f"dom:{dom}" if dom else (f"comp:{comp}" if comp else f"id:{lead.get('id')}")

        raw_title = str(lead.get("job_title") or "")
        score = api.get_seniority_score(raw_title)
        if lead.get("linkedin_url"):
            score += 5
        canon_title = api.get_canonical_title_key(raw_title)
        norm_name = api.normalize_text(lead.get("name") or "")

        lead_entry = {
            "score": score,
            "canonical_title": canon_title,
            "name_norm": norm_name,
            "lead": lead,
        }

        if dedupe_key not in domain_to_leads:
            domain_to_leads[dedupe_key] = [lead_entry]
        else:
            pool = domain_to_leads[dedupe_key]

            # 1. Check if same person is already in the pool
            same_idx = -1
            for idx, p in enumerate(pool):
                if norm_name and p["name_norm"] and p["name_norm"] == norm_name:
                    same_idx = idx
                    break
            if same_idx >= 0:
                if score > pool[same_idx]["score"]:
                    pool[same_idx] = lead_entry
                continue

            # 2. Check canonical title uniqueness (avoid duplicate titles like two VPs of Sales)
            same_title_idx = -1
            if canon_title:
                for idx, p in enumerate(pool):
                    if p["canonical_title"] and p["canonical_title"] == canon_title:
                        same_title_idx = idx
                        break
            if same_title_idx >= 0:
                if score > pool[same_title_idx]["score"]:
                    pool[same_title_idx] = lead_entry
                continue

            # 3. Capacity check
            if len(pool) < max_leads_per_domain:
                pool.append(lead_entry)
                pool.sort(key=lambda x: x["score"], reverse=True)
            else:
                # Pool is full: replace lowest score if incoming has higher score
                min_idx = min(range(len(pool)), key=lambda i: pool[i]["score"])
                if score > pool[min_idx]["score"]:
                    pool[min_idx] = lead_entry
                    pool.sort(key=lambda x: x["score"], reverse=True)

    l0_leads = [item["lead"] for pool in domain_to_leads.values() for item in pool]
    l0_removed = total_input - len(l0_leads)

    # Layers 1-4: same engine as live /match-apollo and /match-enrich.
    # Preserve legacy exact-domain blocking for a one-contact company limit. When
    # selecting multiple contacts, Layer 1 instead requires an exact person or an
    # equivalent canonical-title match at that company.
    title_aware_layer1 = max_leads_per_domain > 1
    contacts = []
    key_to_lead: dict[str, dict] = {}
    for lead in l0_leads:
        key = f"lead_{lead.get('id', lead.get('name', ''))}"
        key_to_lead[key] = lead
        contacts.append(api.lead_dict_to_contact(lead, key))

    primary_domains, _ = api.resolve_company_domains(contacts, connection=conn)
    crm_hits = api.check_person_and_domains_in_crm_batch(
        contacts,
        primary_domains,
        connection=conn,
        active_batch=batch_name,
        active_table=target_table,
        title_aware_exact_domain=title_aware_layer1,
    )

    l1_hits = []
    l2_hits = []
    l3_hits = []
    l4_hits = []

    for k, hit in crm_hits.items():
        lead = key_to_lead.get(k, {})
        st = hit.get("guardrail_status", "")
        reason = hit.get("guardrail_reason", "")
        info = {
            "id": lead.get("id"),
            "name": lead.get("name"),
            "company": lead.get("company"),
            "domain": lead.get("company_domain"),
            "matched_domain": hit.get("matched_domain"),
            "matched_db_domain": hit.get("matched_db_domain"),
            "status": st,
            "reason": reason,
        }
        if st in ("contact_already_in_db", "domain_already_in_db") or reason.startswith("[L1]"):
            l1_hits.append(info)
        elif st == "person_domain_overlap" or reason.startswith("[L2]"):
            l2_hits.append(info)
        elif st == "trie_brand_stem_match" or reason.startswith("[L3]"):
            l3_hits.append(info)
        elif st == "mx_routing_match" or reason.startswith("[L4]"):
            l4_hits.append(info)
        else:
            l1_hits.append(info)

    no_domain = [
        key for key, lead in key_to_lead.items()
        if not primary_domains.get(key)
    ]
    blocked_keys = set(crm_hits.keys()) | set(no_domain)
    final_verified_leads = [key_to_lead[k] for k in key_to_lead if k not in blocked_keys]

    dur = time.time() - t_start
    metrics = {
        "total_input": total_input,
        "table_name": target_table,
        "l0_max_per_domain": max_leads_per_domain,
        "l0_unique": len(l0_leads),
        "l0_removed": l0_removed,
        "l1_title_aware": title_aware_layer1,
        "l1_blocked": len(l1_hits),
        "l2_blocked": len(l2_hits),
        "l3_blocked": len(l3_hits),
        "l4_blocked": len(l4_hits),
        "l1_hits": l1_hits,
        "l2_hits": l2_hits,
        "l3_hits": l3_hits,
        "l4_hits": l4_hits,
        "l1_l4_blocked": len(crm_hits),
        "no_domain_source": len(no_domain),
        "final_verified": len(final_verified_leads),
        "duration_sec": dur,
    }

    if verbose:
        print("\n" + "=" * 95, flush=True)
        print(f"APPLYING 4-LAYER CRM GUARDRAILS & COMPARISON: BATCH '{batch_name}' (Table: `{target_table}`)", flush=True)
        print("=" * 95, flush=True)
        print(f"  Total input leads:                 {total_input:,d}", flush=True)
        cap_str = f"Layer 0 (Cap: {max_leads_per_domain} lead{'s' if max_leads_per_domain > 1 else ''} / domain):"
        print(f"  {cap_str:<35} {len(l0_leads):,d}  (-{l0_removed:,d} dropped as lower seniority / beyond cap)", flush=True)
        l1_label = (
            "Layer 1 (Exact Person / Job Title):"
            if title_aware_layer1
            else "Layer 1 (Exact CRM & Batches):"
        )
        print(f"  {l1_label:<39}-{len(l1_hits):,d} blocked", flush=True)
        print(f"  Layer 2 (Person LCS Anchor):       -{len(l2_hits):,d} blocked", flush=True)
        print(f"  Layer 3 (Dealership Branch Trie):  -{len(l3_hits):,d} blocked", flush=True)
        print(f"  Layer 4 (DNS MX Mail Routing):     -{len(l4_hits):,d} blocked", flush=True)
        if no_domain:
            print(f"  No domain source found:            -{len(no_domain):,d} skipped", flush=True)
        print("  " + "-" * 91, flush=True)
        print(f"  Verified net-new unique leads:     {len(final_verified_leads):,d} in {dur:.2f}s", flush=True)
        print("=" * 95 + "\n", flush=True)

    return final_verified_leads, metrics
