import sys
import os
import time

sys.path.append(os.getcwd())
import backend.api as api
from backend.api import get_connection, _clean_slug, _strip_tld, _lcs_ratio, _is_valid_branch_match, extract_root_domain

batch = "vijay_nestacktech_com-sep-new"

def audit():
    print(f"Loading leads for batch: {batch}...")
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

    print(f"Total leads loaded: {len(leads)}")

    # Ensure Trie is loaded
    if not api._domain_trie.loaded:
        print("Loading domain trie...")
        t0 = time.perf_counter()
        api.load_domain_trie()
        print(f"Trie loaded in {time.perf_counter() - t0:.2f}s, nodes: {api._domain_trie.node_count}")

    # Check root domains
    raw_domains = [r[7] for r in leads if r[7]]
    root_domains = [extract_root_domain(d) for d in raw_domains]
    unique_raw = set(d.lower().strip() for d in raw_domains)
    unique_root = set(d.lower().strip() for d in root_domains if d)
    print(f"Unique raw domains: {len(unique_raw)}")
    print(f"Unique root domains (after extract_root_domain): {len(unique_root)}")

    if len(unique_raw) != len(unique_root):
        print(f"Difference in raw vs root domains: {len(unique_raw) - len(unique_root)}")
        # Find which domains collapsed
        root_map = {}
        for raw, root in zip(raw_domains, root_domains):
            raw_l = raw.lower().strip()
            root_l = root.lower().strip()
            if root_l not in root_map:
                root_map[root_l] = []
            if raw_l not in root_map[root_l]:
                root_map[root_l].append(raw_l)
        for root, raws in root_map.items():
            if len(raws) > 1:
                print(f"  Collapsed root '{root}' has multiple raw domains: {raws}")

if __name__ == "__main__":
    audit()
