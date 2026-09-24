import os
import sys
import tempfile
from pathlib import Path

# Add freshsales_agent root to sys.path
agent_root = Path(__file__).resolve().parent
if str(agent_root) not in sys.path:
    sys.path.insert(0, str(agent_root))

from app.database.db import get_db, init_db
from app.database.models import FileRunMetric
from app.models import ContactPayload, FileProcessingResult
from app.services.field_mapper import map_row_to_contact_payload
from app.services.input_processor import parse_and_dedupe_file
from app.services.reporter import generate_audit_csv_report, save_run_metrics_to_db
from app.services.tld_filter import filter_by_tld, is_blocked_tld
from app.services.upsert_engine import resolve_delta_update


def test_tld_filtering():
    # Blocked TLDs
    assert is_blocked_tld("user@company.fr") is True
    assert is_blocked_tld("john@sales.co.uk") is True
    assert is_blocked_tld("sarah@enterprise.de") is True
    assert is_blocked_tld("alex@corp.ca") is True
    assert is_blocked_tld("test@startup.ee") is True

    # Allowed TLDs
    assert is_blocked_tld("user@company.com") is False
    assert is_blocked_tld("admin@domain.io") is False
    assert is_blocked_tld("contact@nestack.net") is False
    assert is_blocked_tld("info@tech.org") is False

    sample_records = [
        {"email": "good1@company.com", "name": "Alice"},
        {"email": "blocked1@agency.fr", "name": "Bob"},
        {"email": "good2@nestack.io", "name": "Charlie"},
        {"email": "blocked2@firm.co.uk", "name": "David"},
    ]
    valid, excluded = filter_by_tld(sample_records)
    assert len(valid) == 2
    assert len(excluded) == 2
    assert valid[0]["email"] == "good1@company.com"
    assert excluded[0]["email"] == "blocked1@agency.fr"
    print("test_tld_filtering passed.")


def test_field_mapping_and_delta():
    row = {
        "First Name": "Vijay",
        "Last Name": "Raghavan",
        "Title": "CEO",
        "Company Name": "Nestack",
        "Email": "vijay@nestack.com",
        "Work Direct Phone": "1234567890",
        "quality": "good",
        "result": "ok",
        "Contact Owner": "admin@nestack.com",
        "Company Linkedin Url": "https://linkedin.com/company/nestack",
    }

    tag = "apollo-2026-08-23-test_file"
    payload = map_row_to_contact_payload(row, tag=tag)

    assert payload.email == "vijay@nestack.com"
    assert payload.first_name == "Vijay"
    assert payload.last_name == "Raghavan"
    assert payload.job_title == "CEO"
    assert payload.custom_fields["cf_million_quality"] == "good"
    assert payload.custom_fields["cf_apollo_login_owner"] == "admin@nestack.com"
    assert payload.sales_account["custom_field"]["cf_linkedin"] == "https://linkedin.com/company/nestack"

    # Delta resolution test: existing contact has first_name and company, but empty work_number & title
    existing_crm_contact = {
        "first_name": "Vijay Original",
        "company_name": "Nestack Inc",
        "job_title": None,  # empty -> should be filled
        "work_number": "",  # empty -> should be filled
        "tags": ["old-tag"],
    }

    delta = resolve_delta_update(payload, existing_crm_contact, tag=tag)

    # first_name and company_name should NOT be in delta (protected from overwrite)
    assert "first_name" not in delta
    assert "company_name" not in delta

    # empty fields should be in delta
    assert delta["job_title"] == "CEO"
    assert delta["work_number"] == "1234567890"

    # tags must be merged
    assert "old-tag" in delta["tags"]
    assert tag in delta["tags"]
    print("test_field_mapping_and_delta passed.")


def test_input_parsing_and_dedupe():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        csv_file = tmp_path / "sample_good_input.csv"
        csv_file.write_text(
            "First Name,Last Name,Email,Company,Contact Owner\n"
            "Alice,Smith,alice@example.com,Acme,vraghavan@nestack.com\n"
            "Bob,Jones,bob@example.com,Globex,vraghavan@nestack.com\n"
            "Alice Duplicate,Smith,alice@example.com,Acme Dup,vraghavan@nestack.com\n",
            encoding="utf-8",
        )

        parsed = parse_and_dedupe_file(csv_file)
        assert parsed.total_raw_rows == 3
        assert parsed.duplicates_dropped == 1
        assert len(parsed.unique_records) == 2
        assert parsed.apollo_login_owner == "vraghavan@nestack.com"
        assert parsed.tag_applied.startswith("vraghavan-")
        print("test_input_parsing_and_dedupe passed.")


