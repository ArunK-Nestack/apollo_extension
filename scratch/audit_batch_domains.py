#!/usr/bin/env python3
"""Audit batch: unique domains + domain column vs website comparison."""
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import (
    get_connection,
    extract_root_domain,
    canonical_lookup_domain,
    lead_dict_to_contact,
    _domain_lookup_candidates,
)

BATCH = sys.argv[1] if len(sys.argv) > 1 else "rahul_nestack_co_in"


def main():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT batch, COUNT(*) c, COUNT(DISTINCT company_domain) d
                FROM apollo_saved_leads
                WHERE batch LIKE %s OR batch LIKE %s
                GROUP BY batch ORDER BY c DESC
                """,
                ("%rahul%", "%nestack_co_in%"),
            )
            batches = cur.fetchall()
            print("=== MATCHING BATCHES ===")
            for b, c, d in batches:
                print(f"  {b}: {c} leads, {d} distinct company_domain")

            cur.execute(
                """
                SELECT id, name, company, company_domain, website_link, email, job_title, apollo_id
                FROM apollo_saved_leads WHERE batch=%s ORDER BY company_domain, name
                """,
                (BATCH,),
            )
            rows = cur.fetchall()

    cols = [
        "id",
        "name",
        "company",
        "company_domain",
        "website_link",
        "email",
        "job_title",
        "apollo_id",
    ]
    leads = [dict(zip(cols, r)) for r in rows]
    print(f"\n=== BATCH: {BATCH} ({len(leads)} leads) ===")
    if not leads:
        return

    by_domain = defaultdict(list)
    by_canonical = defaultdict(list)
    column_vs_website_mismatch = []
    column_empty_website_used = []
    column_matches_website = 0
    both_empty = 0

    for L in leads:
        dom = (L["company_domain"] or "").strip().lower()
        web_root = extract_root_domain(L["website_link"] or "")
        col_root = extract_root_domain(dom)

        c = lead_dict_to_contact(
            {
                "name": L["name"],
                "job_title": L["job_title"],
                "company": L["company"],
                "company_domain": L["company_domain"],
                "website_link": L["website_link"],
                "email": L["email"],
                "apollo_id": L["apollo_id"],
            },
            key=str(L["id"]),
        )
        canonical = canonical_lookup_domain(c)

        by_domain[dom].append(L["name"])
        by_canonical[canonical].append(L)

        if not col_root and not web_root:
            both_empty += 1
        elif col_root and web_root and col_root == web_root:
            column_matches_website += 1
        elif col_root and web_root and col_root != web_root:
            column_vs_website_mismatch.append(
                (L["name"], col_root, web_root, L["website_link"], dom)
            )
        elif not col_root and web_root:
            column_empty_website_used.append((L["name"], web_root, L["website_link"]))

    dup_canonical = {d: ls for d, ls in by_canonical.items() if d and len(ls) > 1}
    dup_stored = {d: ns for d, ns in by_domain.items() if d and len(ns) > 1}

    print(f"Distinct company_domain stored: {len([d for d in by_domain if d])}")
    print(f"Distinct canonical lookup domains: {len([d for d in by_canonical if d])}")
    print(f"Column matches website root: {column_matches_website}")
    print(f"Column empty, website fallback: {len(column_empty_website_used)}")
    print(f"Column vs website differ (dual-candidate): {len(column_vs_website_mismatch)}")
    print(f"No domain source at all: {both_empty}")
    print(f"Duplicate canonical domains (guardrail FAIL): {len(dup_canonical)}")
    print(f"Duplicate stored company_domain: {len(dup_stored)}")

    if dup_canonical:
        print("\n--- DUPLICATE CANONICAL DOMAINS ---")
        for d, ls in sorted(dup_canonical.items()):
            print(f"  {d}:")
            for x in ls:
                print(
                    f"    - {x['name']} | col={x['company_domain']} | web={x['website_link']}"
                )

    if dup_stored:
        print("\n--- DUPLICATE STORED company_domain ---")
        for d, ns in sorted(dup_stored.items()):
            print(f"  {d}: {ns}")

    if column_vs_website_mismatch:
        print("\n--- COLUMN vs WEBSITE MISMATCH (column primary in lookup) ---")
        for name, col, web, link, stored in column_vs_website_mismatch:
            print(f"  {name}: stored={stored} column_root={col} website_root={web}")

    if column_empty_website_used:
        print("\n--- COLUMN EMPTY (website-only domain) ---")
        for name, web, link in column_empty_website_used:
            print(f"  {name}: website={web}")

    print("\n--- SAMPLE CANDIDATE CHAINS (first 10) ---")
    for L in leads[:10]:
        c = lead_dict_to_contact(
            {
                "name": L["name"],
                "job_title": L["job_title"],
                "company": L["company"],
                "company_domain": L["company_domain"],
                "website_link": L["website_link"],
                "email": L["email"],
                "apollo_id": L["apollo_id"],
            },
            key=str(L["id"]),
        )
        cands = _domain_lookup_candidates(c)
        print(f"  {L['name']}: {cands} | stored={L['company_domain']}")

    guardrail_pass(BATCH, leads)


def guardrail_pass(batch: str, leads: list[dict]) -> None:
    from collections import Counter

    from backend.api import get_seniority_score
    from scripts.lead_guardrails import apply_4_layer_guardrails

    lead_rows = [
        {
            "id": L["id"],
            "apollo_id": L["apollo_id"],
            "name": L["name"],
            "first_name": L.get("first_name"),
            "last_name": L.get("last_name"),
            "job_title": L["job_title"],
            "company": L["company"],
            "company_domain": L["company_domain"],
            "website_link": L["website_link"],
            "segment": L.get("segment"),
            "email": L.get("email") or "",
        }
        for L in leads
    ]
    eligible, metrics = apply_4_layer_guardrails(lead_rows, batch, verbose=False)
    print("\n=== GUARDRAIL RE-CHECK (L1-L4) ===")
    print(f"Eligible: {len(eligible)} / {len(leads)}")
    print(f"Metrics: {metrics}")
    c = Counter((x["company_domain"] or "").lower() for x in eligible)
    dup = {d: n for d, n in c.items() if d and n > 1}
    print(f"Duplicate domains among eligible: {dup or 'none'}")
    for L in leads:
        if (L["company_domain"] or "").lower() == "integralscaffolding.com.au":
            print(
                f"  seniority {L['name']}: {get_seniority_score(L['job_title'] or '')} ({L['job_title']})"
            )


if __name__ == "__main__":
    main()
