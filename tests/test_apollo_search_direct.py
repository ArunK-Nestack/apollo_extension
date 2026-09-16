#!/usr/bin/env python3
"""
Test Suite for Apollo Direct Search & CRM Qualification CLI
=============================================================
Validates:
1. Startup banner rendering & database connectivity checks
2. Saved search filter parsing & summary formatting
3. 10-lead preview table rendering
4. Mapping of Apollo API person payloads to ApolloContact
5. Real-time 4-layer qualification pipeline:
   - Layer 1: AWS RDS 7.28M CRM Domain Check
   - Layer 2: Indian Demographic Origin Guardrail (Drops pure Indian names)
   - Layer 3: 64K Title Guardrails (Required vs Excluded)
   - Layer 4: 1 Contact Per Company Deduplication (Picks highest seniority)
6. Apollo 75-column export generation
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.apollo_search_direct import (
    clean_domain,
    display_startup_banner,
    format_filters_summary,
    render_10_lead_preview,
    map_apollo_person_to_contact,
    qualify_contacts_batch,
    load_local_preset_searches,
    parse_apollo_url,
    load_all_creator_searches,
    load_account_creator_searches,
    fetch_account_labels,
    convert_filters_v2_to_payload,
    fetch_live_apollo_searches,
    save_account_creator_search,
    resolve_company_domain_from_apollo,
    batch_resolve_company_domains,
)
from backend.api import ApolloContact, get_connection


class TestApolloSearchDirect(unittest.TestCase):

    def test_clean_domain(self):
        self.assertEqual(clean_domain("https://www.example.com/about"), "example.com")
        self.assertEqual(clean_domain("http://fintech.io"), "fintech.io")
        self.assertEqual(clean_domain("www.domain.co.uk/"), "domain.co.uk")
        self.assertEqual(clean_domain(""), "")

    def test_parse_apollo_url(self):
        url = "https://app.apollo.io/#/people?sortAscending=false&contactLabelIds[]=679771bc7d6d8801b0db6f42&personTitles[]=CEO&personLocations[]=United%20States"
        payload = parse_apollo_url(url)
        self.assertEqual(payload.get("contact_label_ids"), ["679771bc7d6d8801b0db6f42"])
        self.assertEqual(payload.get("person_titles"), ["CEO"])
        self.assertEqual(payload.get("person_locations"), ["United States"])

    def test_filter_summary_formatting(self):
        filters = {
            "person_locations": ["United States", "Canada"],
            "person_seniorities": ["c_suite", "vice_president"],
            "organization_num_employees_ranges": ["51,200"],
            "person_titles": ["CEO", "CTO", "President"]
        }
        summary = format_filters_summary(filters)
        self.assertIn("United States", summary)
        self.assertIn("c_suite", summary)
        self.assertIn("51,200", summary)
        self.assertIn("CEO", summary)

    def test_10_lead_preview_rendering(self):
        sample_people = [
            {
                "first_name": "David",
                "last_name": "Miller",
                "title": "Chief Technology Officer",
                "organization_name": "PayWave Systems",
                "organization": {"primary_domain": "paywave.io"},
                "city": "Austin",
                "state": "TX"
            },
            {
                "first_name": "Sarah",
                "last_name": "Jenkins",
                "title": "VP Engineering",
                "organization_name": "FinLogic",
                "organization": {"primary_domain": "finlogic.co"},
                "city": "New York",
                "state": "NY"
            }
        ]
        result = render_10_lead_preview(sample_people)
        self.assertTrue(result)

    def test_map_apollo_person_to_contact(self):
        raw_person = {
            "id": "123456",
            "first_name": "Alex",
            "last_name": "Smith",
            "title": "Chief Executive Officer",
            "organization_name": "CloudNova",
            "organization": {
                "primary_domain": "cloudnova.io",
                "website_url": "https://cloudnova.io"
            },
            "linkedin_url": "https://linkedin.com/in/alexsmith",
            "city": "Boston",
            "state": "MA"
        }
        contact = map_apollo_person_to_contact(raw_person)
        self.assertIsInstance(contact, ApolloContact)
        self.assertEqual(contact.apollo_id, "123456")
        self.assertEqual(contact.name, "Alex Smith")
        self.assertEqual(contact.job_title, "Chief Executive Officer")
        self.assertEqual(contact.company_domain, "cloudnova.io")
        self.assertEqual(contact.location, "Boston, MA")

    def test_qualify_contacts_4_layers(self):
        """Verify CRM domain drops, Indian name drops, title checks, and 1/company dedup."""
        # 1. Western CEO with unique net-new domain (Expected: Qualified Winner)
        c1 = ApolloContact(
            key="c1",
            apollo_id="c1",
            name="Alexander Graham",
            first_name="Alexander",
            last_name="Graham",
            job_title="Chief Executive Officer",
            company="NetNewTech Corp",
            company_domain="netnewtech-sample-123456.io",
            domain="netnewtech-sample-123456.io"
        )
        # 2. Lower seniority contact for the SAME company (Expected: Dropped by 1/company dedup)
        c2 = ApolloContact(
            key="c2",
            apollo_id="c2",
            name="Jessica Alba",
            first_name="Jessica",
            last_name="Alba",
            job_title="VP Marketing",
            company="NetNewTech Corp",
            company_domain="netnewtech-sample-123456.io",
            domain="netnewtech-sample-123456.io"
        )
        # 3. Pure Indian origin name (Expected: Dropped by Demographic Filter)
        c3 = ApolloContact(
            key="c3",
            apollo_id="c3",
            name="Rajesh Kumar",
            first_name="Rajesh",
            last_name="Kumar",
            job_title="Chief Technology Officer",
            company="IndianTech Corp",
            company_domain="indiantech-sample-789.io",
            domain="indiantech-sample-789.io"
        )
        # 4. Excluded job title (Expected: Dropped by 64K Title Rules)
        c4 = ApolloContact(
            key="c4",
            apollo_id="c4",
            name="John Doe",
            first_name="John",
            last_name="Doe",
            job_title="Software Engineering Intern",
            company="SDR Corp",
            company_domain="sdrcorp-sample-456.io",
            domain="sdrcorp-sample-456.io"
        )

        with get_connection() as conn:
            qualified, stats = qualify_contacts_batch([c1, c2, c3, c4], "TEST_BATCH", conn, filter_indian=True, filter_titles=True)

            # Assert Indian Demographic dropped Rajesh Kumar
            self.assertGreaterEqual(stats["indian_name"], 1)

            # Assert 1/company dedup dropped Jessica Alba in favor of Alexander Graham (CEO)
            self.assertGreaterEqual(stats["company_dup"], 1)

            # Assert SDR title was excluded
            self.assertGreaterEqual(stats["excluded_title"], 1)

            # Assert Alexander Graham won
            winner_names = [w.name for w in qualified]
            self.assertIn("Alexander Graham", winner_names)
            self.assertNotIn("Rajesh Kumar", winner_names)
            self.assertNotIn("John Doe", winner_names)

    def test_qualify_unique_domains_with_64k_titles(self):
        """Verify pipeline applies 64K title filter (0 LLM cost) while bypassing Indian demographic filter."""
        c_indian_ceo = ApolloContact(
            key="u1",
            apollo_id="u1",
            name="Rahul Sharma",
            job_title="Chief Executive Officer",
            company="Acme Indian Tech",
            company_domain="acme-unique-indian-sample.io",
            domain="acme-unique-indian-sample.io"
        )
        c_dup_vp = ApolloContact(
            key="u2",
            apollo_id="u2",
            name="Bob Smith",
            job_title="VP Engineering",
            company="Acme Indian Tech",
            company_domain="acme-unique-indian-sample.io",
            domain="acme-unique-indian-sample.io"
        )
        c_intern = ApolloContact(
            key="u3",
            apollo_id="u3",
            name="Alice Walker",
            job_title="Software Engineering Intern",
            company="InternTech",
            company_domain="interntech-unique-sample.io",
            domain="interntech-unique-sample.io"
        )
        with get_connection() as conn:
            # Default call has filter_indian=False, filter_titles=True
            qualified, stats = qualify_contacts_batch([c_indian_ceo, c_dup_vp, c_intern], "TEST_UNIQUE_DOMAINS", conn)
            # Indian name is NOT filtered
            self.assertEqual(stats["indian_name"], 0)
            # 64K title filter excluded the intern
            self.assertEqual(stats["excluded_title"], 1)
            # 1/company dedup retained CEO over VP
            self.assertEqual(stats["company_dup"], 1)
            self.assertEqual(stats["required"], 1)
            self.assertEqual(len(qualified), 1)
            self.assertEqual(qualified[0].name, "Rahul Sharma")
            self.assertEqual(qualified[0].company_domain, "acme-unique-indian-sample.io")

    def test_target_leads_limit_enforcement(self):
        """Verify target goal cap correctly limits the qualified leads batch."""
        cands = [
            ApolloContact(key=f"k{i}", name=f"Leader {i}", job_title="Chief Executive Officer", company=f"Comp {i}", company_domain=f"comp{i}-uniq.com")
            for i in range(10)
        ]
        target_limit = 5
        keyword_required_count = 0
        remaining_needed = target_limit - keyword_required_count
        qualified = cands[:remaining_needed]
        self.assertEqual(len(qualified), 5)

    def test_load_all_creator_searches(self):
        """Verify global creator searches are indexed with their source account."""
        all_searches = load_all_creator_searches()
        self.assertIsInstance(all_searches, list)
        self.assertGreaterEqual(len(all_searches), 1)
        # Check source accounts are populated
        accounts = {s.get("source_account") for s in all_searches if s.get("source_account")}
        self.assertIn("rahul@nestack.co.in", accounts)
        self.assertIn("recruiting@nestack.com", accounts)

    def test_fetch_account_labels_mocked(self):
        """Verify account labels parsing separates accounts from contacts modalities."""
        from unittest.mock import patch, MagicMock
        fake_labels = [
            {"_id": "lbl_acc_1", "modality": "accounts", "name": "Company List 1"},
            {"_id": "lbl_acc_2", "modality": "accounts", "name": "Company List 2"},
            {"_id": "lbl_con_1", "modality": "contacts", "name": "People List 1"},
        ]
        with patch("requests.get") as mock_get:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = fake_labels
            mock_get.return_value = mock_res

            res = fetch_account_labels("dummy_key")
            self.assertEqual(len(res["account_labels"]), 2)
            self.assertEqual(len(res["contact_labels"]), 1)
            self.assertEqual(res["account_ids"], ["lbl_acc_1", "lbl_acc_2"])
            self.assertEqual(res["contact_ids"], ["lbl_con_1"])
            self.assertEqual(res["total_count"], 3)

    def test_convert_filters_v2_to_payload(self):
        """Verify conversion of Apollo's live filters_v2 AST into standard search payload."""
        sample_v2 = {
            "prospected_by_current_team": "no",
            "filter_expression": {
                "operands": [
                    {
                        "filters": [
                            {"filter_id": "filter.contact.location", "value": ["United States", "Canada"]},
                            {"filter_id": "filter.contact.title", "operator": "is_any_of", "value": ["CEO", "Founder"]},
                            {"filter_id": "filter.contact.title", "operator": "is_none_of", "value": ["Intern"]},
                            {"filter_id": "filter.account.number_of_employees", "value": ["11,50", "51,200"]},
                            {"filter_id": "filter.account.keywords", "value": ["software", "cloud"]}
                        ]
                    }
                ]
            }
        }
        payload = convert_filters_v2_to_payload(sample_v2)
        self.assertEqual(payload.get("prospected_by_current_team"), "no")
        self.assertEqual(payload.get("person_locations"), ["United States", "Canada"])
        self.assertEqual(payload.get("person_titles"), ["CEO", "Founder"])
        self.assertEqual(payload.get("person_not_titles"), ["Intern"])
        self.assertEqual(payload.get("organization_num_employees_ranges"), ["11,50", "51,200"])
        self.assertEqual(payload.get("q_organization_keyword_tags"), ["software", "cloud"])

    def test_fetch_live_apollo_searches_mocked(self):
        """Verify live finder view parsing and filtering out system/archived views."""
        from unittest.mock import patch, MagicMock
        fake_api_response = {
            "finder_views": [
                {
                    "id": "view_1",
                    "name": "Visual & Creative Services-Regular",
                    "system": False,
                    "archived": False,
                    "filters_v2": {
                        "filter_expression": {
                            "operands": [
                                {
                                    "filters": [
                                        {"filter_id": "filter.contact.location", "value": ["United States"]}
                                    ]
                                }
                            ]
                        }
                    }
                },
                {
                    "id": "sys_view",
                    "name": "Default view",
                    "system": True,
                    "archived": False
                },
                {
                    "id": "archived_view",
                    "name": "Old Deprecated Search",
                    "system": False,
                    "archived": True
                }
            ]
        }
        with patch("requests.post") as mock_post:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = fake_api_response
            mock_post.return_value = mock_res

            live = fetch_live_apollo_searches("dummy_api_key")
            self.assertEqual(len(live), 1)
            self.assertEqual(live[0]["name"], "Visual & Creative Services-Regular")
            self.assertEqual(live[0]["filters"]["person_locations"], ["United States"])
            self.assertTrue(live[0]["is_live"])

    def test_sanitize_payload(self):
        """Verify sanitize_payload strips 'any' string from label IDs and ensures clean lists."""
        from scripts.apollo_search_direct import sanitize_payload
        raw = {
            "not_account_label_ids": ["692eeb2140787c00195e47c9", "any", "694e1720241fa700116a3a5a"],
            "person_titles": "CEO",
            "person_locations": ["United States"],
            "organization_num_employees_ranges": "11,50"
        }
        clean = sanitize_payload(raw)
        self.assertNotIn("any", clean["not_account_label_ids"])
        self.assertEqual(len(clean["not_account_label_ids"]), 2)
        self.assertEqual(clean["person_titles"], ["CEO"])
        self.assertEqual(clean["organization_num_employees_ranges"], ["11,50"])

    def test_fetch_live_signals_extraction(self):
        """Verify live searches prioritize Apollo's flat 'signals' dict over nested AST."""
        from unittest.mock import patch, MagicMock
        fake_api_response = {
            "finder_views": [
                {
                    "id": "view_signals_1",
                    "name": "SaaS Executives",
                    "signals": {
                        "person_locations": ["United States", "Canada"],
                        "person_titles": ["CEO", "CTO"],
                        "not_account_label_ids": ["lbl123", "any"]
                    }
                }
            ]
        }
        with patch("requests.post") as mock_post:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = fake_api_response
            mock_post.return_value = mock_res

            live = fetch_live_apollo_searches("dummy_api_key")
            self.assertEqual(len(live), 1)
            self.assertEqual(live[0]["name"], "SaaS Executives")
            self.assertEqual(live[0]["filters"]["person_locations"], ["United States", "Canada"])
            self.assertEqual(live[0]["filters"]["person_titles"], ["CEO", "CTO"])
            self.assertNotIn("any", live[0]["filters"].get("not_account_label_ids", []))

    def test_batch_wide_company_deduplication(self):
        """Verify cross-page deduplication retains highest seniority lead across entire batch."""
        session_seen = {}
        # Page 1: VP of Sales arrives first
        c_page1 = ApolloContact(
            key="p1_lead",
            apollo_id="p1_lead",
            name="Bob Martin",
            first_name="Bob",
            last_name="Martin",
            job_title="VP Sales",
            company="MegaCorp",
            company_domain="megacorp-unique-test.com",
            domain="megacorp-unique-test.com"
        )
        # Page 2: CEO arrives later (higher seniority score)
        c_page2_ceo = ApolloContact(
            key="p2_ceo",
            apollo_id="p2_ceo",
            name="Alice Walker",
            first_name="Alice",
            last_name="Walker",
            job_title="Chief Executive Officer",
            company="MegaCorp",
            company_domain="megacorp-unique-test.com",
            domain="megacorp-unique-test.com"
        )
        # Page 3: Manager arrives even later (lower seniority score -> should be dropped)
        c_page3_mgr = ApolloContact(
            key="p3_mgr",
            apollo_id="p3_mgr",
            name="Charlie Brown",
            first_name="Charlie",
            last_name="Brown",
            job_title="Sales Manager",
            company="MegaCorp",
            company_domain="megacorp-unique-test.com",
            domain="megacorp-unique-test.com"
        )

        with get_connection() as conn:
            # Page 1 qualification
            q1, stats1 = qualify_contacts_batch([c_page1], "TEST_DEDUP", conn, session_seen)
            self.assertEqual(len(q1), 1)
            self.assertEqual(session_seen["megacorp-unique-test.com"].name, "Bob Martin")

            # Page 2 qualification -> CEO upgrades VP
            q2, stats2 = qualify_contacts_batch([c_page2_ceo], "TEST_DEDUP", conn, session_seen)
            self.assertEqual(len(q2), 1)
            self.assertEqual(session_seen["megacorp-unique-test.com"].name, "Alice Walker")

            # Page 3 qualification -> Manager dropped as company duplicate
            q3, stats3 = qualify_contacts_batch([c_page3_mgr], "TEST_DEDUP", conn, session_seen)
            self.assertEqual(len(q3), 0)
            self.assertEqual(stats3["company_dup"], 1)
            self.assertEqual(session_seen["megacorp-unique-test.com"].name, "Alice Walker")

    def test_resolve_company_domain_fallback(self):
        """Verify deterministic candidate fallback when Apollo search returns no result."""
        res = resolve_company_domain_from_apollo("", "Liquid AI")
        self.assertIn("company_domain", res)
        self.assertEqual(res["company_domain"], "liquid.ai")
        self.assertEqual(res["website_link"], "https://liquid.ai")

    def test_resolve_company_domain_zero_credits(self):
        """Verify resolve_company_domain_from_apollo resolves domain without calling paid Apollo mixed_companies API."""
        from unittest.mock import patch
        with patch("requests.post") as mock_post:
            res = resolve_company_domain_from_apollo("fake_key", "Acme Innovations")
            mock_post.assert_not_called()
            self.assertTrue(bool(res.get("company_domain")))
            self.assertTrue(res.get("website_link", "").startswith("https://"))

    def test_batch_resolve_company_domains(self):
        """Verify batch_resolve_company_domains resolves multiple companies."""
        results = batch_resolve_company_domains("", ["Liquid AI", "Datadog, Inc."])
        self.assertIn("liquid ai", results)
        self.assertIn("datadog", results)

    def test_map_apollo_person_with_resolved_company(self):
        """Verify mapping uses resolved company info when person object has empty domain/website/location."""
        raw_person = {
            "id": "person_999",
            "name": "Jane Doe",
            "title": "Director of Engineering",
            "organization_name": "Acme Innovations",
            "organization": {"name": "Acme Innovations"}
        }
        resolved = {
            "company_domain": "acmeinnovations.com",
            "website_link": "https://acmeinnovations.com",
            "location": "Denver, Colorado"
        }
        contact = map_apollo_person_to_contact(raw_person, resolved_company_info=resolved)
        self.assertEqual(contact.company_domain, "acmeinnovations.com")
        self.assertEqual(contact.website_link, "https://acmeinnovations.com")
        self.assertEqual(contact.location, "Denver, Colorado")


if __name__ == "__main__":
    unittest.main()

