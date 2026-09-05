"""
Hard Test Cases Suite for Multi-Layer Domain Deduplication & Guardrails
======================================================================
Tests:
1. Unit Tests:
   - _clean_slug (normalization of hyphens, dots, country TLDs)
   - _lcs_ratio (longest common substring accuracy)
   - DomainPrefixTrie (prefix matching, min_prefix_len=4, boundary safety)
2. Hard Duplicate Scenarios (Must Detect & Block - Expected: True):
   - Category A: Dealership Branch Extensions (e.g. mullinaxfordkiss -> mullinaxford)
   - Category B: Punctuation, Dots & Subdomains (e.g. lubri-care -> lubricare, manukau.toyota.co.nz -> manukautoyota)
   - Category C: Short Brand Roots (4 chars: goaz, star, audi, jeep, ford, karl)
   - Category D: Long Compound Brand Roots (mancave, janssen, cavender, sonax, thedavis, autotech)
   - Category E: Person-Anchor LCS Mismatched Branch Domains
3. Hard Negative Controls (Must NOT Block - False Positive Prevention - Expected: False):
   - Unrelated brands sharing generic substrings (starbucks vs starcar, oxford vs ford)
   - Different companies with different people (apple vs appletree, generalhospital vs generalmotors)
4. Full Benchmark Regression Test on 666 Real-World Mismatched Contacts:
   - Validates that overall accuracy >= 90%.
"""

import sys
import os
import re
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
import api


class TestUnitFunctions(unittest.TestCase):
    """Verify underlying string and trie mechanics."""

    def test_clean_slug(self):
        self.assertEqual(api._clean_slug("lubri-care.com"), "lubricare")
        self.assertEqual(api._clean_slug("manukau.toyota.co.nz"), "manukautoyota")
        self.assertEqual(api._clean_slug("tynan.com.au"), "tynan")
        self.assertEqual(api._clean_slug("e-z-go.com"), "ezgo")
        self.assertEqual(api._clean_slug("mullinaxfordkiss.com"), "mullinaxfordkiss")

    def test_lcs_ratio(self):
        ratio, common = api._lcs_ratio("mancavedetail", "mancavecolorado")
        self.assertEqual(common, "mancave")
        self.assertAlmostEqual(ratio, 7 / 13, places=2)

        ratio, common = api._lcs_ratio("janssenautogroup", "janssenmotors")
        self.assertEqual(common, "janssen")

        ratio, common = api._lcs_ratio("autotechct", "3017autotech")
        self.assertEqual(common, "autotech")

    def test_trie_prefix_matching(self):
        trie = api.DomainPrefixTrie()
        trie.insert("mullinaxford", "mullinaxford.com")
        trie.insert("jonhall", "jonhall.com")
        trie.insert("goaz", "goaz.com")
        trie.insert("audi", "audi.com")

        # Must match when incoming domain is a branch of DB domain
        match, prefix = trie.find_prefix_match("mullinaxfordkiss", min_prefix_len=4)
        self.assertEqual(match, "mullinaxford.com")
        self.assertEqual(prefix, "mullinaxford")

        # Short 4-char brand root
        match, prefix = trie.find_prefix_match("goazmotorcycles", min_prefix_len=4)
        self.assertEqual(match, "goaz.com")
        self.assertEqual(prefix, "goaz")

        # Identical slug should NOT match as branch (remaining must exist)
        match, prefix = trie.find_prefix_match("mullinaxford", min_prefix_len=4)
        self.assertEqual(match, "")


