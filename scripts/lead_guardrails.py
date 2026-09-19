"""
Lead Guardrails Engine
======================
  Layer 0: Intra-batch seniority deduplication (1 lead per domain)
  Layers 1-4: Shared api.check_person_and_domains_in_crm_batch engine
"""

import time
from typing import List, Dict, Any, Tuple

import backend.api as api


def apply_4_layer_guardrails(
    leads: List[Dict[str, Any]],
    batch_name: str,
    conn=None,
    verbose: bool = True,
    table_name: str = "apollo_saved_leads",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not leads:
        return [], {}

    target_table = "enrich_saved_leads" if table_name == "enrich_saved_leads" else "apollo_saved_leads"
    t_start = time.time()
    total_input = len(leads)

    if conn is None:
        conn = api.get_connection()

    # Layer 0: one lead per canonical domain (or company fallback)
    domain_to_lead = {}
    for lead in leads:
        key = f"lead_{lead.get('id', lead.get('name', ''))}"
        contact = api.lead_dict_to_contact(lead, key)
        dom = api.canonical_lookup_domain(contact)
        comp = (lead.get("company") or "").strip().lower()
        dedupe_key = f"dom:{dom}" if dom else (f"comp:{comp}" if comp else f"id:{lead.get('id')}")

        score = api.get_seniority_score(str(lead.get("job_title") or ""))
        if lead.get("linkedin_url"):
            score += 5

        if dedupe_key not in domain_to_lead or score > domain_to_lead[dedupe_key][0]:
            domain_to_lead[dedupe_key] = (score, lead)

    l0_leads = [v[1] for v in domain_to_lead.values()]
    l0_removed = total_input - len(l0_leads)

    # Layers 1-4: same engine as live /match-apollo and /match-enrich
    contacts = []
    key_to_lead: dict[str, dict] = {}
    for lead in l0_leads:
        key = f"lead_{lead.get('id', lead.get('name', ''))}"
        key_to_lead[key] = lead
        contacts.append(api.lead_dict_to_contact(lead, key))

    primary_domains, _ = api.resolve_company_domains(contacts, connection=conn)
    crm_hits = api.check_person_and_domains_in_crm_batch(
        contacts, primary_domains, connection=conn, active_batch=batch_name, active_table=target_table
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
        "l0_unique": len(l0_leads),
        "l0_removed": l0_removed,
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
        print(f"  Layer 0 (1 lead / domain):         {len(l0_leads):,d}  (-{l0_removed:,d} dropped as lower seniority)", flush=True)
        print(f"  Layer 1 (Exact CRM & Batches):     -{len(l1_hits):,d} blocked", flush=True)
        print(f"  Layer 2 (Person LCS Anchor):       -{len(l2_hits):,d} blocked", flush=True)
        print(f"  Layer 3 (Dealership Branch Trie):  -{len(l3_hits):,d} blocked", flush=True)
        print(f"  Layer 4 (DNS MX Mail Routing):     -{len(l4_hits):,d} blocked", flush=True)
        if no_domain:
            print(f"  No domain source found:            -{len(no_domain):,d} skipped", flush=True)
        print("  " + "-" * 91, flush=True)
        print(f"  Verified net-new unique leads:     {len(final_verified_leads):,d} in {dur:.2f}s", flush=True)
        print("=" * 95 + "\n", flush=True)

    return final_verified_leads, metrics
