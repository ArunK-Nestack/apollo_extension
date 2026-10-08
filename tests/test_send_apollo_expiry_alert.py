import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.send_apollo_expiry_alert import (
    build_expiry_csv,
    filter_accounts_expiring_within,
    format_email_expiry_report,
    format_whatsapp_expiry_message,
    run_expiry_report_pipeline,
    send_whatsapp_alert,
    probe_single_account,
)


def test_format_whatsapp_expiry_message():
    now_utc = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)

    mock_accounts = [
        {
            "id": 1,
            "email": "user1@example.com",
            "name": "User One",
            "billing_end": "2026-09-20T12:00:00.000+00:00",
            "credits_avail": 4000,
            "credits_used": 1000,
            "credits_remaining": 3000,
        },
        {
            "id": 2,
            "email": "user2@example.com",
            "name": "User Two",
            "billing_end": "2026-09-24T12:00:00.000+00:00",
            "credits_avail": 2500,
            "credits_used": 0,
            "credits_remaining": 2500,
        }
    ]

    msg = format_whatsapp_expiry_message(mock_accounts, now_utc=now_utc)

    assert "APOLLO DAILY EXPIRY & CREDIT DIGEST" in msg
    assert "user1@example.com" in msg
    assert "3,000" in msg
    assert "EXPIRING IN NEXT 24–48 HOURS" in msg
    assert "user2@example.com" in msg


def test_send_whatsapp_alert_dry_run():
    res = send_whatsapp_alert(message="Test Message", dry_run=True)
    assert res["status"] == "dry_run"


def test_send_whatsapp_alert_api_call():
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Message queued"
        mock_get.return_value = mock_resp

        res = send_whatsapp_alert(
            message="Test Alert",
            phone="+919999999999",
            apikey="mock_key",
            dry_run=False
        )

        assert res["status"] == "success"
        mock_get.assert_called_once()


def test_format_email_expiry_report_contains_live_account_table():
    now_utc = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    accounts = [{
        "id": 1,
        "email": "user1@example.com",
        "name": "User One",
        "status": "active",
        "billing_end": "2026-09-20T12:00:00+00:00",
        "credits_avail": 4000,
        "credits_remaining": 3000,
    }]

    html_report = format_email_expiry_report(accounts, now_utc=now_utc)
    csv_report = build_expiry_csv(accounts, now_utc=now_utc)

    assert "Apollo Accounts Expiring Within the Next 7 Days" in html_report
    assert "user1@example.com" in html_report
    assert "3,000" in html_report
    assert "Account ID,Account Name,Email" in csv_report


def test_expiry_pipeline_dispatches_email_once():
    now_utc = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    accounts = [{
        "id": 1,
        "email": "user1@example.com",
        "name": "User One",
        "status": "active",
        "billing_end": "2026-09-22T12:00:00+00:00",
        "credits_avail": 4000,
        "credits_remaining": 3000,
    }]

    with patch("scripts.send_saving_report.dispatch_email") as dispatch:
        dispatch.return_value = {"status": "success", "recipients": ["ops@example.com"]}
        result = run_expiry_report_pipeline(
            channels=["email"],
            accounts_data=accounts,
            now_utc=now_utc,
        )

    assert result["status"] == "ok"
    assert result["total_accounts"] == 1
    assert result["channels"]["email"]["status"] == "success"
    dispatch.assert_called_once()


def test_expiry_email_contains_only_next_seven_days():
    now_utc = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    accounts = [
        {
            "id": 1,
            "name": "Within Window",
            "email": "within@example.com",
            "status": "active",
            "billing_end": "2026-09-27T08:00:00+00:00",
            "credits_avail": 4000,
            "credits_remaining": 3000,
        },
        {
            "id": 2,
            "name": "Outside Window",
            "email": "outside@example.com",
            "status": "active",
            "billing_end": "2026-09-27T08:00:01+00:00",
            "credits_avail": 4000,
            "credits_remaining": 3000,
        },
        {
            "id": 3,
            "name": "Already Expired",
            "email": "expired@example.com",
            "status": "active",
            "billing_end": "2026-09-20T07:59:59+00:00",
            "credits_avail": 4000,
            "credits_remaining": 3000,
        },
    ]

    filtered = filter_accounts_expiring_within(accounts, now_utc, days=7)
    assert [account["id"] for account in filtered] == [1]

    with patch("scripts.send_saving_report.dispatch_email") as dispatch:
        dispatch.return_value = {"status": "success", "recipients": ["ops@example.com"]}
        result = run_expiry_report_pipeline(
            channels=["email"],
            accounts_data=accounts,
            now_utc=now_utc,
        )

    subject, html_report, csv_report = dispatch.call_args.args[:3]
    assert "1 Expiring Within 7 Days" in subject
    assert "within@example.com" in html_report
    assert "outside@example.com" not in html_report
    assert "expired@example.com" not in html_report
    assert "within@example.com" in csv_report
    assert "outside@example.com" not in csv_report
    assert result["total_accounts_probed"] == 3
    assert result["total_accounts"] == 1
