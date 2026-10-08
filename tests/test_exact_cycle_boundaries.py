import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from backend.api import app
from scripts.apollo_saved_search_inspector import get_account_cycle_window, format_cycle_tag

IST = timezone(timedelta(hours=5, minutes=30))

def test_vijay_exact_second_boundary_transition():
    """
    VIJAY@NESTACKTECH.COM has expiry_utc: 2026-10-08T12:31:15+00:00 (18:01:15 IST).
    Before 18:01:15 IST -> active cycle MUST be sep 08 - oct 08.
    At and after 18:01:15 IST -> active cycle MUST be oct 08 - nov 08.
    """
    account_email = "VIJAY@NESTACKTECH.COM"

    # 1 second before expiration
    t_before = datetime(2026, 10, 8, 18, 1, 14, tzinfo=IST)
    start_b, end_b, tag_b = get_account_cycle_window(account_email, t_before)
    assert tag_b == "sep 08 - oct 08"
    assert start_b <= t_before.astimezone(timezone.utc) < end_b

    # Exact second of expiration
    t_exact = datetime(2026, 10, 8, 18, 1, 15, tzinfo=IST)
    start_e, end_e, tag_e = get_account_cycle_window(account_email, t_exact)
    assert tag_e == "oct 08 - nov 08"
    assert start_e <= t_exact.astimezone(timezone.utc) < end_e

    # 1 second after expiration
    t_after = datetime(2026, 10, 8, 18, 1, 16, tzinfo=IST)
    start_a, end_a, tag_a = get_account_cycle_window(account_email, t_after)
    assert tag_a == "oct 08 - nov 08"
    assert start_a <= t_after.astimezone(timezone.utc) < end_a


def test_api_account_cycle_endpoint():
    """Verify FastAPI /api/account-cycle returns exact timestamp-aware cycle tags."""
    client = TestClient(app)

    # Before expiry
    resp_before = client.get("/api/account-cycle", params={
        "email": "VIJAY@NESTACKTECH.COM",
        "target_date": "2026-10-08T18:01:14+05:30"
    })
    assert resp_before.status_code == 200
    data_before = resp_before.json()
    assert data_before["cycle_tag"] == "sep 08 - oct 08"
    assert data_before["email"] == "VIJAY@NESTACKTECH.COM"

    # At expiry
    resp_exact = client.get("/api/account-cycle", params={
        "email": "VIJAY@NESTACKTECH.COM",
        "target_date": "2026-10-08T18:01:15+05:30"
    })
    assert resp_exact.status_code == 200
    data_exact = resp_exact.json()
    assert data_exact["cycle_tag"] == "oct 08 - nov 08"

    # All accounts endpoint
    resp_all = client.get("/api/account-cycles")
    assert resp_all.status_code == 200
    data_all = resp_all.json()
    assert data_all["count"] == 19
    assert len(data_all["accounts"]) == 19


def test_all_19_accounts_containment_and_consistency():
    """Verify that get_account_cycle_window produces valid windows containing target_date for all 19 accounts."""
    import json
    with open("config/apollo_live_account_report.json", "r", encoding="utf-8") as f:
        report = json.load(f)

    test_now = datetime(2026, 10, 8, 10, 51, 0, tzinfo=IST)
    test_now_utc = test_now.astimezone(timezone.utc)

    for acc in report["accounts"]:
        email = acc["email"]
        start_dt, end_dt, tag = get_account_cycle_window(email, test_now)
        # Check window validity
        assert start_dt < end_dt, f"Start must precede end for {email}"
        assert start_dt <= test_now_utc < end_dt, f"target_date must be in [start, end) for {email}"
        assert tag == format_cycle_tag(start_dt, end_dt)
