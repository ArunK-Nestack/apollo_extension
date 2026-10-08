from scripts.apollo_account_report import merge_probe_results_with_cache


def test_failed_probe_preserves_last_known_expiry_and_credits():
    live = [{
        "id": 4,
        "name": "Vijay",
        "email": "VIJAY@NESTACKTECH.COM",
        "status": "Error: network unavailable",
        "billing_end": None,
        "credits_avail": 0,
        "credits_remaining": 0,
    }]
    cached = {
        "generated_at": "07 Oct 2026 05:00:00 PM IST",
        "accounts": [{
            "id": 4,
            "name": "Vijay",
            "email": "VIJAY@NESTACKTECH.COM",
            "status": "active",
            "expiry_utc": "2026-10-08T12:31:15+00:00",
            "credits_avail": 4000,
            "credits_remaining": 1000,
        }],
    }

    merged = merge_probe_results_with_cache(live, cached)[0]

    assert merged["billing_end"] == "2026-10-08T12:31:15+00:00"
    assert merged["credits_remaining"] == 1000
    assert merged["status"] == "active"
    assert merged["is_stale"] is True
    assert merged["probe_status"] == "Error: network unavailable"
    assert merged["last_success_at"] == "07 Oct 2026 05:00:00 PM IST"


def test_successful_probe_replaces_cached_values():
    live = [{
        "id": 4,
        "name": "Vijay",
        "email": "VIJAY@NESTACKTECH.COM",
        "status": "active",
        "billing_end": "2026-11-08T12:31:15+00:00",
        "credits_avail": 4000,
        "credits_remaining": 3999,
    }]
    cached = {
        "generated_at": "07 Oct 2026 05:00:00 PM IST",
        "accounts": [{
            "email": "VIJAY@NESTACKTECH.COM",
            "expiry_utc": "2026-10-08T12:31:15+00:00",
            "credits_remaining": 1000,
        }],
    }

    merged = merge_probe_results_with_cache(live, cached, successful_at="08 Oct 2026 04:00:00 PM IST")[0]

    assert merged["billing_end"] == "2026-11-08T12:31:15+00:00"
    assert merged["credits_remaining"] == 3999
    assert merged["is_stale"] is False
    assert merged["source"] == "live"
    assert merged["last_success_at"] == "08 Oct 2026 04:00:00 PM IST"
