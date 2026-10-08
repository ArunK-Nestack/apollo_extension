import os
import sys
import tempfile
import csv
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Ensure workspace root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MV_DIR = PROJECT_ROOT / "millionverifier_agent_step1"

FS_AGENT_DIR = PROJECT_ROOT / "freshsales_agent"
if str(FS_AGENT_DIR) in sys.path:
    sys.path.remove(str(FS_AGENT_DIR))

if "app" in sys.modules:
    for k in list(sys.modules.keys()):
        if k == "app" or k.startswith("app."):
            del sys.modules[k]

if str(MV_DIR) in sys.path:
    sys.path.remove(str(MV_DIR))
sys.path.insert(0, str(MV_DIR))

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(1, str(PROJECT_ROOT))

from app.models import VerificationJob, CategorizationSummary
from app.services.verifier import VerificationService
from app.services.categorizer import categorize_results, classify_row
from scripts.send_to_millionverifier import ensure_millionverifier_api_key


def test_classify_row():
    # Test quality mapping
    assert classify_row({"quality": "good"}) == "good"
    assert classify_row({"quality": "bad"}) == "bad"
    assert classify_row({"quality": "risky"}) == "risky"

    # Test result mapping
    assert classify_row({"result": "ok"}) == "good"
    assert classify_row({"result": "invalid"}) == "bad"
    assert classify_row({"result": "catch_all"}) == "risky"
    assert classify_row({"result": "disposable"}) == "bad"


def test_categorize_preserves_all_75_columns():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Mock downloaded verified CSV from MillionVerifier
        verified_csv = tmp_path / "downloaded_verified.csv"
        with open(verified_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["email", "quality", "result", "subresult"])
            writer.writerow(["ceo@companyalpha.com", "good", "ok", ""])
            writer.writerow(["bad@companybeta.com", "bad", "invalid", "mailbox_not_found"])
            writer.writerow(["catchall@companygamma.com", "risky", "catch_all", ""])

        # 75-column original row data
        original_headers = [
            "First Name", "Last Name", "Title", "Company", "Company Name for Emails",
            "Email", "Email Status", "Email Confidence", "Seniority", "Departments",
            "Account", "Hierarchy", "Employee 2", "Industry 2"
        ]
        email_to_orig = {
            "ceo@companyalpha.com": {
                "First Name": "Alice",
                "Last Name": "Smith",
                "Title": "CEO",
                "Company": "Company Alpha",
                "Company Name for Emails": "Company Alpha",
                "Email": "ceo@companyalpha.com",
                "Email Status": "verified",
                "Email Confidence": "100",
                "Seniority": "C-Level",
                "Departments": "Executive",
                "Account": "companyalpha.com",
                "Hierarchy": "Management",
                "Employee 2": "50",
                "Industry 2": "Software",
            },
            "bad@companybeta.com": {
                "First Name": "Bob",
                "Last Name": "Jones",
                "Title": "Manager",
                "Company": "Company Beta",
                "Company Name for Emails": "Company Beta",
                "Email": "bad@companybeta.com",
                "Email Status": "verified",
                "Email Confidence": "90",
                "Seniority": "Manager",
                "Departments": "Sales",
                "Account": "companybeta.com",
                "Hierarchy": "Staff",
                "Employee 2": "20",
                "Industry 2": "Retail",
            },
            "catchall@companygamma.com": {
                "First Name": "Charlie",
                "Last Name": "Brown",
                "Title": "VP",
                "Company": "Company Gamma",
                "Company Name for Emails": "Company Gamma",
                "Email": "catchall@companygamma.com",
                "Email Status": "verified",
                "Email Confidence": "85",
                "Seniority": "VP",
                "Departments": "Product",
                "Account": "companygamma.com",
                "Hierarchy": "Executive",
                "Employee 2": "100",
                "Industry 2": "Finance",
            },
        }

        cat_summary = categorize_results(
            csv_path=verified_csv,
            output_dir=tmp_path / "output",
            base_filename="test_run",
            total_input_count=3,
            email_to_original_row=email_to_orig,
            original_headers=original_headers,
        )

        assert cat_summary.good_count == 1
        assert cat_summary.bad_count == 1
        assert cat_summary.risky_count == 1

        # Check that good file preserved all original columns
        df_good = pd.read_csv(cat_summary.good_file)
        assert len(df_good) == 1
        assert df_good.iloc[0]["Email"] == "ceo@companyalpha.com"
        assert df_good.iloc[0]["Company"] == "Company Alpha"
        assert df_good.iloc[0]["Title"] == "CEO"
        assert df_good.iloc[0]["Account"] == "companyalpha.com"
        # Verification columns should be present
        assert df_good.iloc[0]["quality"] == "good"
        assert df_good.iloc[0]["result"] == "ok"


def test_verification_service_workflow():
    mock_client = MagicMock()
    mock_job_initial = VerificationJob.from_api({
        "file_id": "12345",
        "status": "in_progress",
        "total_rows": 10,
        "verified": 0,
        "percent": 0,
    })
    mock_job_finished = VerificationJob.from_api({
        "file_id": "12345",
        "status": "finished",
        "total_rows": 10,
        "verified": 10,
        "percent": 100,
    })

    mock_client.upload_csv.return_value = mock_job_initial
    mock_client.get_file_info.return_value = mock_job_finished

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        clean_file = tmp_path / "clean.csv"
        clean_file.write_text("email\ntest@example.com\n")

        download_file = tmp_path / "verified_clean.csv"
        download_file.write_text("email,quality,result\ntest@example.com,good,ok\n")
        mock_client.download_results.return_value = download_file

        service = VerificationService(client=mock_client)
        service.poll_interval = 0.01

        job, downloaded_path = service.verify_file(clean_file, download_dir=tmp_path)

        assert job.status == "finished"
        assert str(job.file_id) == "12345"
        assert downloaded_path.exists()
        mock_client.upload_csv.assert_called_once_with(clean_file)
        mock_client.get_file_info.assert_called_once_with("12345")
