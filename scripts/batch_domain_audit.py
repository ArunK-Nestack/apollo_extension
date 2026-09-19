#!/usr/bin/env python3
"""
Batch domain audit: unique-domain guardrails + domain-column vs website check.
Used by manage_batches.py and CLI: python scripts/batch_domain_audit.py <batch_name>
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import (
    canonical_lookup_domain,
    extract_root_domain,
    get_seniority_score,
    lead_dict_to_contact,
    _domain_lookup_candidates,
    generate_candidate_domains,
    ensure_batch_enrichment_ledger_table,
)
from scripts.lead_guardrails import apply_4_layer_guardrails
from scripts.domain_resolver_engine import (
    clean_domain,
    _generate_smart_permutations,
    _has_active_dns,
    _verify_homepage_brand,
    _check_clearbit_autocomplete,
)
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


def resolve_batch_name(conn, user_input: str, batches: list[dict], table_name: str = "apollo_saved_leads") -> str | None:
    """Resolve batch by list index, exact name, or single fuzzy DB match."""
    raw = (user_input or "").strip()
    if not raw:
        return None

    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(batches):
            return batches[idx]["batch"]
        return None

    for b in batches:
        if b["batch"].lower() == raw.lower():
            return b["batch"]

    target_table = "enrich_saved_leads" if table_name == "enrich_saved_leads" else "apollo_saved_leads"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT batch FROM `{target_table}`
            WHERE batch = %s OR batch LIKE %s
            GROUP BY batch
            ORDER BY COUNT(*) DESC
            """,
            (raw, f"%{raw}%"),
        )
        hits = [str(r[0]) for r in cur.fetchall() if r and r[0]]

    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    if raw in hits:
        return raw
    lower = raw.lower()
    exact_ci = [h for h in hits if h.lower() == lower]
    if len(exact_ci) == 1:
        return exact_ci[0]

    print(f"\n[AMBIGUOUS] Multiple batches match '{raw}':")
    for h in hits[:10]:
        print(f"  - {h}")
    if len(hits) > 10:
        print(f"  ... +{len(hits) - 10} more")
    print("Use exact batch name or list number from overview.")
    return None


