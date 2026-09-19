import os
import sys
import json
import tempfile
import csv
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FS_AGENT_DIR = PROJECT_ROOT / "freshsales_agent"

MV_DIR = PROJECT_ROOT / "millionverifier_agent_step1"
if str(MV_DIR) in sys.path:
    sys.path.remove(str(MV_DIR))

# Disambiguate 'app' namespace when pytest collects both millionverifier and freshsales
if "app" in sys.modules:
    for k in list(sys.modules.keys()):
        if k == "app" or k.startswith("app."):
            del sys.modules[k]

if str(FS_AGENT_DIR) in sys.path:
    sys.path.remove(str(FS_AGENT_DIR))
sys.path.insert(0, str(FS_AGENT_DIR))

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(1, str(PROJECT_ROOT))

from scripts.freshsales_bridge import (
    get_freshsales_ledger,
    save_freshsales_ledger,
    record_batch_sync,
    discover_verified_good_batches,
    sync_good_file_to_freshsales,
)
from app.services.upsert_engine import resolve_delta_update
from app.services.tld_filter import filter_by_tld, is_blocked_tld
from app.services.input_processor import parse_and_dedupe_file


from app.models import ContactPayload
from app.config import Settings


def test_tld_filter_rules():
    """Verify 33 country TLD blocklist correctly filters foreign domains."""
    # Excluded TLDs (from the 33 European / North American country list)
    assert is_blocked_tld("user@company.co.uk") is True
    assert is_blocked_tld("finance@corp.de") is True
    assert is_blocked_tld("sales@venture.ca") is True
    assert is_blocked_tld("admin@domain.es") is True
    assert is_blocked_tld("info@agency.fr") is True

    # Allowed global / US domains
    assert is_blocked_tld("ceo@company.com") is False
    assert is_blocked_tld("founder@startup.io") is False
    assert is_blocked_tld("engineer@tech.ai") is False
    assert is_blocked_tld("contact@nonprofit.org") is False
    assert is_blocked_tld("network@isp.net") is False


def test_tag_merging_preserves_existing_tags():
    """Verify custom tag is appended without overwriting existing Freshsales tags."""
    crm_contact = {
        "id": 12345,
        "first_name": "Sarah",
        "last_name": "Connor",
        "email": "sarah@cyberdyne.com",
        "job_title": "Security Specialist",
        "mobile_number": "555-0199",
        "tags": ["vip-client", "q1-prospect"],
    }
    incoming = ContactPayload(
        first_name="Sarah",
        last_name="Connor",
        email="sarah@cyberdyne.com",
        job_title="Chief Security Officer",  # Different title
        mobile_number="555-9999",            # Different phone
        work_number="123-4567",              # New field
        custom_fields={},
    )

    delta = resolve_delta_update(
        incoming=incoming,
        existing_crm_record=crm_contact,
        tag="vraghavan-millionverifier-sept",
    )

    # Existing tags must be preserved AND new tag merged in
    assert "vip-client" in delta["tags"]
    assert "q1-prospect" in delta["tags"]
    assert "vraghavan-millionverifier-sept" in delta["tags"]
    assert len(delta["tags"]) == 3

    # Non-overwrite verification: existing fields must NOT be in delta
    assert "job_title" not in delta
    assert "mobile_number" not in delta

    # Net-new non-empty fields CAN be added
    assert delta.get("work_number") == "123-4567"


