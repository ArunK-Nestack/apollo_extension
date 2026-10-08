#!/usr/bin/env python3
"""
Unit Tests for High-Accuracy Domain Resolver Engine
===================================================
Validates:
1. Domain string cleaning (strips protocols, www, paths).
2. Live DNS checking on known active vs invalid domains.
3. Word-boundary Clearbit autocomplete matching.
4. Multi-tier resolution (returns valid domains without Apollo credits).
5. Thread-safe batch resolution.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.domain_resolver_engine import (
    clean_domain,
    _has_active_dns,
    resolve_domain_multi_tier,
    batch_resolve_domains_high_accuracy,
)
from backend.api import get_connection


class TestDomainResolverEngine(unittest.TestCase):

    def test_clean_domain(self):
        self.assertEqual(clean_domain("https://www.google.com/search?q=test"), "google.com")
        self.assertEqual(clean_domain("http://stripe.com:443/"), "stripe.com")
        self.assertEqual(clean_domain("www.domain.co.uk"), "domain.co.uk")
        self.assertEqual(clean_domain(""), "")

    def test_has_active_dns(self):
        self.assertTrue(_has_active_dns("google.com"))
        self.assertTrue(_has_active_dns("microsoft.com"))
        self.assertFalse(_has_active_dns("thisiscompletelyfakedojkdldkfjf.com"))

    def test_resolve_single_company(self):
        res = resolve_domain_multi_tier("Stripe")
        self.assertIn("company_domain", res)
        self.assertEqual(res["company_domain"], "stripe.com")
        self.assertEqual(res["website_link"], "https://stripe.com")
        self.assertTrue(bool(res.get("source")))

    def test_resolve_batch_companies(self):
        comps = ["Stripe", "Liquid AI", "Microsoft"]
        results = batch_resolve_domains_high_accuracy(comps, max_workers=3)
        self.assertIn("stripe", results)
        self.assertIn("liquid ai", results)
        self.assertIn("microsoft", results)
        self.assertEqual(results["stripe"]["company_domain"], "stripe.com")
        self.assertEqual(results["liquid ai"]["company_domain"], "liquid.ai")

    def test_mysql_cache_integration(self):
        with get_connection() as conn:
            res = resolve_domain_multi_tier("Stripe", conn=conn)
            self.assertEqual(res["company_domain"], "stripe.com")


if __name__ == "__main__":
    unittest.main()
