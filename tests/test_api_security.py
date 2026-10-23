import backend.api as api


def test_health_remains_public_when_authentication_is_required(monkeypatch):
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "CONTACT_CHECKER_API_TOKEN", "test-secret")

    assert api.get_api_auth_error("/health", "GET", "") is None


def test_protected_endpoint_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "CONTACT_CHECKER_API_TOKEN", "test-secret")

    assert api.get_api_auth_error("/set-company-cap", "POST", "") == (
        401,
        "Invalid or missing API token.",
    )


def test_protected_endpoint_accepts_valid_token(monkeypatch):
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "CONTACT_CHECKER_API_TOKEN", "test-secret")

    assert api.get_api_auth_error("/set-company-cap", "POST", "test-secret") is None


def test_required_authentication_without_server_token_fails_closed(monkeypatch):
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "CONTACT_CHECKER_API_TOKEN", "")
    monkeypatch.setattr(api, "API_MEMBER_TOKENS", {})

    assert api.get_api_auth_error("/set-company-cap", "POST", "") == (
        503,
        "API authentication is required but no server token is configured.",
    )


def test_individual_member_token_is_accepted_and_identified(monkeypatch):
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "CONTACT_CHECKER_API_TOKEN", "")
    monkeypatch.setattr(api, "API_MEMBER_TOKENS", {"member-one": "member-secret"})

    assert api.get_api_auth_error("/sync-saved-leads", "POST", "member-secret") is None
    assert api.resolve_api_member("member-secret") == "member-one"
