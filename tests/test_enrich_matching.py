import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from backend.enrich_api import (
    match_enrich,
    EnrichMatchRequest,
    EnrichContact,
    _enrich_batch_seen_companies,
    _enrich_batch_seen_lock,
)


def test_enrich_matching_existing_domain_in_crm():
    """Test that a lead whose company domain already exists in CRM (e.g. deloitte.com) is marked EXISTING and IGNORED."""
    req = EnrichMatchRequest(
        contacts=[
            EnrichContact(
                key="lead_deloitte",
                name="John Existing",
                job_title="VP of Engineering",
                company="Deloitte",
                domain="deloitte.com",
            )
        ],
        batch="test_batch_existing",
        collect_mode="contacts",
        auto_save=False,
    )

    res = match_enrich(req)
    assert res["total"] == 1
    assert res["existing_count"] == 1
    assert res["required_count"] == 0
    assert res["ignored_count"] == 1

    lead_res = res["results"]["lead_deloitte"]
    assert lead_res["exists"] is True
    assert lead_res["required"] is False
    assert lead_res["ignored"] is True
    assert lead_res["status"] == "existing_in_db"
    assert "already exists in CRM" in lead_res["reason"] or "already exists in database" in lead_res["reason"] or "L1" in lead_res["reason"]


def test_enrich_matching_net_new_domain_qualified():
    """Test that a lead with a net-new domain not in CRM and an approved decision maker title is marked TARGET (required: True)."""
    with _enrich_batch_seen_lock:
        _enrich_batch_seen_companies.clear()

    req = EnrichMatchRequest(
        contacts=[
            EnrichContact(
                key="lead_net_new_target",
                name="David Sterling",
                job_title="Vice President of Engineering",
                company="HyperNewAIStartup999",
                domain="hypernewaistartup999.io",
            )
        ],
        batch="test_batch_new",
        collect_mode="contacts",
        auto_save=False,
    )

    res = match_enrich(req)
    assert res["total"] == 1
    assert res["required_count"] == 1
    assert res["ignored_count"] == 0

    lead_res = res["results"]["lead_net_new_target"]
    assert lead_res["exists"] is False
    assert lead_res["required"] is True
    assert lead_res["ignored"] is False
    assert lead_res["status"] == "qualified"
    assert lead_res["matched_domain"] == "hypernewaistartup999.io"


def test_enrich_matching_net_new_domain_disqualified_title():
    """Test that a lead with a net-new domain but an excluded intern title is rejected by guardrails."""
    with _enrich_batch_seen_lock:
        _enrich_batch_seen_companies.clear()

    req = EnrichMatchRequest(
        contacts=[
            EnrichContact(
                key="lead_intern",
                name="Alice Walker",
                job_title="Summer Engineering Intern",
                company="HyperNewAIStartup888",
                domain="hypernewaistartup888.io",
            )
        ],
        batch="test_batch_intern",
        collect_mode="contacts",
        auto_save=False,
    )

    res = match_enrich(req)
    assert res["total"] == 1
    assert res["required_count"] == 0
    assert res["ignored_count"] == 1

    lead_res = res["results"]["lead_intern"]
    assert lead_res["required"] is False
    assert lead_res["ignored"] is True
    assert lead_res["status"] == "disqualified_title"


def test_enrich_matching_demographic_filter():
    """Test that a pure Indian subcontinent origin name is skipped by demographic guardrails."""
    with _enrich_batch_seen_lock:
        _enrich_batch_seen_companies.clear()

    req = EnrichMatchRequest(
        contacts=[
            EnrichContact(
                key="lead_indian",
                name="Rahul Sharma",
                job_title="Vice President of Engineering",
                company="HyperNewAIStartup777",
                domain="hypernewaistartup777.io",
            )
        ],
        batch="test_batch_demographic",
        collect_mode="contacts",
        auto_save=False,
    )

    res = match_enrich(req)
    assert res["total"] == 1
    assert res["required_count"] == 0
    assert res["ignored_count"] == 1

    lead_res = res["results"]["lead_indian"]
    assert lead_res["required"] is False
    assert lead_res["ignored"] is True
    assert lead_res["status"] == "indian_name_disqualified"


def test_enrich_matching_domain_uniqueness_seniority_election():
    """
    Test domain uniqueness:
    When multiple leads exist from the same net-new company domain,
    only 1 lead is selected, and the higher-ranking seniority score wins!
    """
    with _enrich_batch_seen_lock:
        _enrich_batch_seen_companies.clear()

    req = EnrichMatchRequest(
        contacts=[
            EnrichContact(
                key="lead_mgr",
                name="Bob Manager",
                job_title="Product Manager",  # Seniority 40
                company="UniqueStartupDomain123",
                domain="uniquestartupdomain123.com",
            ),
            EnrichContact(
                key="lead_cto",
                name="Carol CTO",
                job_title="Chief Technology Officer",  # Seniority 100
                company="UniqueStartupDomain123",
                domain="uniquestartupdomain123.com",
            ),
        ],
        batch="test_batch_uniqueness",
        collect_mode="contacts",
        auto_save=False,
    )

    res = match_enrich(req)
    assert res["total"] == 2
    # Only 1 unique lead per company domain should be admitted as TARGET!
    assert res["required_count"] == 1
    assert res["ignored_count"] == 1

    # Carol CTO should have outranked Bob Manager
    cto_res = res["results"]["lead_cto"]
    mgr_res = res["results"]["lead_mgr"]

    assert cto_res["required"] is True
    assert cto_res["ignored"] is False
    assert cto_res["status"] == "qualified"

    assert mgr_res["required"] is False
    assert mgr_res["ignored"] is True
    assert mgr_res["status"] == "company_limit_reached"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