def test_excel_parsing_and_dedupe():
    import openpyxl

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        xlsx_file = tmp_path / "sample_good_input.xlsx"

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Contacts"
        ws.append(["First Name", "Last Name", "Email", "Company", "Contact Owner"])
        ws.append(["Alice", "Smith", "alice@example.com", "Acme", "vraghavan@nestack.com"])
        ws.append(["Bob", "Jones", "bob@example.com", "Globex", "vraghavan@nestack.com"])
        ws.append(["Alice Duplicate", "Smith", "alice@example.com", "Acme Dup", "vraghavan@nestack.com"])
        wb.save(xlsx_file)
        wb.close()

        parsed = parse_and_dedupe_file(xlsx_file)
        assert parsed.total_raw_rows == 3
        assert parsed.duplicates_dropped == 1
        assert len(parsed.unique_records) == 2
        assert parsed.apollo_login_owner == "vraghavan@nestack.com"
        assert parsed.tag_applied.startswith("vraghavan-")
        print("test_excel_parsing_and_dedupe passed.")


def test_database_logging():
    init_db()
    res = FileProcessingResult(
        source_filename="test_run_file.csv",
        tag_applied="apollo-2026-08-23-test",
        apollo_login_owner="owner@nestack.com",
        total_input_rows=100,
        file_duplicates_skipped=5,
        tld_filtered_count=10,
        contacts_sent_to_crm=85,
        freshly_created_count=70,
        updated_in_crm_count=15,
        failed_errors_count=0,
        status="completed",
    )

    audit_rows = [
        {"email": "c1@test.com", "action": "created", "fields_filled_count": 10, "fields_filled_names": "All", "error_reason": ""},
        {"email": "c2@test.com", "action": "updated", "fields_filled_count": 2, "fields_filled_names": "job_title", "error_reason": ""},
    ]

    run_id = save_run_metrics_to_db(res, audit_rows)
    assert run_id > 0

    db = get_db()
    metric = db.query(FileRunMetric).filter(FileRunMetric.id == run_id).first()
    assert metric is not None
    assert metric.input == 100
    assert metric.tld_block == 10
    assert metric.success_percentage == 100.0
    db.close()
    print("test_database_logging passed.")


def test_unsynced_leads_extraction():
    from scripts.freshsales_bridge import extract_unsynced_leads
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_csv = Path(tmpdir) / "test_good.csv"
        audit_csv = Path(tmpdir) / "test_good_crm_audit.csv"
        retry_csv = Path(tmpdir) / "retry.csv"

        orig_csv.write_text(
            "First Name,Last Name,Email,Title\n"
            "John,Doe,john@success.com,CEO\n"
            "Jane,Smith,jane@failed.com,CTO\n"
            "Bob,Brown,bob@blocked.fr,Director\n",
            encoding="utf-8"
        )

        audit_csv.write_text(
            "email,action,fields_filled_count,fields_filled_names,error_reason\n"
            "john@success.com,created,10,All,\n"
            "jane@failed.com,created,10,All,Freshsales HTTP 405 error\n"
            "bob@blocked.fr,excluded_tld,0,,Matched DELETE_TLDS blocklist\n",
            encoding="utf-8"
        )

        out_path, count = extract_unsynced_leads(orig_csv, audit_csv, retry_csv)
        assert count == 1
        assert out_path.is_file()
        content = out_path.read_text(encoding="utf-8")
        assert "jane@failed.com" in content
        assert "john@success.com" not in content
        assert "bob@blocked.fr" not in content
        print("test_unsynced_leads_extraction passed.")


if __name__ == "__main__":
    test_tld_filtering()
    test_field_mapping_and_delta()
    test_input_parsing_and_dedupe()
    test_excel_parsing_and_dedupe()
    test_database_logging()
    test_unsynced_leads_extraction()
    print("All Freshsales CRM agent unit tests passed successfully!")

