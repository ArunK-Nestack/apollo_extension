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
)
from scripts.lead_guardrails import apply_4_layer_guardrails


def resolve_batch_name(conn, user_input: str, batches: list[dict]) -> str | None:
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

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT batch FROM apollo_saved_leads
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


def fetch_batch_leads(conn, batch_name: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, apollo_id, name, first_name, last_name, job_title, company,
                   company_domain, website_link, email, segment
            FROM apollo_saved_leads
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


def audit_batch(
    conn,
    batch_name: str,
    *,
    run_guardrails: bool = True,
) -> dict[str, Any]:
    leads = fetch_batch_leads(conn, batch_name)
    analysis = analyze_batch_leads(leads)
    analysis["batch"] = batch_name

    if not leads:
        analysis["guardrail_metrics"] = {}
        analysis["guardrail_eligible"] = 0
        return analysis

    if run_guardrails:
        eligible, metrics = apply_4_layer_guardrails(leads, batch_name, conn, verbose=False)
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

    print("\n" + "=" * 92)
    print(f" BATCH DOMAIN AUDIT: {batch}")
    print("=" * 92)

    if total == 0:
        print(" [NOTICE] No leads in this batch.")
        print("=" * 92 + "\n")
        return

    unique_ok = result.get("unique_domains_ok", False)
    col_ok = result.get("domain_column_ok", False)
    safe_n = result.get("safe_enrich_count", 0)

    print(f" Total leads:                 {total:,d}")
    print(f" Distinct company_domain:       {result.get('distinct_stored_domains', 0):,d}")
    print(f" Distinct canonical domains:    {result.get('distinct_canonical_domains', 0):,d}")
    print()
    print(" DOMAIN COLUMN vs WEBSITE")
    print(f"   Column matches website:      {result.get('column_matches_website', 0):,d} / {total:,d}")
    print(f"   Column empty (web fallback): {result.get('column_empty_website_count', 0):,d}")
    print(f"   Column != website:         {result.get('column_mismatch_count', 0):,d}")
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
        print(" GUARDRAIL RE-CHECK (Layer 0 + L1-L4)")
        print(f"   Layer 0 unique:              {gm.get('l0_unique', 0):,d}  (-{gm.get('l0_removed', 0):,d})")
        print(f"   L1-L4 CRM blocked:           {gm.get('l1_l4_blocked', 0):,d}")
        print(f"   Final enrich-eligible:       {gm.get('final_verified', 0):,d}")
        print(f"   Verdict:                     {'PASS' if result.get('guardrail_unique_ok') else 'OK after L0'}")

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


def delete_duplicate_drops(conn, batch_name: str, drops: list[dict[str, Any]]) -> int:
    if not drops:
        return 0
    ids = [int(d["id"]) for d in drops]
    with conn.cursor() as cur:
        placeholders = ", ".join(["%s"] * len(ids))
        cur.execute(
            f"DELETE FROM `apollo_saved_leads` WHERE `batch` = %s AND `id` IN ({placeholders})",
            [batch_name] + ids,
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def prompt_delete_duplicate_drops(conn, report: dict[str, Any]) -> int:
    drops = get_duplicate_drops(report)
    if not drops:
        return 0

    batch = report.get("batch", "")
    print("\n--- DELETE DUPLICATE LEADS (DROP at enrich) ---")
    for d in drops:
        print(
            f"  - id={d['id']} | {d.get('name')} | {d.get('job_title') or ''} "
            f"| {d.get('domain')} | seniority={d.get('seniority')}"
        )

    ans = input(f"\nDelete {len(drops)} lower-seniority duplicate(s) from '{batch}'? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        print("Skipped deletion.")
        return 0

    deleted = delete_duplicate_drops(conn, batch, drops)
    print(f"Deleted {deleted} row(s).")
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
        if get_duplicate_drops(report):
            prompt_delete_duplicate_drops(conn, report)
    raise SystemExit(0 if report.get("unique_domains_ok") and report.get("domain_column_ok") else 1)
