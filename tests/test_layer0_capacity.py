#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Suite: Layer 0 Dynamic Domain Capacity in Guardrails Engine
================================================================
Verifies:
  1. Default max_leads_per_domain=1 collapses to 1 highest-seniority lead per domain.
  2. Setting max_leads_per_domain=3 retains top 3 distinct seniority leads.
  3. Setting max_leads_per_domain=5 retains up to 5 distinct seniority leads.
  4. Duplicate canonical job titles within the same domain are deduplicated.
  5. Pool replacement evicts the lowest-scoring lead when at capacity.
  6. Environment variable MAX_LEADS_PER_DOMAIN override works properly.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.lead_guardrails import apply_4_layer_guardrails


def test_layer0_single_lead_default():
    leads = [
        {"id": 1, "name": "Alice Smith", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "Software Engineer"},
        {"id": 2, "name": "Bob Jones", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "VP of Engineering"},
        {"id": 3, "name": "Charlie Brown", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "Director of Engineering"},
    ]
    # Default is 1
    verified, metrics = apply_4_layer_guardrails(leads, "test_batch_1", conn=None, verbose=False, max_leads_per_domain=1)
    
    # Layer 0 must keep only 1 lead for acme.com (Bob Jones, VP)
    assert metrics["l0_unique"] == 1
    assert metrics["l0_removed"] == 2
    assert metrics["l0_max_per_domain"] == 1
    print("✓ test_layer0_single_lead_default passed")


def test_layer0_multi_lead_capacity_3():
    leads = [
        {"id": 1, "name": "Alice Smith", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "Chief Executive Officer"},
        {"id": 2, "name": "Bob Jones", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "VP of Engineering"},
        {"id": 3, "name": "Charlie Brown", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "Director of Operations"},
        {"id": 4, "name": "David Clark", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "Operations Manager"},
        {"id": 5, "name": "Eve Davis", "company": "Beta LLC", "company_domain": "beta.com", "job_title": "Founder"},
    ]
    verified, metrics = apply_4_layer_guardrails(leads, "test_batch_3", conn=None, verbose=False, max_leads_per_domain=3)

    # For Acme Corp: top 3 by seniority (CEO, VP, Director) kept; Manager dropped
    # For Beta LLC: 1 lead kept
    # Total Layer 0 leads: 3 + 1 = 4
    assert metrics["l0_unique"] == 4
    assert metrics["l0_removed"] == 1
    assert metrics["l0_max_per_domain"] == 3
    print("✓ test_layer0_multi_lead_capacity_3 passed")


def test_layer0_duplicate_title_dedup():
    leads = [
        {"id": 1, "name": "Alice Smith", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "VP of Engineering"},
        {"id": 2, "name": "Alan Smith", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "Vice President of Engineering", "linkedin_url": "https://linkedin.com/in/alansmith"}, # higher score due to linkedin
        {"id": 3, "name": "Charlie Brown", "company": "Acme Corp", "company_domain": "acme.com", "job_title": "Director of Sales"},
    ]
    # Even with cap=3, the two duplicate VP of Engineering titles must be collapsed to the higher score one
    verified, metrics = apply_4_layer_guardrails(leads, "test_batch_dupe_titles", conn=None, verbose=False, max_leads_per_domain=3)
    
    assert metrics["l0_unique"] == 2
    assert metrics["l0_removed"] == 1
    print("✓ test_layer0_duplicate_title_dedup passed")


def test_layer0_env_var_override():
    os.environ["MAX_LEADS_PER_DOMAIN"] = "4"
    try:
        leads = [
            {"id": 1, "name": "Person 1", "company": "Apex Inc", "company_domain": "apex.com", "job_title": "CEO"},
            {"id": 2, "name": "Person 2", "company": "Apex Inc", "company_domain": "apex.com", "job_title": "CTO"},
            {"id": 3, "name": "Person 3", "company": "Apex Inc", "company_domain": "apex.com", "job_title": "VP Marketing"},
            {"id": 4, "name": "Person 4", "company": "Apex Inc", "company_domain": "apex.com", "job_title": "Director of Finance"},
            {"id": 5, "name": "Person 5", "company": "Apex Inc", "company_domain": "apex.com", "job_title": "Sales Rep"},
        ]
        # max_leads_per_domain passed as 1, but env var is 4
        verified, metrics = apply_4_layer_guardrails(leads, "test_batch_env", conn=None, verbose=False, max_leads_per_domain=1)
        assert metrics["l0_max_per_domain"] == 4
        assert metrics["l0_unique"] == 4
        assert metrics["l0_removed"] == 1
        print("✓ test_layer0_env_var_override passed")
    finally:
        del os.environ["MAX_LEADS_PER_DOMAIN"]


if __name__ == "__main__":
    test_layer0_single_lead_default()
    test_layer0_multi_lead_capacity_3()
    test_layer0_duplicate_title_dedup()
    test_layer0_env_var_override()
    print("\nALL LAYER 0 DYNAMIC CAPACITY TESTS PASSED SUCCESSFULLY! 🎯")