def fetch_batch_leads(
    conn,
    batch_name: str,
    table_name: str = "apollo_saved_leads",
    ignore_enriched: bool = True
) -> list[dict[str, Any]]:
    target_table = "enrich_saved_leads" if table_name == "enrich_saved_leads" else "apollo_saved_leads"
    ensure_batch_enrichment_ledger_table(conn)
    with conn.cursor() as cur:
        if ignore_enriched:
            if target_table == "enrich_saved_leads":
                cur.execute(
                    """
                    SELECT l.id, '' as apollo_id, l.name, l.first_name, l.last_name, l.job_title, l.company,
                           l.company_domain, l.website_link, '' as email, l.segment
                    FROM `enrich_saved_leads` l
                    LEFT JOIN `batch_enrichment_ledger` e
                      ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                    WHERE l.batch = %s
                      AND e.id IS NULL
                      AND (l.email IS NULL OR l.email = '')
                    ORDER BY l.company_domain, l.name
                    """,
                    (batch_name,),
                )
            else:
                cur.execute(
                    """
                    SELECT l.id, l.apollo_id, l.name, l.first_name, l.last_name, l.job_title, l.company,
                           l.company_domain, l.website_link, l.email, l.segment
                    FROM `apollo_saved_leads` l
                    LEFT JOIN `batch_enrichment_ledger` e
                      ON e.batch COLLATE utf8mb4_unicode_ci = l.batch COLLATE utf8mb4_unicode_ci AND e.saved_lead_id = l.id
                    WHERE l.batch = %s
                      AND e.id IS NULL
                      AND (l.email IS NULL OR l.email = '')
                    ORDER BY l.company_domain, l.name
                    """,
                    (batch_name,),
                )
        else:
            if target_table == "enrich_saved_leads":
                cur.execute(
                    """
                    SELECT id, '' as apollo_id, name, first_name, last_name, job_title, company,
                           company_domain, website_link, '' as email, segment
                    FROM `enrich_saved_leads`
                    WHERE batch = %s
                    ORDER BY company_domain, name
                    """,
                    (batch_name,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, apollo_id, name, first_name, last_name, job_title, company,
                           company_domain, website_link, email, segment
                    FROM `apollo_saved_leads`
                    WHERE batch = %s
                    ORDER BY company_domain, name
                    """,
                    (batch_name,),
                )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def analyze_batch_leads(leads: list[dict[str, Any]]) -> dict[str, Any]:
    by_stored: dict[str, list[dict]] = defaultdict(list)
    by_canonical: dict[str, list[dict]] = defaultdict(list)
    column_matches_website = 0
    column_empty_website_used: list[dict] = []
    column_vs_website_mismatch: list[dict] = []
    no_domain_source = 0

    for lead in leads:
        dom = (lead.get("company_domain") or "").strip().lower()
        web_root = extract_root_domain(lead.get("website_link") or "")
        col_root = extract_root_domain(dom)

        contact = lead_dict_to_contact(lead, key=str(lead.get("id", "")))
        canonical = canonical_lookup_domain(contact)
        cands = _domain_lookup_candidates(contact)

        lead["_canonical_domain"] = canonical
        lead["_candidates"] = cands

        if dom:
            by_stored[dom].append(lead)
        if canonical:
            by_canonical[canonical].append(lead)

        if not col_root and not web_root:
            no_domain_source += 1
        elif col_root and web_root and col_root == web_root:
            column_matches_website += 1
        elif col_root and web_root and col_root != web_root:
            column_vs_website_mismatch.append(
                {
                    "name": lead.get("name"),
                    "stored": dom,
                    "column_root": col_root,
                    "website_root": web_root,
                    "website_link": lead.get("website_link"),
                    "candidates": cands,
                }
            )
        elif not col_root and web_root:
            column_empty_website_used.append(
                {
                    "name": lead.get("name"),
                    "website_root": web_root,
                    "website_link": lead.get("website_link"),
                }
            )

    duplicate_groups = []
    for domain, group in sorted(by_canonical.items()):
        if len(group) < 2:
            continue
        ranked = sorted(
            group,
            key=lambda L: (
                get_seniority_score(str(L.get("job_title") or "")),
                str(L.get("name") or ""),
            ),
            reverse=True,
        )
        duplicate_groups.append(
            {
                "domain": domain,
                "contacts": [
                    {
                        "id": L.get("id"),
                        "name": L.get("name"),
                        "job_title": L.get("job_title"),
                        "seniority": get_seniority_score(str(L.get("job_title") or "")),
                        "company_domain": L.get("company_domain"),
                        "website_link": L.get("website_link"),
                    }
                    for L in ranked
                ],
                "winner": ranked[0].get("name"),
            }
        )

    stored_dupes = {d: [L.get("name") for L in gs] for d, gs in by_stored.items() if d and len(gs) > 1}

    return {
        "total_leads": len(leads),
        "distinct_stored_domains": len([d for d in by_stored if d]),
        "distinct_canonical_domains": len(by_canonical),
        "duplicate_domain_groups": duplicate_groups,
        "stored_domain_dupes": stored_dupes,
        "column_matches_website": column_matches_website,
        "column_empty_website_count": len(column_empty_website_used),
        "column_empty_website": column_empty_website_used,
        "column_mismatch_count": len(column_vs_website_mismatch),
        "column_mismatches": column_vs_website_mismatch,
        "no_domain_source": no_domain_source,
        "unique_domains_ok": len(duplicate_groups) == 0,
        "domain_column_ok": (
            no_domain_source == 0
            and len(column_empty_website_used) == 0
        ),
    }


def audit_multi_candidate_and_verification(conn, leads: list[dict[str, Any]], batch_name: str, table_name: str = "apollo_saved_leads") -> dict[str, Any]:
    """
    Multi-candidate CRM collision check + Domain verification arbiter:
    1. Evaluates all corporate domain candidate permutations against 7.47M CRM emails and past batches in both apollo_saved_leads and enrich_saved_leads.
    2. Verifies whether domains pass active DNS and homepage brand title validation.
    3. Identifies exact CRM collisions and unverified phantom guesses.
    """
    if not leads:
        return {"crm_collisions": [], "unverified_phantoms": [], "verified_net_new": []}

    lead_candidates: dict[int, list[str]] = {}
    all_candidate_roots: set[str] = set()

    for lead in leads:
        lid = lead.get("id")
        comp = lead.get("company") or ""
        stored_dom = lead.get("company_domain") or ""
        cands = set(generate_candidate_domains(comp))
        for p in _generate_smart_permutations(comp)[:4]:
            cands.add(clean_domain(p))
        if stored_dom:
            cands.add(clean_domain(stored_dom))
        roots = [extract_root_domain(c) for c in cands if c]
        roots = [r for r in roots if r]
        lead_candidates[lid] = roots
        for r in roots:
            all_candidate_roots.add(r)

    # Batch query CRM emails, apollo_saved_leads, and enrich_saved_leads
    crm_matched_domains = set()
    if all_candidate_roots:
        cand_list = list(all_candidate_roots)
        chunk_size = 500
        with conn.cursor() as cur:
            for i in range(0, len(cand_list), chunk_size):
                chunk = cand_list[i:i + chunk_size]
                fmt = ",".join(["%s"] * len(chunk))
                # 1. CRM emails
                cur.execute(f"SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt})", chunk)
                for r in cur.fetchall():
                    if r and r[0]:
                        crm_matched_domains.add(str(r[0]).strip().lower())
                # 2. apollo_saved_leads
                if table_name == "apollo_saved_leads":
                    cur.execute(
                        f"SELECT DISTINCT company_domain FROM apollo_saved_leads WHERE company_domain IN ({fmt}) AND batch != %s",
                        chunk + [batch_name],
                    )
                else:
                    cur.execute(
                        f"SELECT DISTINCT company_domain FROM apollo_saved_leads WHERE company_domain IN ({fmt})",
                        chunk,
                    )
                for r in cur.fetchall():
                    if r and r[0]:
                        crm_matched_domains.add(str(r[0]).strip().lower())
                # 3. enrich_saved_leads
                try:
                    if table_name == "enrich_saved_leads":
                        cur.execute(
                            f"SELECT DISTINCT company_domain FROM enrich_saved_leads WHERE company_domain IN ({fmt}) AND batch != %s",
                            chunk + [batch_name],
                        )
                    else:
                        cur.execute(
                            f"SELECT DISTINCT company_domain FROM enrich_saved_leads WHERE company_domain IN ({fmt})",
                            chunk,
                        )
                    for r in cur.fetchall():
                        if r and r[0]:
                            crm_matched_domains.add(str(r[0]).strip().lower())
                except Exception:
                    pass

    # In-batch company domain cache to prevent redundant HTTP probes
    comp_cache: dict[str, tuple[bool, str]] = {}
    comp_lock = threading.Lock()

    # Verify domains concurrently
    def verify_single(lead):
        lid = lead.get("id")
        comp = lead.get("company") or ""
        dom = lead.get("company_domain") or ""

        # Check CRM collision
        cands = lead_candidates.get(lid, [])
        hit_dom = next((c for c in cands if c in crm_matched_domains), None)
        if hit_dom:
            return {
                "id": lid,
                "name": lead.get("name"),
                "company": comp,
                "domain": dom,
                "status": "CRM_COLLISION",
                "matched_domain": hit_dom,
                "reason": f"Matches CRM/past batches via candidate domain '{hit_dom}'"
            }

        # Check in-memory company cache first
        ckey = comp.strip().lower()
        with comp_lock:
            cached = comp_cache.get(ckey)

        if cached is not None:
            is_v, v_dom = cached
        else:
            is_v = False
            v_dom = ""
            # 1. Prioritize stored domain first (already present on lead)
            if dom and _has_active_dns(dom) and _verify_homepage_brand(dom, comp):
                is_v = True
                v_dom = dom
            # 2. Fallback to Clearbit autocomplete if stored domain missing or invalid
            elif comp:
                cb = _check_clearbit_autocomplete(comp)
                if cb and _has_active_dns(cb) and _verify_homepage_brand(cb, comp):
                    is_v = True
                    v_dom = cb
            with comp_lock:
                comp_cache[ckey] = (is_v, v_dom)

        if not is_v:
            return {
                "id": lid,
                "name": lead.get("name"),
                "company": comp,
                "domain": dom,
                "status": "UNVERIFIED_PHANTOM",
                "reason": "Unverified or parked domain (failed active DNS/brand title verification)"
            }

        return {
            "id": lid,
            "name": lead.get("name"),
            "company": comp,
            "domain": dom,
            "status": "VERIFIED_NET_NEW",
            "verified_domain": v_dom
        }

    total_leads = len(leads)
    print(f"\n[*] Probing {total_leads:,d} leads with 40 concurrent workers...")
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=40) as executor:
        future_to_lead = [executor.submit(verify_single, l) for l in leads]
        for idx, f in enumerate(as_completed(future_to_lead), 1):
            results.append(f.result())
            if idx % 100 == 0 or idx == total_leads:
                elapsed = max(0.5, time.time() - t0)
                rate = idx / elapsed
                pct = (idx / total_leads) * 100
                sys.stdout.write(f"\r  [Audit Progress] Verified {idx:,d}/{total_leads:,d} leads ({pct:.1f}%) | Speed: {rate:.0f} leads/s...")
                sys.stdout.flush()
    print()

    crm_collisions = [r for r in results if r["status"] == "CRM_COLLISION"]
    unverified_phantoms = [r for r in results if r["status"] == "UNVERIFIED_PHANTOM"]
    verified_net_new = [r for r in results if r["status"] == "VERIFIED_NET_NEW"]

    return {
        "crm_collisions": crm_collisions,
        "unverified_phantoms": unverified_phantoms,
        "verified_net_new": verified_net_new,
    }


def audit_batch(
    conn,
    batch_name: str,
    *,
    run_guardrails: bool = True,
    table_name: str = "apollo_saved_leads",
    ignore_enriched: bool = True,
) -> dict[str, Any]:
    target_table = "enrich_saved_leads" if table_name == "enrich_saved_leads" else "apollo_saved_leads"
    leads = fetch_batch_leads(conn, batch_name, table_name=target_table, ignore_enriched=ignore_enriched)
    analysis = analyze_batch_leads(leads)
    analysis["batch"] = batch_name
    analysis["table_name"] = target_table
    analysis["ignore_enriched"] = ignore_enriched

    ignored_count = 0
    total_in_db = len(leads)
    if ignore_enriched:
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM `{target_table}` WHERE batch = %s", (batch_name,))
                row = cur.fetchone()
                if row:
                    total_in_db = row[0]
                    ignored_count = max(0, total_in_db - len(leads))
        except Exception:
            pass
    analysis["total_in_db"] = total_in_db
    analysis["ignored_enriched_count"] = ignored_count

    if not leads:
        analysis["guardrail_metrics"] = {}
        analysis["guardrail_eligible"] = 0
        return analysis

    if run_guardrails:
        print(f"  Checking 4-layer CRM guardrails on {len(leads):,d} unenriched leads against CRM, Apollo & Enrich tables...", flush=True)
        eligible, metrics = apply_4_layer_guardrails(leads, batch_name, conn, verbose=False, table_name=target_table)
        analysis["guardrail_metrics"] = metrics
        analysis["guardrail_eligible"] = len(eligible)
        analysis["safe_enrich_count"] = int(metrics.get("final_verified", len(eligible)))
        analysis["guardrail_unique_ok"] = True
    else:
        analysis["guardrail_metrics"] = {}
        analysis["guardrail_eligible"] = analysis["distinct_canonical_domains"]
        analysis["safe_enrich_count"] = analysis["distinct_canonical_domains"]
        analysis["guardrail_unique_ok"] = analysis["unique_domains_ok"]

    return analysis


def print_batch_audit_report(result: dict[str, Any]) -> None:
    batch = result.get("batch", "")
    total = result.get("total_leads", 0)
    target_table = result.get("table_name", "apollo_saved_leads")

    print("\n" + "=" * 92)
    print(f" BATCH DOMAIN AUDIT: {batch} (Table: `{target_table}`)")
    print("=" * 92)

    if total == 0:
        ignored = result.get("ignored_enriched_count", 0)
        if ignored > 0:
            print(f" [NOTICE] All {ignored:,d} leads in this batch are already enriched. 0 unenriched leads to audit.")
        else:
            print(" [NOTICE] No leads in this batch.")
        print("=" * 92 + "\n")
        return

    unique_ok = result.get("unique_domains_ok", False)
    col_ok = result.get("domain_column_ok", False)
    safe_n = result.get("safe_enrich_count", 0)

    ignored = result.get("ignored_enriched_count", 0)
    if ignored > 0:
        print(f" Total leads in batch:          {result.get('total_in_db', total):,d}")
        print(f" Already-Enriched Leads (Safe): {ignored:,d} (excluded from audit)")
        print(f" Unenriched Leads Audited:      {total:,d}")
    else:
        print(f" Total leads in batch:          {total:,d}")
    print(f" Distinct company_domain:       {result.get('distinct_stored_domains', 0):,d}")
    print(f" Distinct canonical domains:    {result.get('distinct_canonical_domains', 0):,d}")
    print()
    print(" DOMAIN COLUMN vs WEBSITE")
    print(f"   Column matches website:      {result.get('column_matches_website', 0):,d} / {total:,d}")
    print(f"   Column empty (web fallback): {result.get('column_empty_website_count', 0):,d}")
    print(f"   Column != website:           {result.get('column_mismatch_count', 0):,d}")
    print(f"   No domain source:            {result.get('no_domain_source', 0):,d}")
    print(f"   Verdict:                     {'PASS' if col_ok else 'REVIEW'}")
    print()
    print(" UNIQUE DOMAIN (1 per company)")
    dup_n = len(result.get("duplicate_domain_groups") or [])
    print(f"   Duplicate domains in batch:  {dup_n:,d}")
    print(f"   Verdict:                     {'PASS' if unique_ok else 'FAIL'}")

    gm = result.get("guardrail_metrics") or {}
    if gm:
        print()
        print("=" * 92)
        print(f" 4-LAYER CRM GUARDRAIL COMPARISON REPORT (Table: `{target_table}`)")
        print("=" * 92)
        print(f"   Total Leads Evaluated:         {total:,d}")
        print(f"   Layer 0 Intra-Batch Unique:    {gm.get('l0_unique', 0):,d}  (-{gm.get('l0_removed', 0):,d} duplicate contacts dropped)")
        print(f"   Layer 1 Exact CRM & Batches:   -{gm.get('l1_blocked', 0):,d} blocked (exact match in emails / saved batches)")
        print(f"   Layer 2 Person LCS Anchor:     -{gm.get('l2_blocked', 0):,d} blocked (person exists in DB at parent company)")
        print(f"   Layer 3 Branch Prefix Trie:    -{gm.get('l3_blocked', 0):,d} blocked (branch stem matches known DB domain)")
        print(f"   Layer 4 DNS MX Mail Routing:   -{gm.get('l4_blocked', 0):,d} blocked (shared corporate mail routing)")
        if gm.get("no_domain_source"):
            print(f"   No Domain Source Found:        -{gm.get('no_domain_source', 0):,d} skipped")
        print("   " + "-" * 88)
        print(f"   TOTAL CRM/CROSS-BATCH BLOCKED: {gm.get('l1_l4_blocked', 0):,d} leads")
        print(f"   TRUE NET-NEW ENRICH-READY:     {gm.get('final_verified', 0):,d} leads")
        if total > 0:
            uniqueness_pct = (gm.get('final_verified', 0) / total) * 100
            print(f"   Domain Uniqueness Rate:        {uniqueness_pct:.1f}%")
        print(f"   Guardrails Verdict:            {'PASS (100% Unique Net-New)' if gm.get('l1_l4_blocked', 0) == 0 else 'BLOCKED DUPLICATES IDENTIFIED'}")

        if gm.get("l1_hits"):
            print("\n   [Layer 1 Samples — Exact CRM / Batch Overlaps]:")
            for h in gm["l1_hits"][:8]:
                print(f"     • id={h.get('id')} | {h.get('name')} ({h.get('company')}) -> {h.get('domain')} ({h.get('reason')})")
            if len(gm["l1_hits"]) > 8:
                print(f"       ... +{len(gm['l1_hits']) - 8} more")

        if gm.get("l2_hits"):
            print("\n   [Layer 2 Samples — Person-Name LCS Anchor Matches]:")
            for h in gm["l2_hits"][:8]:
                print(f"     • id={h.get('id')} | {h.get('name')} ({h.get('company')}) -> {h.get('domain')} ({h.get('reason')})")
            if len(gm["l2_hits"]) > 8:
                print(f"       ... +{len(gm['l2_hits']) - 8} more")

        if gm.get("l3_hits"):
            print("\n   [Layer 3 Samples — Dealership Branch Prefix Matches]:")
            for h in gm["l3_hits"][:8]:
                print(f"     • id={h.get('id')} | {h.get('name')} ({h.get('company')}) -> {h.get('domain')} ({h.get('reason')})")
            if len(gm["l3_hits"]) > 8:
                print(f"       ... +{len(gm['l3_hits']) - 8} more")

        if gm.get("l4_hits"):
            print("\n   [Layer 4 Samples — DNS MX Mail Routing Matches]:")
            for h in gm["l4_hits"][:8]:
                print(f"     • id={h.get('id')} | {h.get('name')} ({h.get('company')}) -> {h.get('domain')} ({h.get('reason')})")
            if len(gm["l4_hits"]) > 8:
                print(f"       ... +{len(gm['l4_hits']) - 8} more")

    print()
    print(f" SAFE TO ENRICH (unique after L0): {safe_n:,d} leads")
    print("=" * 92)

    if result.get("duplicate_domain_groups"):
        print("\n DUPLICATE DOMAINS (seniority winner = enrich keeps)")
        for grp in result["duplicate_domain_groups"]:
            print(f"\n  {grp['domain']}:")
            for c in grp["contacts"]:
                tag = "KEEP" if c["name"] == grp["winner"] else "DROP at enrich"
                print(
                    f"    [{tag}] {c['name']} | {c.get('job_title') or ''} "
                    f"| seniority={c['seniority']}"
                )

    if result.get("column_mismatches"):
        print("\n COLUMN vs WEBSITE MISMATCH (dual lookup — column checked first)")
        for row in result["column_mismatches"][:20]:
            print(
                f"  {row['name']}: column={row['column_root']} website={row['website_root']} "
                f"candidates={row['candidates']}"
            )
        if result["column_mismatch_count"] > 20:
            print(f"  ... +{result['column_mismatch_count'] - 20} more")

    if result.get("column_empty_website"):
        print("\n COLUMN EMPTY — website-only domain")
        for row in result["column_empty_website"][:20]:
            print(f"  {row['name']}: {row['website_root']}")

    if not unique_ok and not result.get("duplicate_domain_groups"):
        print("\n STORED DOMAIN DUPLICATES (raw company_domain)")
        for dom, names in sorted(result.get("stored_domain_dupes", {}).items()):
            print(f"  {dom}: {', '.join(names)}")

    print()


def get_duplicate_drops(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Leads Layer 0 would drop: lower seniority per duplicate canonical domain."""
    drops: list[dict[str, Any]] = []
    for grp in result.get("duplicate_domain_groups") or []:
        winner = grp.get("winner")
        domain = grp.get("domain") or ""
        for c in grp.get("contacts") or []:
            if c.get("name") == winner:
                continue
            lead_id = c.get("id")
            if lead_id is None:
                continue
            drops.append(
                {
                    "id": lead_id,
                    "name": c.get("name"),
                    "job_title": c.get("job_title"),
                    "domain": domain,
                    "seniority": c.get("seniority"),
                }
            )
    return drops


def delete_duplicate_drops(conn, batch_name: str, drops: list[dict[str, Any]], table_name: str = "apollo_saved_leads") -> int:
    if not drops:
        return 0
    target_table = "enrich_saved_leads" if table_name == "enrich_saved_leads" else "apollo_saved_leads"
    ids = [int(d["id"]) for d in drops]
    with conn.cursor() as cur:
        placeholders = ", ".join(["%s"] * len(ids))
        cur.execute(
            f"DELETE FROM `{target_table}` WHERE `batch` = %s AND `id` IN ({placeholders})",
            [batch_name] + ids,
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def prompt_delete_duplicate_drops(conn, report: dict[str, Any], table_name: str = "apollo_saved_leads") -> int:
    drops = get_duplicate_drops(report)
    if not drops:
        return 0

    batch = report.get("batch", "")
    target_table = report.get("table_name", table_name)
    print("\n--- DELETE DUPLICATE LEADS (DROP at enrich) ---")
    for d in drops:
        print(
            f"  - id={d['id']} | {d.get('name')} | {d.get('job_title') or ''} "
            f"| {d.get('domain')} | seniority={d.get('seniority')}"
        )

    ans = input(f"\nDelete {len(drops)} lower-seniority duplicate(s) from '{batch}' in `{target_table}`? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        print("Skipped deletion.")
        return 0

    deleted = delete_duplicate_drops(conn, batch, drops, table_name=target_table)
    print(f"Deleted {deleted} row(s).")
    return deleted


def prompt_enhanced_audit_cleaning(conn, report: dict[str, Any], table_name: str = "apollo_saved_leads") -> int:
    """Prompt user with granular options to clean duplicates, CRM collisions, and phantom domains."""
    batch = report.get("batch", "")
    target_table = report.get("table_name", table_name)
    dup_drops = get_duplicate_drops(report)
    sr = report.get("shield_results", {})
    crm_drops = sr.get("crm_collisions", [])
    phantom_drops = sr.get("unverified_phantoms", [])

    total_issues = len(dup_drops) + len(crm_drops) + len(phantom_drops)
    if total_issues == 0:
        print(f"\n[✓] Batch '{batch}' in `{target_table}` is 100% clean, verified, and unique! No invalid leads found.")
        return 0

    print("\n" + "=" * 92)
    print(f" CLEANING & PURITY ACTIONS FOR BATCH: '{batch}' (Table: `{target_table}`)")
    print("=" * 92)
    print(f"  [1] Delete Intra-Batch Duplicate Domain Drops ({len(dup_drops):,d} leads - lower seniority)")
    print(f"  [2] Delete CRM Collision Leads ({len(crm_drops):,d} leads - already in CRM/past batches)")
    print(f"  [3] Delete Unverified Phantom Domain Leads ({len(phantom_drops):,d} leads - unverified/parked)")
    print(f"  [4] Clean ALL Invalid Leads ({total_issues:,d} leads combined - leave ONLY verified net-new)")
    print(f"  [5] Keep all leads (skip deletion)")

    choice = input("\nSelect cleaning action [1-5, default 5]: ").strip()
    if choice not in ("1", "2", "3", "4"):
        print("Skipped deletion. All leads kept.")
        return 0

    to_delete_ids = []
    desc = ""
    if choice == "1":
        to_delete_ids = [d["id"] for d in dup_drops if d.get("id")]
        desc = f"{len(to_delete_ids)} intra-batch duplicate leads"
    elif choice == "2":
        to_delete_ids = [d["id"] for d in crm_drops if d.get("id")]
        desc = f"{len(to_delete_ids)} CRM collision leads"
    elif choice == "3":
        to_delete_ids = [d["id"] for d in phantom_drops if d.get("id")]
        desc = f"{len(to_delete_ids)} unverified phantom domain leads"
    elif choice == "4":
        all_ids = set()
        for d in dup_drops:
            if d.get("id"):
                all_ids.add(d["id"])
        for d in crm_drops:
            if d.get("id"):
                all_ids.add(d["id"])
        for d in phantom_drops:
            if d.get("id"):
                all_ids.add(d["id"])
        to_delete_ids = list(all_ids)
        desc = f"{len(to_delete_ids)} invalid leads (duplicates + CRM collisions + phantoms)"

    if not to_delete_ids:
        print("No leads to delete for this option.")
        return 0

    confirm = input(f"\nAre you sure you want to permanently delete {desc} from '{batch}' in `{target_table}`? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Deletion canceled.")
        return 0

    deleted = delete_duplicate_drops(conn, batch, [{"id": i} for i in to_delete_ids], table_name=target_table)
    print(f"\n[✓] Successfully deleted {deleted:,d} leads from '{batch}' in `{target_table}`.")
    return deleted


def _self_check() -> None:
    leads = [
        {
            "id": 1,
            "name": "Alice CEO",
            "job_title": "CEO",
            "company": "Acme",
            "company_domain": "acme.com",
            "website_link": "https://www.acme.com",
            "email": "",
            "apollo_id": "a1",
        },
        {
            "id": 2,
            "name": "Bob Manager",
            "job_title": "Manager",
            "company": "Acme",
            "company_domain": "acme.com",
            "website_link": "http://acme.com",
            "email": "",
            "apollo_id": "a2",
        },
        {
            "id": 3,
            "name": "Carol",
            "job_title": "Director",
            "company": "Beta",
            "company_domain": "beta.io",
            "website_link": "https://beta.io",
            "email": "",
            "apollo_id": "a3",
        },
    ]
    r = analyze_batch_leads(leads)
    assert r["total_leads"] == 3
    assert r["distinct_canonical_domains"] == 2
    assert len(r["duplicate_domain_groups"]) == 1
    assert r["duplicate_domain_groups"][0]["winner"] == "Alice CEO"
    assert r["column_matches_website"] == 3
    assert r["unique_domains_ok"] is False
    drops = get_duplicate_drops(r)
    assert len(drops) == 1 and drops[0]["name"] == "Bob Manager" and drops[0]["id"] == 2
    print("OK: batch_domain_audit self-check passed")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        _self_check()
        raise SystemExit(0)

    from backend.api import get_connection

    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if not name:
        print("Usage: python scripts/batch_domain_audit.py <batch_name>")
        print("       python scripts/batch_domain_audit.py --self-check")
        raise SystemExit(1)

    with get_connection() as conn:
        resolved = resolve_batch_name(conn, name, [])
        if not resolved:
            print(f"[ERROR] Batch not found: '{name}'")
            raise SystemExit(1)
        report = audit_batch(conn, resolved)
        print_batch_audit_report(report)
        prompt_delete_duplicate_drops(conn, report)
    raise SystemExit(0 if report.get("unique_domains_ok") and report.get("domain_column_ok") else 1)