class TestHardDuplicateScenarios(unittest.TestCase):
    """
    Test 40+ hard edge cases that typically slip through simple exact matching.
    """

    def setUp(self):
        # Build local trie for mock DB domains
        self.trie = api.DomainPrefixTrie()
        known_db_domains = [
            "mullinaxford.com",
            "jonhall.com",
            "corteseauto.com",
            "goaz.com",
            "starcar.com",
            "karldirect.com",
            "ezgo.com",
            "audi.com",
            "ford.com",
            "jeep.com",
            "bensoncdj.com",
            "sonaxusa.com",
            "lubri-care.com",
            "cavenderinterests.com",
            "janssenmotors.com",
            "mancavecolorado.com",
            "autotechct.com",
            "thedavisrecoveryhouse.com",
            "manukau.toyota.co.nz",
            "penskeautomotive.com",
        ]
        for d in known_db_domains:
            slug = api._strip_tld(d)
            cslug = api._clean_slug(d)
            if slug:
                self.trie.insert(slug, d)
            if cslug and cslug != slug:
                self.trie.insert(cslug, d)

    def is_blocked(self, person_matched_in_db: bool, apollo_domain: str, db_domain: str) -> tuple[bool, str]:
        """Simulate Layer 1, Layer 2, Layer 3 check on a candidate."""
        clean_apollo = api._clean_slug(apollo_domain)
        clean_db = api._clean_slug(db_domain)

        # L1: Exact match
        if apollo_domain.lower() == db_domain.lower() or clean_apollo == clean_db:
            return True, "L1_exact"

        # L2: Person name match + LCS Domain overlap
        if person_matched_in_db:
            ratio, common = api._lcs_ratio(clean_db, clean_apollo)
            hit = (
                (ratio >= 0.65 and len(common) >= 4) or
                (ratio >= 0.50 and len(common) >= 5) or
                (ratio >= 0.55 and len(common) >= 4) or
                (clean_db.startswith(common) and clean_apollo.startswith(common) and len(common) >= 4) or
                (len(common) >= 7)
            )
            if hit:
                return True, f"L2_lcs ({common}, {ratio:.0%})"

        # L3: Prefix Trie match
        trie_match, prefix = self.trie.find_prefix_match(clean_apollo, min_prefix_len=4)
        if trie_match and trie_match.lower() != apollo_domain.lower():
            return True, f"L3_trie ({prefix} -> {trie_match})"

        return False, "None"

    def test_category_a_dealership_branches(self):
        """Dealership branches where location suffix is added."""
        hard_cases = [
            ("Mullinax Staff", "mullinaxfordkiss.com", "mullinaxford.com"),
            ("Mullinax Staff", "mullinaxfordapopka.com", "mullinaxford.com"),
            ("Jon Hall Staff", "jonhallchevrolet.com", "jonhall.com"),
            ("Jon Hall Staff", "jonhallhyundai.com", "jonhall.com"),
            ("Audi Staff", "audiwestchase.com", "audi.com"),
            ("Jeep Staff", "jeepofcherryhill.com", "jeep.com"),
            ("Ford Staff", "fordofsmithtown.com", "ford.com"),
        ]
        for person, apollo_dom, db_dom in hard_cases:
            blocked, reason = self.is_blocked(True, apollo_dom, db_dom)
            self.assertTrue(blocked, f"Failed to block dealership branch: {apollo_dom} vs {db_dom} ({reason})")

    def test_category_b_punctuation_and_subdomains(self):
        """Punctuation, dots, hyphens, and regional TLDs."""
        hard_cases = [
            ("Robert Corica", "lubricaredistributorsconn.com", "lubri-care.com"),
            ("Michael Gapes", "manukautoyota.co.nz", "manukau.toyota.co.nz"),
            ("EZGO Sales", "e-z-go-parts.com", "ezgo.com"),
            ("Corica Staff", "lubri-care-ny.com", "lubri-care.com"),
        ]
        for person, apollo_dom, db_dom in hard_cases:
            blocked, reason = self.is_blocked(True, apollo_dom, db_dom)
            self.assertTrue(blocked, f"Failed to block punctuated/subdomain branch: {apollo_dom} vs {db_dom} ({reason})")

    def test_category_c_short_brand_roots(self):
        """Short 4-character brand roots like goaz, star, karl, etc."""
        hard_cases = [
            ("Jay Tucker", "goazmotorcycles.com", "goaz.com"),
            ("Holly Jarrett", "starofquakertown.com", "starcar.com"),
            ("Leo Karl", "karlchevy.com", "karldirect.com"),
            ("Leo Karl", "karlextended.com", "karldirect.com"),
        ]
        for person, apollo_dom, db_dom in hard_cases:
            blocked, reason = self.is_blocked(True, apollo_dom, db_dom)
            self.assertTrue(blocked, f"Failed to block 4-char brand root: {apollo_dom} vs {db_dom} ({reason})")

    def test_category_d_long_compound_brand_roots(self):
        """Compound company group brand roots (ratio >= 50% or len >= 7)."""
        hard_cases = [
            ("Michael Bergren", "mancavedetail.com", "mancavecolorado.com"),
            ("Dave Janssen", "janssenautogroup.com", "janssenmotors.com"),
            ("Jon Briggs", "cavendercareers.com", "cavenderinterests.com"),
            ("Rob McCrary", "sonaxonline.com", "sonaxusa.com"),
            ("Marcie Davis", "thedavishouse.com", "thedavisrecoveryhouse.com"),
            ("Vincent Martello", "3017autotech.com", "autotechct.com"),
            ("Matthew McCormick", "bensonchryslerdodgejeep.com", "bensoncdj.com"),
        ]
        for person, apollo_dom, db_dom in hard_cases:
            blocked, reason = self.is_blocked(True, apollo_dom, db_dom)
            self.assertTrue(blocked, f"Failed to block compound brand root: {apollo_dom} vs {db_dom} ({reason})")

    def test_category_e_negative_controls_no_false_positives(self):
        """Negative controls: Unrelated companies MUST NOT be blocked."""
        negative_cases = [
            ("Alice Smith", "starbucks.com", "starcar.com"),          # Different company, different person (not in DB)
            ("Bob Jones", "apple.com", "appletree.com"),              # Different company
            ("Charlie Ray", "generalhospital.com", "generalmotors.com"), # Generic word 'general'
            ("Danielle Green", "firstnationalbank.com", "firststatebank.com"),
            ("Eve White", "oxford.com", "ford.com"),                  # Suffix match, not prefix!
        ]
        for person, apollo_dom, db_dom in negative_cases:
            blocked, reason = self.is_blocked(False, apollo_dom, db_dom)
            self.assertFalse(blocked, f"FALSE POSITIVE: Unrelated '{apollo_dom}' incorrectly blocked by '{db_dom}' via {reason}")


