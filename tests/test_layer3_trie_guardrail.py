import pytest
import backend.api as api
from scripts.lead_guardrails import apply_4_layer_guardrails


def test_layer3_trie_auto_loaded_and_detects_branch():
    # Insert test root into dynamic trie entries if not present
    api.load_domain_trie()
    assert api._domain_trie.loaded is True

    # Seed a known test parent domain into the trie
    api._domain_trie.insert("mullinaxford", "mullinaxford.com")

    # An incoming lead with a dealership branch domain: mullinaxfordkissimmee.com
    contact = api.ApolloContact(
        key="lead_branch_1",
        name="John Doe",
        job_title="Sales Manager",
        company="Mullinax Ford Kissimmee",
        company_domain="mullinaxfordkissimmee.com",
    )

    # Calling _try_domain_layers without matched records or matched domains
    hit = api._try_domain_layers(
        contact,
        "mullinaxfordkissimmee.com",
        "john doe",
        matched_records={},
        matched_domains=set(),
    )

    assert hit is not None
    assert hit["guardrail_status"] == "trie_brand_stem_match"
    assert hit["matched_db_domain"] == "mullinaxford.com"
    assert "[L3]" in hit["guardrail_reason"]


def test_layer3_in_apply_4_layer_guardrails():
    # Make sure trie is loaded and seeded
    api.load_domain_trie()
    api._domain_trie.insert("mullinaxford", "mullinaxford.com")

    leads = [
        {
            "id": 99901,
            "name": "Jane Branch",
            "job_title": "General Manager",
            "company": "Mullinax Ford Kissimmee",
            "company_domain": "mullinaxfordkissimmee.com",
        },
        {
            "id": 99902,
            "name": "Bob Normal",
            "job_title": "CEO",
            "company": "Totally Unique Corp 12345",
            "company_domain": "uniqueunseencompany98765.com",
        },
    ]

    verified, metrics = apply_4_layer_guardrails(
        leads,
        batch_name="test_layer3_batch",
        conn=None,
        verbose=False,
        max_leads_per_domain=1,
    )

    assert metrics["l3_blocked"] >= 1
    l3_ids = [h["id"] for h in metrics["l3_hits"]]
    assert 99901 in l3_ids
    assert any(lead["id"] == 99902 for lead in verified)
