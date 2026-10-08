import os
import pytest
from unittest.mock import MagicMock, patch
from scripts.apollo_saved_search_inspector import (
    probe_search_metrics,
    fetch_account_total_saved_contacts,
    fetch_account_labels,
    format_filters_summary,
    mask_key,
)


def test_mask_key():
    assert mask_key("1234567890abcdef") == "123...cdef"
    assert mask_key("") == "[NOT SET]"
    assert mask_key("short") == "...ort"


def test_format_filters_summary():
    filters = {
        "person_titles": ["CEO", "Director", "VP"],
        "person_locations": ["United States", "Canada"],
        "person_seniorities": ["c_suite", "owner"],
        "organization_num_employees_ranges": ["1,10", "11,50"],
        "q_organization_keyword_tags": ["SaaS", "AI", "Cloud"],
        "q_keywords": "software",
    }
    summary = format_filters_summary(filters)
    assert "Target Titles" in summary
    assert "Target Locations" in summary
    assert "Seniority Levels" in summary
    assert "Employee Headcount" in summary
    assert "Industry Keywords" in summary
    assert "software" in summary


@patch("requests.post")
def test_probe_search_metrics(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Simulate Total=100, New=80, Saved=20
    mock_resp.json.side_effect = [
        {"total_entries": 100},
        {"total_entries": 80},
        {"total_entries": 20},
    ]
    mock_post.return_value = mock_resp

    metrics = probe_search_metrics("test_key", {"person_titles": ["Director"]})
    assert metrics["total"] == 100
    assert metrics["new"] == 80
    assert metrics["saved"] == 20


@patch("requests.post")
def test_fetch_account_total_saved_contacts(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"pagination": {"total_entries": 3132122}}
    mock_post.return_value = mock_resp

    count = fetch_account_total_saved_contacts("test_key")
    assert count == 3132122


@patch("requests.get")
def test_fetch_account_labels(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"id": "lbl1", "name": "List A", "modality": "contacts", "cached_count": 500},
        {"id": "lbl2", "name": "Co List", "modality": "accounts", "cached_count": 100},
    ]
    mock_get.return_value = mock_resp

    labels = fetch_account_labels("test_key")
    assert len(labels) == 1
    assert labels[0]["name"] == "List A"


def test_export_leads_to_75_column_csv(tmp_path, monkeypatch):
    import csv
    from scripts.apollo_saved_search_inspector import export_leads_to_csv

    monkeypatch.setattr("scripts.apollo_saved_search_inspector.EXPORTS_DIR", str(tmp_path))

    sample_lead = {
        "id": "person_123",
        "first_name": "Alice",
        "last_name": "Smith",
        "title": "VP Engineering",
        "email": "alice@acme.com",
        "email_status": "verified",
        "organization": {
            "name": "Acme Corp",
            "primary_domain": "acme.com",
            "industry": "Software",
            "estimated_num_employees": 150,
        },
    }

    out_file = export_leads_to_csv([sample_lead], "test_export", "user@apollo.io")
    assert os.path.exists(out_file)

    with open(out_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        row = next(reader)

    assert len(header) == 75
    assert header[0] == "First Name"
    assert header[16] == "Work Direct Phone"
    assert row[0] == "Alice"
    assert row[1] == "Smith"
    assert row[2] == "VP Engineering"
    assert row[3] == "Acme Corp"
    assert row[5] == "alice@acme.com"

