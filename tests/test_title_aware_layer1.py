from types import SimpleNamespace

from backend import api
from scripts import lead_guardrails


def _contact(title: str) -> api.ApolloContact:
    return api.ApolloContact(
        key="lead",
        name="New Person",
        job_title=title,
        company="Acme Corp",
        company_domain="acme.com",
    )


def test_existing_domain_allows_novel_company_title(monkeypatch):
    monkeypatch.setattr(api, "_DNS_AVAILABLE", False)
    monkeypatch.setattr(api, "_domain_trie", SimpleNamespace(loaded=False))

    hit = api._try_domain_layers(
        _contact("Director of Operations"),
        "acme.com",
        api.normalize_text("New Person"),
        matched_records={},
        matched_domains={"acme.com"},
        matched_titles={"acme.com": {api.get_canonical_title_key("Chief Executive Officer")}},
        title_aware_exact_domain=True,
    )

    assert hit is None


def test_existing_domain_blocks_equivalent_company_title(monkeypatch):
    monkeypatch.setattr(api, "_DNS_AVAILABLE", False)
    monkeypatch.setattr(api, "_domain_trie", SimpleNamespace(loaded=False))

    hit = api._try_domain_layers(
        _contact("Vice President of Sales"),
        "acme.com",
        api.normalize_text("New Person"),
        matched_records={},
        matched_domains={"acme.com"},
        matched_titles={"acme.com": {api.get_canonical_title_key("VP Sales")}},
        title_aware_exact_domain=True,
    )

    assert hit["guardrail_status"] == "duplicate_title_for_company"
    assert hit["required"] is False
    assert "[L1]" in hit["guardrail_reason"]


def test_existing_person_remains_blocked_with_novel_title(monkeypatch):
    monkeypatch.setattr(api, "_DNS_AVAILABLE", False)
    monkeypatch.setattr(api, "_domain_trie", SimpleNamespace(loaded=False))
    norm_name = api.normalize_text("New Person")

    hit = api._try_domain_layers(
        _contact("Director of Operations"),
        "acme.com",
        norm_name,
        matched_records={(norm_name, "acme.com"): "New Person"},
        matched_domains={"acme.com"},
        matched_titles={"acme.com": {api.get_canonical_title_key("Chief Executive Officer")}},
        title_aware_exact_domain=True,
    )

    assert hit["guardrail_status"] == "contact_already_in_db"


def test_previously_enriched_apollo_id_blocks_even_when_company_changed(monkeypatch):
    monkeypatch.setattr(api, "_DNS_AVAILABLE", False)
    monkeypatch.setattr(api, "_domain_trie", SimpleNamespace(loaded=False))
    contact = _contact("Director of Operations").model_copy(
        update={"apollo_id": "person-123", "company_domain": "new-company.com"}
    )

    hit = api._try_domain_layers(
        contact,
        "new-company.com",
        api.normalize_text(contact.name),
        matched_records={},
        matched_domains=set(),
        matched_apollo_ids={"person-123"},
        title_aware_exact_domain=True,
    )

    assert hit["guardrail_status"] == "apollo_id_already_enriched"
    assert hit["required"] is False
    assert "[L1]" in hit["guardrail_reason"]


def test_guardrails_use_legacy_layer1_only_when_company_limit_is_one(monkeypatch):
    lead = {
        "id": 1,
        "name": "New Person",
        "job_title": "Director of Operations",
        "company": "Acme Corp",
        "company_domain": "acme.com",
    }
    observed_modes = []

    def fake_resolve(contacts, connection=None):
        return ({contact.key: "acme.com" for contact in contacts}, {})

    def fake_check(contacts, primary_domains, **kwargs):
        title_aware = kwargs["title_aware_exact_domain"]
        observed_modes.append(title_aware)
        if title_aware:
            return {}
        return {
            contacts[0].key: {
                "guardrail_status": "domain_already_in_db",
                "guardrail_reason": "[L1] Exact company domain already exists",
                "matched_domain": "acme.com",
            }
        }

    monkeypatch.setattr(lead_guardrails.api, "resolve_company_domains", fake_resolve)
    monkeypatch.setattr(lead_guardrails.api, "check_person_and_domains_in_crm_batch", fake_check)

    legacy_result, legacy_metrics = lead_guardrails.apply_4_layer_guardrails(
        [lead], "test-batch", conn=object(), verbose=False, max_leads_per_domain=1
    )
    multi_result, multi_metrics = lead_guardrails.apply_4_layer_guardrails(
        [lead], "test-batch", conn=object(), verbose=False, max_leads_per_domain=3
    )

    assert observed_modes == [False, True]
    assert legacy_result == []
    assert legacy_metrics["l1_title_aware"] is False
    assert multi_result == [lead]
    assert multi_metrics["l1_title_aware"] is True