def test_ledger_persistence():
    """Verify sync ledger correctly records and retrieves batch sync history."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_ledger = Path(tmp_dir) / "test_ledger.json"

        with patch("scripts.freshsales_bridge.LEDGER_PATH", tmp_ledger):
            # Initially empty
            ledger = get_freshsales_ledger()
            assert ledger == {}

            # Record batch sync
            record_batch_sync(
                file_stem="dental_batch_good",
                tag="vraghavan-dental",
                total_leads=150,
                created=120,
                updated=30,
                tld_blocked=5,
                audit_file="reports/audit.csv",
                file_path="/path/to/dental_batch_good.csv",
            )

            # Reload
            reloaded = get_freshsales_ledger()
            assert "dental_batch_good" in reloaded
            entry = reloaded["dental_batch_good"]
            assert entry["tag"] == "vraghavan-dental"
            assert entry["total_leads"] == 150
            assert entry["created"] == 120
            assert entry["updated"] == 30
            assert entry["tld_blocked"] == 5


def test_discover_verified_good_batches():
    """Verify discovery finds Good files and marks UNPROCESSED vs SYNCED."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        good_file1 = tmp_path / "vraghavan_batch1_verified_good_20260920.csv"
        good_file2 = tmp_path / "vraghavan_batch2_verified_good_20260920.csv"

        # Create dummy CSV files
        for f in [good_file1, good_file2]:
            with open(f, "w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                writer.writerow(["First Name", "Last Name", "Email"])
                writer.writerow(["John", "Doe", "john@example.com"])

        # Mock ledger where batch1 is synced, batch2 is not
        mock_ledger = {
            good_file1.stem.lower(): {
                "tag": "vraghavan-leads",
                "synced_at": "2026-09-20 12:00:00",
                "created": 1,
                "updated": 0,
            }
        }

        with patch("scripts.freshsales_bridge.get_freshsales_ledger", return_value=mock_ledger):
            batches = discover_verified_good_batches(search_dirs=[tmp_path])

            assert len(batches) == 2
            b1 = next(b for b in batches if b["file_path"] == good_file1)
            b2 = next(b for b in batches if b["file_path"] == good_file2)

            assert b1["is_synced"] is True
            assert b1["sync_tag"] == "vraghavan-leads"
            assert b2["is_synced"] is False
            assert b2["sync_tag"] == ""


def test_sync_good_file_pipeline_mocked():
    """Verify end-to-end sync execution with mocked CRM API."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        good_file = tmp_path / "vraghavan_test_good.csv"

        # Sample data with US and foreign domains
        rows = [
            ["First Name", "Last Name", "Email", "Title", "Company"],
            ["Alice", "Smith", "alice@validtech.com", "CEO", "Valid Tech"],
            ["Bob", "Jones", "bob@ukfirm.co.uk", "CTO", "UK Firm"],  # Should be blocked by TLD filter
            ["Carol", "White", "carol@cloudcorp.io", "VP Sales", "Cloud Corp"],
        ]
        with open(good_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows)

        reports_dir = tmp_path / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        # Mock FreshsalesClient to avoid live API calls in unit test
        with patch("app.services.batch_processor.FreshsalesClient") as MockClient, \
             patch("time.sleep"), \
             patch.object(Settings, "require_api_key", return_value=None), \
             patch("scripts.freshsales_bridge.record_batch_sync") as mock_record:

            mock_client_instance = MagicMock()
            # Neither email exists in CRM initially
            mock_client_instance.batch_lookup_emails.return_value = {}
            # Dispatch bulk upsert
            mock_client_instance.bulk_upsert_contacts.return_value = "job_123"
            mock_client_instance.get_job_status.return_value = {
                "status": "completed",
            }
            MockClient.return_value = mock_client_instance

            res = sync_good_file_to_freshsales(
                good_csv_path=good_file,
                custom_tag="vraghavan-verified-q3",
                reports_dir=reports_dir,
            )

            assert res["status"] == "completed"
            assert res["tld_blocked"] == 1  # ukfirm.co.uk blocked
            assert res["created"] == 2      # Alice and Carol created
            assert res["tag"] == "vraghavan-verified-q3"

            # Check that record_batch_sync was called
            assert mock_record.called
            call_kwargs = mock_record.call_args.kwargs
            assert call_kwargs["file_stem"] == good_file.stem
            assert call_kwargs["tag"] == "vraghavan-verified-q3"
