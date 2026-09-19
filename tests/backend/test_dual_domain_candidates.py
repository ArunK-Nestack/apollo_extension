"""Self-check: dual-domain candidate ordering (no DB required)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api import ApolloContact, _domain_lookup_candidates, canonical_lookup_domain, extract_root_domain


def _c(column="", website="", email=""):
    return ApolloContact(
        key="k",
        name="Test User",
        job_title="Director",
        company="Acme Inc",
        company_domain=column,
        website_link=website,
        email=email,
    )


def test_column_first():
    cands = _domain_lookup_candidates(_c("ford.com", "https://careers.ford.com"))
    assert cands == [("column", "ford.com")], cands


def test_column_then_website_when_differ():
    cands = _domain_lookup_candidates(
        _c("newbrand.io", "https://mullinaxfordkiss.com")
    )
    assert cands == [("column", "newbrand.io"), ("website", "mullinaxfordkiss.com")], cands


def test_website_only_when_column_empty():
    cands = _domain_lookup_candidates(
        _c("", "https://bancroftcontracting.com")
    )
    assert cands == [("website", "bancroftcontracting.com")], cands


def test_bancroft_row():
    column = extract_root_domain("bancroftcontracting.com")
    website = extract_root_domain("https://bancroftcontracting.com")
    assert column == website == "bancroftcontracting.com"
    cands = _domain_lookup_candidates(
        _c("bancroftcontracting.com", "https://bancroftcontracting.com")
    )
    assert len(cands) == 1 and cands[0][1] == "bancroftcontracting.com"


def test_email_third_candidate():
    cands = _domain_lookup_candidates(
        _c("newbrand.io", "https://branch.example.com", email="person@ford.com")
    )
    assert cands == [
        ("column", "newbrand.io"),
        ("website", "example.com"),
        ("email", "ford.com"),
    ]


def test_canonical_never_invents():
    assert canonical_lookup_domain(_c("", "")) == ""
    assert canonical_lookup_domain(_c("", "", email="")) == ""


if __name__ == "__main__":
    test_column_first()
    test_column_then_website_when_differ()
    test_website_only_when_column_empty()
    test_bancroft_row()
    test_email_third_candidate()
    test_canonical_never_invents()
    print("OK: dual-domain candidate tests passed")
