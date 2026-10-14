"""
Unit & Integration Tests for Dynamic Company Capacity (1-10) and Seniority Selection
=====================================================================================
Tests:
1. get_canonical_title_key normalization & deduplication
2. get_seniority_score ranking
3. Category 1: Net-new company (E_db = 0) with dynamic capacity N, unique titles, outranking
4. Category 2: Company in DB (E_db > 0) with slot computation (N - E_db)
5. Category 2: At or exceeding cap (E_db >= N) with DB outranking
6. Credit Lock: leads with is_credit_spent=True are protected from eviction
7. Title Deduplication across database and active batch
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
import api


class TestCanonicalTitleKey(unittest.TestCase):
    """Test get_canonical_title_key function in api.py"""

    def test_executive_abbreviations(self):
        self.assertEqual(api.get_canonical_title_key("VP of Sales"), "sales_vp")
        self.assertEqual(api.get_canonical_title_key("Vice President, Sales"), "sales_vp")
        self.assertEqual(api.get_canonical_title_key("Vice President of Sales"), "sales_vp")
        self.assertEqual(api.get_canonical_title_key("VP Sales"), "sales_vp")

        self.assertEqual(api.get_canonical_title_key("Chief Executive Officer"), "ceo")
        self.assertEqual(api.get_canonical_title_key("CEO"), "ceo")

        self.assertEqual(api.get_canonical_title_key("Chief Technology Officer"), "cto")
        self.assertEqual(api.get_canonical_title_key("CTO"), "cto")

        self.assertEqual(api.get_canonical_title_key("Chief Financial Officer"), "cfo")
        self.assertEqual(api.get_canonical_title_key("CFO"), "cfo")

        self.assertEqual(api.get_canonical_title_key("Co-Founder"), "founder")
        self.assertEqual(api.get_canonical_title_key("Founder"), "founder")

    def test_distinct_roles_differ(self):
        self.assertNotEqual(
            api.get_canonical_title_key("VP of Sales"),
            api.get_canonical_title_key("VP of Marketing")
        )
        self.assertNotEqual(
            api.get_canonical_title_key("Sales Manager"),
            api.get_canonical_title_key("Sales Director")
        )

    def test_empty_and_punctuation(self):
        self.assertEqual(api.get_canonical_title_key(""), "")
        self.assertEqual(api.get_canonical_title_key(None), "")
        self.assertEqual(api.get_canonical_title_key("   "), "")
        self.assertEqual(api.get_canonical_title_key("Director -- Operations & Logistics"), "director_logistics_operations")


class TestSeniorityScoring(unittest.TestCase):
    """Test seniority score hierarchy in api.py"""

    def test_hierarchy_ordering(self):
        ceo_score = api.get_seniority_score("CEO")
        vp_score = api.get_seniority_score("VP of Sales")
        dir_score = api.get_seniority_score("Director of Marketing")
        mgr_score = api.get_seniority_score("Account Manager")
        rep_score = api.get_seniority_score("Sales Representative")

        self.assertGreater(ceo_score, vp_score)
        self.assertGreater(vp_score, dir_score)
        self.assertGreater(dir_score, mgr_score)
        self.assertGreater(mgr_score, rep_score)


class TestDynamicCompanyCapMatchApollo(unittest.TestCase):
    """Test match_apollo logic with mock DB connection"""

    def setUp(self):
        # Reset batch cache
        with api._batch_seen_companies_lock:
            api._batch_seen_companies.clear()

    @patch("api.get_connection")
    @patch("api.get_target_table_schema")
    @patch("api.resolve_company_domains")
    @patch("api.check_person_and_domains_in_crm_batch")
    @patch("api.lookup_job_titles_batch")
    @patch("api.fetch_existing_company_contacts_batch")
    def test_category_1_net_new_company_fills_up_to_cap(
        self, mock_fetch_db, mock_lookup_titles, mock_check_crm, mock_resolve_domains, mock_schema, mock_conn
    ):
        """Category 1: 0 contacts in DB. Max contacts = 3. Should admit 3 leads with distinct titles."""
        mock_conn.return_value.__enter__.return_value = MagicMock()
        mock_resolve_domains.return_value = (
            {"c1": "acme.com", "c2": "acme.com", "c3": "acme.com", "c4": "acme.com"},
            {"c1": "https://acme.com", "c2": "https://acme.com", "c3": "https://acme.com", "c4": "https://acme.com"}
        )
        mock_check_crm.return_value = {}  # Net new company
        mock_fetch_db.return_value = {}   # 0 contacts in DB

        mock_lookup_titles.return_value = {
            "CEO": {"required": True, "status": "qualified", "segment": "Leadership", "reason": "Exec"},
            "VP of Sales": {"required": True, "status": "qualified", "segment": "Sales", "reason": "VP"},
            "Director of IT": {"required": True, "status": "qualified", "segment": "IT", "reason": "Dir"},
            "Account Manager": {"required": True, "status": "qualified", "segment": "Sales", "reason": "Mgr"},
        }

        contacts = [
            api.ApolloContact(key="c1", name="Alice Smith", job_title="CEO", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c2", name="Bob Jones", job_title="VP of Sales", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c3", name="Charlie Brown", job_title="Director of IT", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c4", name="David Clark", job_title="Account Manager", company="Acme Corp", company_domain="acme.com"),
        ]

        req = api.ApolloMatchRequest(
            contacts=contacts,
            batch="test_cat1_batch",
            max_contacts_per_company=3,
            title_guardrail_enabled=True,
            indian_name_guardrail_enabled=False
        )

        res = api.match_apollo(req)

        self.assertTrue(res["results"]["c1"]["required"])
        self.assertTrue(res["results"]["c2"]["required"])
        self.assertTrue(res["results"]["c3"]["required"])
        # 4th lead exceeds cap (3) and score (Account Manager ~40) is lower than Charlie (Director ~65) -> rejected
        self.assertFalse(res["results"]["c4"]["required"])
        self.assertEqual(res["results"]["c4"]["guardrail_status"], "company_limit_reached")

    @patch("api.get_connection")
    @patch("api.get_target_table_schema")
    @patch("api.resolve_company_domains")
    @patch("api.check_person_and_domains_in_crm_batch")
    @patch("api.lookup_job_titles_batch")
    @patch("api.fetch_existing_company_contacts_batch")
    def test_duplicate_canonical_title_rejected(
        self, mock_fetch_db, mock_lookup_titles, mock_check_crm, mock_resolve_domains, mock_schema, mock_conn
    ):
        """Duplicate canonical title for same company must be rejected."""
        mock_conn.return_value.__enter__.return_value = MagicMock()
        mock_resolve_domains.return_value = (
            {"c1": "acme.com", "c2": "acme.com"},
            {"c1": "https://acme.com", "c2": "https://acme.com"}
        )
        mock_check_crm.return_value = {}
        mock_fetch_db.return_value = {}

        mock_lookup_titles.return_value = {
            "VP of Sales": {"required": True, "status": "qualified", "segment": "Sales", "reason": "VP"},
            "Vice President, Sales": {"required": True, "status": "qualified", "segment": "Sales", "reason": "VP"},
        }

        contacts = [
            api.ApolloContact(key="c1", name="Bob Jones", job_title="VP of Sales", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c2", name="Robert Jones", job_title="Vice President, Sales", company="Acme Corp", company_domain="acme.com"),
        ]

        req = api.ApolloMatchRequest(
            contacts=contacts,
            batch="test_dup_title_batch",
            max_contacts_per_company=3,
            title_guardrail_enabled=True,
            indian_name_guardrail_enabled=False
        )

        res = api.match_apollo(req)

        self.assertTrue(res["results"]["c1"]["required"])
        self.assertFalse(res["results"]["c2"]["required"])
        self.assertEqual(res["results"]["c2"]["guardrail_status"], "duplicate_title_for_company")

    @patch("api.get_connection")
    @patch("api.get_target_table_schema")
    @patch("api.resolve_company_domains")
    @patch("api.check_person_and_domains_in_crm_batch")
    @patch("api.lookup_job_titles_batch")
    @patch("api.fetch_existing_company_contacts_batch")
    def test_seniority_outranking_when_slots_full(
        self, mock_fetch_db, mock_lookup_titles, mock_check_crm, mock_resolve_domains, mock_schema, mock_conn
    ):
        """When cap (2) is full, a higher seniority lead replaces the lowest unlocked lead."""
        mock_conn.return_value.__enter__.return_value = MagicMock()
        mock_resolve_domains.return_value = (
            {"c1": "acme.com", "c2": "acme.com", "c3": "acme.com"},
            {"c1": "https://acme.com", "c2": "https://acme.com", "c3": "https://acme.com"}
        )
        mock_check_crm.return_value = {}
        mock_fetch_db.return_value = {}

        mock_lookup_titles.return_value = {
            "VP of Sales": {"required": True, "status": "qualified", "segment": "Sales", "reason": "VP"},
            "Sales Manager": {"required": True, "status": "qualified", "segment": "Sales", "reason": "Mgr"},
            "Chief Executive Officer": {"required": True, "status": "qualified", "segment": "Exec", "reason": "CEO"},
        }

        # c1: VP (score ~75), c2: Manager (score ~40). Cap = 2.
        # c3: CEO (score 100) arrives -> outranks Sales Manager (c2)!
        contacts = [
            api.ApolloContact(key="c1", name="Bob Jones", job_title="VP of Sales", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c2", name="Dan Smith", job_title="Sales Manager", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c3", name="Alice CEO", job_title="Chief Executive Officer", company="Acme Corp", company_domain="acme.com"),
        ]

        req = api.ApolloMatchRequest(
            contacts=contacts,
            batch="test_outrank_batch",
            max_contacts_per_company=2,
            title_guardrail_enabled=True,
            indian_name_guardrail_enabled=False
        )

        res = api.match_apollo(req)

        self.assertTrue(res["results"]["c1"]["required"])
        # c2 was initially admitted, but then outranked by c3
        self.assertFalse(res["results"]["c2"]["required"])
        self.assertEqual(res["results"]["c2"]["guardrail_status"], "company_limit_reached")
        # c3 is admitted
        self.assertTrue(res["results"]["c3"]["required"])

    @patch("api.get_connection")
    @patch("api.get_target_table_schema")
    @patch("api.resolve_company_domains")
    @patch("api.check_person_and_domains_in_crm_batch")
    @patch("api.lookup_job_titles_batch")
    @patch("api.fetch_existing_company_contacts_batch")
    def test_category_2_company_in_db_slot_calculation(
        self, mock_fetch_db, mock_lookup_titles, mock_check_crm, mock_resolve_domains, mock_schema, mock_conn
    ):
        """Category 2: DB has 2 contacts. Cap is 5. Open slots = 3. Should admit up to 3 leads."""
        mock_conn.return_value.__enter__.return_value = MagicMock()
        mock_resolve_domains.return_value = (
            {"c1": "acme.com", "c2": "acme.com", "c3": "acme.com", "c4": "acme.com"},
            {"c1": "https://acme.com", "c2": "https://acme.com", "c3": "https://acme.com", "c4": "https://acme.com"}
        )
        mock_check_crm.return_value = {}
        mock_fetch_db.return_value = {
            "acmecom": [
                {"name": "Old Lead 1", "job_title": "Account Executive", "canonical_title": "account_executive", "score": 25},
                {"name": "Old Lead 2", "job_title": "Sales Coordinator", "canonical_title": "coordinator_sales", "score": 20},
            ]
        }

        mock_lookup_titles.return_value = {
            "CEO": {"required": True, "status": "qualified", "segment": "Exec", "reason": "Exec"},
            "VP of Marketing": {"required": True, "status": "qualified", "segment": "Marketing", "reason": "VP"},
            "Director of HR": {"required": True, "status": "qualified", "segment": "HR", "reason": "Dir"},
            "Junior Analyst": {"required": True, "status": "qualified", "segment": "Analytics", "reason": "Analyst"},
        }

        contacts = [
            api.ApolloContact(key="c1", name="Lead 1", job_title="CEO", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c2", name="Lead 2", job_title="VP of Marketing", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c3", name="Lead 3", job_title="Director of HR", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c4", name="Lead 4", job_title="Junior Analyst", company="Acme Corp", company_domain="acme.com"),
        ]

        req = api.ApolloMatchRequest(
            contacts=contacts,
            batch="test_cat2_batch",
            max_contacts_per_company=5,
            title_guardrail_enabled=True,
            indian_name_guardrail_enabled=False
        )

        res = api.match_apollo(req)

        # 3 slots available: c1, c2, c3 admitted!
        self.assertTrue(res["results"]["c1"]["required"])
        self.assertTrue(res["results"]["c2"]["required"])
        self.assertTrue(res["results"]["c3"]["required"])
        # c4 Junior Analyst has score ~15, which does NOT outrank DB contacts (scores 20, 25) -> rejected
        self.assertFalse(res["results"]["c4"]["required"])
        self.assertEqual(res["results"]["c4"]["guardrail_status"], "company_limit_reached")

    @patch("api.get_connection")
    @patch("api.get_target_table_schema")
    @patch("api.resolve_company_domains")
    @patch("api.check_person_and_domains_in_crm_batch")
    @patch("api.lookup_job_titles_batch")
    @patch("api.fetch_existing_company_contacts_batch")
    def test_category_2_at_cap_outranks_db_contacts(
        self, mock_fetch_db, mock_lookup_titles, mock_check_crm, mock_resolve_domains, mock_schema, mock_conn
    ):
        """Category 2: DB has 3 contacts (cap = 3). Higher seniority lead outranks lowest DB contact."""
        mock_conn.return_value.__enter__.return_value = MagicMock()
        mock_resolve_domains.return_value = (
            {"c1": "acme.com", "c2": "acme.com"},
            {"c1": "https://acme.com", "c2": "https://acme.com"}
        )
        mock_check_crm.return_value = {}
        mock_fetch_db.return_value = {
            "acmecom": [
                {"name": "Old Lead 1", "job_title": "Account Executive", "canonical_title": "account_executive", "score": 25},
                {"name": "Old Lead 2", "job_title": "Marketing Manager", "canonical_title": "manager_marketing", "score": 45},
                {"name": "Old Lead 3", "job_title": "Director of Sales", "canonical_title": "director_sales", "score": 65},
            ]
        }

        mock_lookup_titles.return_value = {
            "VP of Engineering": {"required": True, "status": "qualified", "segment": "Eng", "reason": "VP"},
            "Intern": {"required": True, "status": "qualified", "segment": "Intern", "reason": "Intern"},
        }

        # c1: VP of Engineering (score 75 > 25 lowest DB contact) -> admitted!
        # c2: Intern (score 10 < 45 next DB contact) -> rejected!
        contacts = [
            api.ApolloContact(key="c1", name="VP Eng", job_title="VP of Engineering", company="Acme Corp", company_domain="acme.com"),
            api.ApolloContact(key="c2", name="Intern Guy", job_title="Intern", company="Acme Corp", company_domain="acme.com"),
        ]

        req = api.ApolloMatchRequest(
            contacts=contacts,
            batch="test_cat2_at_cap",
            max_contacts_per_company=3,
            title_guardrail_enabled=True,
            indian_name_guardrail_enabled=False
        )

        res = api.match_apollo(req)

        self.assertTrue(res["results"]["c1"]["required"])
        self.assertFalse(res["results"]["c2"]["required"])
        self.assertEqual(res["results"]["c2"]["guardrail_status"], "company_limit_reached")

    @patch("api.get_connection")
    @patch("api.get_target_table_schema")
    @patch("api.resolve_company_domains")
    @patch("api.check_person_and_domains_in_crm_batch")
    @patch("api.lookup_job_titles_batch")
    @patch("api.fetch_existing_company_contacts_batch")
    def test_credit_lock_protection_prevents_eviction(
        self, mock_fetch_db, mock_lookup_titles, mock_check_crm, mock_resolve_domains, mock_schema, mock_conn
    ):
        """Contact with is_credit_spent=True in batch cannot be evicted."""
        mock_conn.return_value.__enter__.return_value = MagicMock()
        mock_resolve_domains.return_value = (
            {"c2": "acme.com"},
            {"c2": "https://acme.com"}
        )
        mock_check_crm.return_value = {}
        mock_fetch_db.return_value = {}

        mock_lookup_titles.return_value = {
            "Chief Executive Officer": {"required": True, "status": "qualified", "segment": "Exec", "reason": "CEO"},
        }

        # Preload active batch with credit-locked lead (score 40)
        batch_tag = "test_credit_lock_batch"
        with api._batch_seen_companies_lock:
            api._batch_seen_companies[batch_tag] = {
                "acmecom": [{
                    "key": "c1",
                    "apollo_id": "c1",
                    "name": "Locked User",
                    "title": "Account Manager",
                    "canonical_title": "account_manager",
                    "score": 40,
                    "company": "Acme Corp",
                    "domain": "acme.com",
                    "is_credit_spent": True,  # CREDIT SPENT: LOCKED!
                }]
            }

        # c2 arrives with score 100 (CEO). Cap = 1.
        # Since c1 is locked and cap is 1, c1 CANNOT be evicted!
        contacts = [
            api.ApolloContact(key="c2", name="Alice CEO", job_title="Chief Executive Officer", company="Acme Corp", company_domain="acme.com"),
        ]

        req = api.ApolloMatchRequest(
            contacts=contacts,
            batch=batch_tag,
            max_contacts_per_company=1,
            title_guardrail_enabled=True,
            indian_name_guardrail_enabled=False
        )

        res = api.match_apollo(req)

        self.assertFalse(res["results"]["c2"]["required"])
        self.assertEqual(res["results"]["c2"]["guardrail_status"], "company_limit_reached")


class TestFilterSyncContactsDynamicCap(unittest.TestCase):
    """Test _filter_sync_contacts_dynamic_cap used in /sync-saved-leads."""

    def test_sync_allows_multiple_contacts_up_to_cap(self):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []  # No existing DB contacts

        contacts = [
            api.SyncSavedLeadItem(apollo_id="c1", name="Alice", job_title="CEO", company="Acme Corp", company_domain="acme.com"),
            api.SyncSavedLeadItem(apollo_id="c2", name="Bob", job_title="VP of Sales", company="Acme Corp", company_domain="acme.com"),
            api.SyncSavedLeadItem(apollo_id="c3", name="Charlie", job_title="Director of Engineering", company="Acme Corp", company_domain="acme.com"),
        ]

        # With cap = 3, all 3 distinct titles must be admitted
        res = api._filter_sync_contacts_dynamic_cap(contacts, "test_batch", mock_cur, max_contacts_per_company=3)
        self.assertEqual(len(res), 3)
        saved_names = {c.name for c in res}
        self.assertEqual(saved_names, {"Alice", "Bob", "Charlie"})

    def test_sync_rejects_duplicate_canonical_title(self):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []

        contacts = [
            api.SyncSavedLeadItem(apollo_id="c1", name="Bob Jones", job_title="VP of Sales", company="Acme Corp", company_domain="acme.com"),
            api.SyncSavedLeadItem(apollo_id="c2", name="Robert Jones", job_title="Vice President, Sales", company="Acme Corp", company_domain="acme.com"),
        ]

        # Both have canonical title 'sales_vp'; only one should be kept
        res = api._filter_sync_contacts_dynamic_cap(contacts, "test_batch", mock_cur, max_contacts_per_company=3)
        self.assertEqual(len(res), 1)

    def test_sync_cap_limits_total_contacts(self):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []

        contacts = [
            api.SyncSavedLeadItem(apollo_id="c1", name="Alice", job_title="CEO", company="Acme Corp", company_domain="acme.com"),
            api.SyncSavedLeadItem(apollo_id="c2", name="Bob", job_title="VP of Sales", company="Acme Corp", company_domain="acme.com"),
            api.SyncSavedLeadItem(apollo_id="c3", name="Charlie", job_title="Director of Engineering", company="Acme Corp", company_domain="acme.com"),
            api.SyncSavedLeadItem(apollo_id="c4", name="David", job_title="Account Manager", company="Acme Corp", company_domain="acme.com"),
        ]

        # With cap = 2, only top 2 by seniority (Alice CEO, Bob VP) must be admitted
        res = api._filter_sync_contacts_dynamic_cap(contacts, "test_batch", mock_cur, max_contacts_per_company=2)
        self.assertEqual(len(res), 2)
        saved_names = {c.name for c in res}
        self.assertEqual(saved_names, {"Alice", "Bob"})

    def test_sync_credit_lock_prevents_eviction(self):
        mock_cur = MagicMock()
        # DB already has 1 contact: Account Manager (score 40), but credits_charged=1 (LOCKED!)
        # Row format: (id, apollo_id, name, job_title, company, company_domain, credits_charged, email_status)
        mock_cur.fetchall.return_value = [
            (101, "c_old", "Old Lead", "Account Manager", "Acme Corp", "acme.com", 1, "revealed")
        ]

        # Cap = 1. Incoming contact is CEO (score 100).
        # Since DB contact has credits spent, it is protected from eviction!
        contacts = [
            api.SyncSavedLeadItem(apollo_id="c_new", name="Alice CEO", job_title="Chief Executive Officer", company="Acme Corp", company_domain="acme.com"),
        ]

        res = api._filter_sync_contacts_dynamic_cap(contacts, "test_batch", mock_cur, max_contacts_per_company=1)
        # Cannot evict credit-locked lead, so incoming cannot be added
        self.assertEqual(len(res), 0)

    def test_sync_outranking_replaces_unlocked_db_contact(self):
        mock_cur = MagicMock()
        # DB already has 1 contact: Intern (score 20), unlocked (credits=0, email_status="")
        mock_cur.fetchall.return_value = [
            (202, "c_intern", "Intern Guy", "Intern", "Acme Corp", "acme.com", 0, "")
        ]

        # Cap = 1. Incoming contact is VP of Sales (score 80).
        # Unlocked DB lead should be evicted and deleted!
        contacts = [
            api.SyncSavedLeadItem(apollo_id="c_vp", name="Bob VP", job_title="VP of Sales", company="Acme Corp", company_domain="acme.com"),
        ]

        res = api._filter_sync_contacts_dynamic_cap(contacts, "test_batch", mock_cur, max_contacts_per_company=1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].name, "Bob VP")
        # Verify delete was called for evicted DB lead id 202
        mock_cur.execute.assert_called()
        del_calls = [call for call in mock_cur.execute.call_args_list if "DELETE FROM `apollo_saved_leads`" in str(call)]
        self.assertTrue(len(del_calls) > 0)

    def test_legacy_one_per_domain_collapses_to_one(self):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []

        contacts = [
            api.SyncSavedLeadItem(apollo_id="c1", name="Alice", job_title="CEO", company="Acme Corp", company_domain="acme.com"),
            api.SyncSavedLeadItem(apollo_id="c2", name="Bob", job_title="VP of Sales", company="Acme Corp", company_domain="acme.com"),
        ]

        res = api._filter_sync_contacts_one_per_domain(contacts, "test_batch", mock_cur)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].name, "Alice")


if __name__ == "__main__":
    unittest.main()
