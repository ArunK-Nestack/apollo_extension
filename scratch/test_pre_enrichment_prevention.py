import os
import sys
import re
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pandas as pd
from backend.api import get_connection, _clean_slug, extract_root_domain

downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
f_good = os.path.join(downloads_dir, "rahul@nestack.co.in(aug 13 - sep 13) - good - 2092 good - 3993 total.csv")
df = pd.read_csv(f_good)

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT name, company, company_domain, email
            FROM apollo_saved_leads
            WHERE batch = 'rahul_nestack_co_in-sep'
        """)
        db_leads = {r[3].strip().lower(): r for r in cur.fetchall() if r[3]}

        unique_edoms = list({e.split("@")[-1].strip().lower() for e in df["Email"].dropna() if "@" in e})
        chunk_size = 1000
        crm_matches = set()
        for i in range(0, len(unique_edoms), chunk_size):
            chunk = unique_edoms[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT DISTINCT domain FROM emails WHERE domain IN ({fmt})", chunk)
            for r in cur.fetchall():
                crm_matches.add(r[0].lower().strip())

        colliding = []
        for _, r in df.iterrows():
            em = str(r.get("Email", "")).strip().lower()
            if "@" in em and em.split("@")[-1] in crm_matches:
                db_lead = db_leads.get(em)
                if db_lead:
                    colliding.append((db_lead[0], db_lead[1], db_lead[2], em.split("@")[-1], em))

        print(f"Testing Pre-Enrichment Prevention Engine on {len(colliding)} colliding leads:")

        names = list({c[0] for c in colliding if c[0]})
        person_to_crm_doms = {}
        for i in range(0, len(names), chunk_size):
            chunk = names[i:i+chunk_size]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT full_name, domain FROM emails WHERE full_name IN ({fmt})", chunk)
            for r in cur.fetchall():
                person_to_crm_doms.setdefault(r[0].lower().strip(), []).append(r[1].lower().strip())

        prevented_count = 0
        prevented_by_reason = {"multi_tld": 0, "person_name_crm_dom": 0, "company_name_slug": 0}
        prevented_examples = []

        common_tlds = [".com", ".com.au", ".co.uk", ".ca", ".net", ".org", ".nz", ".com.nz", ".co.nz"]

        for name, company, scraped_dom, actual_edom, email in colliding:
            caught = False
            reason = ""

            # 1. Multi-TLD expansion of scraped domain
            base = scraped_dom.split(".")[0] if "." in scraped_dom else ""
            if base and len(base) >= 4:
                alt_tlds = [f"{base}{t}" for t in common_tlds if f"{base}{t}" != scraped_dom]
                fmt_t = ",".join(["%s"] * len(alt_tlds))
                cur.execute(f"SELECT domain FROM emails WHERE domain IN ({fmt_t}) LIMIT 1", tuple(alt_tlds))
                row = cur.fetchone()
                if row:
                    caught = True
                    reason = f"Multi-TLD: Scraped {scraped_dom} -> Sister TLD {row[0]} found in CRM"
                    prevented_by_reason["multi_tld"] += 1

            # 2. Person Name in CRM where CRM domain overlaps with Company Name or Scraped Domain
            if not caught and name.lower() in person_to_crm_doms:
                crm_doms = person_to_crm_doms[name.lower()]
                comp_slug = _clean_slug(company)
                for cdom in crm_doms:
                    cdom_slug = _clean_slug(cdom)
                    if (comp_slug and cdom_slug and (comp_slug in cdom_slug or cdom_slug in comp_slug or (len(comp_slug)>=3 and comp_slug[:4] == cdom_slug[:4]))):
                        caught = True
                        reason = f"Person Anchor: {name} in CRM at {cdom} matches company '{company}'"
                        prevented_by_reason["person_name_crm_dom"] += 1
                        break

            # 3. Company Name slug in CRM
            if not caught:
                comp_slug = _clean_slug(company)
                if comp_slug and len(comp_slug) >= 5:
                    cur.execute("SELECT domain FROM emails WHERE domain LIKE %s LIMIT 1", (f"{comp_slug}%",))
                    row = cur.fetchone()
                    if row:
                        caught = True
                        reason = f"Company Brand: '{company}' ({comp_slug}) matches CRM domain {row[0]}"
                        prevented_by_reason["company_name_slug"] += 1

            if caught:
                prevented_count += 1
                if len(prevented_examples) < 10:
                    prevented_examples.append((name, company, scraped_dom, actual_edom, reason))

        print(f"RESULTS:")
        print(f"  Total collisions prevented PRE-ENRICHMENT: {prevented_count} / {len(colliding)} ({prevented_count/len(colliding)*100:.1f}%)")
        print(f"  Breakdown: {prevented_by_reason}")
        print("\nSample pre-enrichment saves (0 credits spent):")
        for ex in prevented_examples:
            print(f"  • {ex[0]} @ {ex[1]}")
            print(f"      Reason: {ex[4]}")
            print(f"      Actual Enriched Email Domain: {ex[3]} (Prevented burning 1 credit!)")
