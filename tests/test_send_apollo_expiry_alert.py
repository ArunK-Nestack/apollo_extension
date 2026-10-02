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
    format_whatsapp_expiry_message,
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