class TestRealWorldRegression(unittest.TestCase):
    """
    Test against the actual 666 real-world contacts from Apollo where domains differed.
    Enforces >= 90% accuracy target.
    """

    def test_666_real_world_accuracy(self):
        csv_path = r"C:\Users\test\.gemini\antigravity-ide\brain\406658bc-76c4-4741-8e31-b7a7dd91b7be\.user_uploaded\media_1788627511387.csv"
        if not os.path.exists(csv_path):
            self.skipTest(f"CSV file not found at {csv_path}")

        import csv
        csv_contacts = {}
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                fn = r.get("Contact : First name", "").strip()
                ln = r.get("Contact : Last name", "").strip()
                name = f"{fn} {ln}".strip()
                email = r.get("Contact : Emails", "").strip().lower()
                real_domain = email.split("@")[1] if "@" in email else ""
                if name:
                    csv_contacts[name.lower()] = {"name": name, "real_domain": real_domain}

        with api.get_connection() as conn:
            cur = conn.cursor()
            fmt = ",".join(["%s"] * len(csv_contacts))
            cur.execute(
                f"SELECT name, company_domain FROM apollo_saved_leads WHERE LOWER(name) IN ({fmt})",
                tuple(csv_contacts.keys())
            )
            saved_rows = cur.fetchall()

        apollo_map = {name.strip().lower(): apollo_domain.strip().lower() for name, apollo_domain in saved_rows if apollo_domain}

        test_contacts = []
        for name_lower, info in csv_contacts.items():
            apollo_dom = apollo_map.get(name_lower)
            if apollo_dom and apollo_dom != info["real_domain"]:
                test_contacts.append({
                    "name": info["name"],
                    "apollo_domain": apollo_dom,
                    "real_domain": info["real_domain"]
                })

        total = len(test_contacts)
        self.assertGreater(total, 600, "Should have loaded 600+ test contacts")

        all_apollo_domains = [c["apollo_domain"] for c in test_contacts]
        all_names = [c["name"] for c in test_contacts]

        with api.get_connection() as conn:
            cur = conn.cursor()
            schema = api.get_target_table_schema(conn)
            tbl = schema["table_name"]
            dom_col = schema["email_domain"]
            name_col = schema["name"] or "full_name"

            fmt_d = ",".join(["%s"] * len(all_apollo_domains))
            cur.execute(f"SELECT DISTINCT `{dom_col}` FROM `{tbl}` WHERE `{dom_col}` IN ({fmt_d})", tuple(all_apollo_domains))
            l1_exact_domains = {r[0].lower() for r in cur.fetchall()}

            fmt_n = ",".join(["%s"] * len(all_names))
            cur.execute(f"SELECT `{name_col}`, `{dom_col}` FROM `{tbl}` WHERE `{name_col}` IN ({fmt_n})", tuple(all_names))
            name_to_db_domains = {}
            for (nm, dom) in cur.fetchall():
                key = nm.strip().lower()
                if key not in name_to_db_domains:
                    name_to_db_domains[key] = []
                name_to_db_domains[key].append(dom.strip().lower())

            if not api._domain_trie.loaded:
                api.load_domain_trie(conn)

        blocked_count = 0
        for c in test_contacts:
            apollo_dom = c["apollo_domain"]
            name_lower = c["name"].lower()
            slug_apollo = api._clean_slug(apollo_dom)

            # L1
            if apollo_dom in l1_exact_domains:
                blocked_count += 1
                continue

            # L2
            db_domains = name_to_db_domains.get(name_lower, [])
            blocked_l2 = False
            for db_dom in db_domains:
                db_slug = api._clean_slug(db_dom)
                ratio, common = api._lcs_ratio(db_slug, slug_apollo)
                hit = (
                    (ratio >= 0.65 and len(common) >= 4) or
                    (ratio >= 0.50 and len(common) >= 5) or
                    (ratio >= 0.55 and len(common) >= 4) or
                    (len(common) >= 7)
                )
                if hit:
                    blocked_count += 1
                    blocked_l2 = True
                    break
            if blocked_l2:
                continue

            # L3
            if api._domain_trie.loaded and slug_apollo:
                trie_match, _ = api._domain_trie.find_prefix_match(slug_apollo, min_prefix_len=4)
                if trie_match and trie_match != apollo_dom:
                    blocked_count += 1
                    continue

        accuracy = (blocked_count / total) * 100.0
        print(f"\n[Real-World Benchmark] Blocked: {blocked_count}/{total} ({accuracy:.2f}%)")
        self.assertGreaterEqual(accuracy, 90.0, f"Accuracy {accuracy:.2f}% is below the required 90.0% threshold!")


if __name__ == "__main__":
    unittest.main(verbosity=2)
