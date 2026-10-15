from __future__ import annotations

from backend.cyclepilot_agent import (
    calculate_target,
    load_scoped_searches,
    make_manifest_hash,
    public_accounts,
)
from backend.dashboard_routes import (
    cyclepilot_accounts,
    cyclepilot_dashboard,
    cyclepilot_searches,
    dashboard_router,
)


def test_cyclepilot_page_is_separate_and_discoverable():
    response = cyclepilot_dashboard()
    page = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "CyclePilot Agent" in page
    assert "Freshsales" not in page
    paths = {getattr(route, "path", "") for route in dashboard_router.routes}
    assert "/cyclepilot" in paths
    assert "/api/v1/cyclepilot/runs/{run_id}/prepare" in paths


def test_public_accounts_never_expose_credentials():
    accounts = public_accounts()
    assert len(accounts) == 19
    assert all(set(account) <= {"id", "name", "email"} for account in accounts)


def test_searches_are_owned_by_selected_login_only():
    accounts = public_accounts()
    first = accounts[0]["email"]
    second = accounts[1]["email"]
    first_searches = load_scoped_searches(first)
    second_searches = load_scoped_searches(second)
    assert first_searches
    assert all("filters" in search for search in first_searches)
    assert first_searches != second_searches
    assert load_scoped_searches("missing@example.invalid") == []


def test_credit_target_respects_reserve_and_existing_inventory():
    assert calculate_target(100, 25, 20) == {
        "usable_credits": 75,
        "existing_ready": 20,
        "new_contacts_needed": 55,
    }
    assert calculate_target(10, 25, 99)["new_contacts_needed"] == 0


def test_manifest_hash_is_order_independent_but_content_sensitive():
    assert make_manifest_hash({"batch": "A", "count": 10}) == make_manifest_hash({"count": 10, "batch": "A"})
    assert make_manifest_hash({"batch": "A", "count": 10}) != make_manifest_hash({"batch": "A", "count": 11})


def test_cyclepilot_accounts_and_scoped_search_api():
    accounts = cyclepilot_accounts()["accounts"]
    assert accounts and "api_key" not in accounts[0]
    searches = cyclepilot_searches(accounts[0]["email"])["searches"]
    assert isinstance(searches, list)

