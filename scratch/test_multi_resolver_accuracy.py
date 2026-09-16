#!/usr/bin/env python3
"""
Benchmark Multi-Resolver Comparison & Multi-Candidate CRM Check
against batch 'recruiting-sep' (432 enriched leads).
"""

import os
import sys
import re
import json
import urllib.request
import urllib.parse
from typing import List, Set, Dict, Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import (
    get_connection,
    clean_company_name,
    generate_candidate_domains,
    extract_root_domain,
    normalize_text
)
from scripts.domain_resolver_engine import (
    clean_domain,
    _has_active_dns,
    _generate_smart_permutations,
    AGGREGATOR_DOMAINS
)


def check_clearbit_strict(company_name: str) -> Optional[str]:
    """Strict Clearbit matching: 100% token equality, no dropped distinctive tokens."""
    if not company_name or len(company_name.strip()) < 2:
        return None
    cleaned = clean_company_name(company_name).strip()
    if not cleaned:
        return None
    target_tokens = set(re.sub(r"[^a-zA-Z0-9\s]", "", cleaned.lower()).split())
    if not target_tokens:
        return None

    try:
        url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={urllib.parse.quote(cleaned)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data or not isinstance(data, list):
                return None
            for item in data[:3]:
                cand_name = item.get("name") or ""
                cand_dom = clean_domain(item.get("domain") or "")
                if not cand_dom or not cand_name:
                    continue
                cand_tokens = set(re.sub(r"[^a-zA-Z0-9\s]", "", cand_name.lower()).split())
                # STRICT: tokens must match exactly, or only differ by standard legal suffix
                legal_suffixes = {"inc", "llc", "ltd", "corp", "corporation", "co", "company"}
                diff = target_tokens ^ cand_tokens
                if not diff or diff.issubset(legal_suffixes):
                    return cand_dom
    except Exception:
        pass
    return None


def get_all_candidate_domains(company_name: str) -> Set[str]:
    """Generate all candidate permutations for a company name."""
    candidates = set()
    cleaned = clean_company_name(company_name).strip()
    if not cleaned:
        return candidates

    # 1. Direct standard permutations from backend.api
    for c in generate_candidate_domains(cleaned):
        cd = clean_domain(c)
        if cd:
            candidates.add(cd)

    # 2. Smart permutations (root-brand stripped)
    for c in _generate_smart_permutations(cleaned):
        cd = clean_domain(c)
        if cd:
            candidates.add(cd)

    return candidates


def main():
    print("=" * 80)
    print("TESTING MULTI-RESOLVER + MULTI-CANDIDATE CRM CHECK ON 'recruiting-sep'")
    print("=" * 80)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Fetch all leads from recruiting-sep that have emails
            cur.execute("""
                SELECT id, name, company, company_domain, email
                FROM apollo_saved_leads
                WHERE batch = 'recruiting-sep' AND email IS NOT NULL AND email != ''
            """)
            leads = cur.fetchall()

            # 2. Find which leads are genuinely in the CRM
            all_emails = [r[4].strip().lower() for r in leads if r[4]]
            format_strings = ",".join(["%s"] * len(all_emails))
            cur.execute(f"SELECT email, domain FROM emails WHERE email IN ({format_strings})", all_emails)
            crm_email_map = {r[0].lower().strip(): r[1].lower().strip() for r in cur.fetchall()}

            # Check email domains in CRM
            all_email_doms = list(set(e.split("@")[-1].lower().strip() for e in all_emails if "@" in e))
            fmt_doms = ",".join(["%s"] * len(all_email_doms))
            cur.execute(f"SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt_doms})", all_email_doms)
            crm_domains = set(r[0].lower().strip() for r in cur.fetchall())

    collided_leads = []
    net_new_leads = []
    for l in leads:
        em = (l[4] or "").lower().strip()
        edom = em.split("@")[-1].strip() if "@" in em else ""
        if em in crm_email_map or edom in crm_domains:
            collided_leads.append(l)
        else:
            net_new_leads.append(l)

    print(f"Total Enriched Leads Audited: {len(leads)}")
    print(f"  • Collided with CRM (Wasted Credits): {len(collided_leads)}")
    print(f"  • Genuinely Net-New (Good Credits):   {len(net_new_leads)}")
    print("-" * 80)

    # Test Multi-Candidate CRM Check
    print("\n[EVALUATING MULTI-CANDIDATE CRM SHIELD ON COLLIDED LEADS]")
    caught_before_enrichment = 0
    missed_leads = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            for l in collided_leads:
                lid, name, company, old_domain, em = l
                # Generate candidate domains for this company name
                cands = list(get_all_candidate_domains(company))
                if old_domain:
                    cands.append(clean_domain(old_domain))
                cands = list(dict.fromkeys(cands))

                # Check if ANY candidate domain is in CRM
                if cands:
                    fmt = ",".join(["%s"] * len(cands))
                    cur.execute(f"SELECT domain FROM emails WHERE domain IN ({fmt}) LIMIT 1", cands)
                    hit = cur.fetchone()
                    if hit:
                        caught_before_enrichment += 1
                        continue

                missed_leads.append((company, old_domain, em))

    caught_pct = (caught_before_enrichment / len(collided_leads)) * 100 if collided_leads else 0
    print(f"Collided Leads Caught BEFORE Enrichment: {caught_before_enrichment} / {len(collided_leads)} ({caught_pct:.1f}%)")
    print(f"Credits Protected: {caught_before_enrichment} Apollo Credits Saved!")
    print(f"Still Missed: {len(missed_leads)}")

    print("\nSample Missed Leads (Why did they slip past candidate generator?):")
    for m in missed_leads[:10]:
        print(f"  Company: '{m[0]}' | Old Domain: '{m[1]}' | Real Enriched Email: '{m[2]}'")


if __name__ == "__main__":
    main()
