"""
Unit tests for Apollo People Search API Logger & Zero-Credit Auditing.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from backend.people_search_logger import (
    log_people_search_call,
    ensure_people_search_log_table,
    LOG_FILE_TXT,
    LOG_FILE_JSONL,
)
from scripts.view_people_search_logs import load_logs_from_jsonl, load_logs_from_db
from scripts.apollo_search_direct import query_apollo_search


def test_log_people_search_call_files(tmp_path):
    """Test that log_people_search_call creates file logs with 0 credits."""
    test_payload = {
        "page": 2,
        "per_page": 100,
        "q_keywords": "Chief Information Officer",
        "person_titles": ["CIO", "CTO"],
        "api_key": "SECRET_SHOULD_BE_STRIPPED",
    }

    entry = log_people_search_call(
        endpoint="https://api.apollo.io/api/v1/mixed_people/api_search",
        payload=test_payload,
        status_code=200,
        returned_count=100,
        total_entries=5420,
        duration_ms=235,
        account_email="test_audit@nestacktech.com",
        credits_charged=0,
    )

    # 1. Structure assertions
    assert entry["account_email"] == "test_audit@nestacktech.com"
    assert entry["page_number"] == 2
    assert entry["per_page"] == 100
    assert entry["keywords"] == "Chief Information Officer"
    assert entry["status_code"] == 200
    assert entry["returned_count"] == 100
    assert entry["total_entries"] == 5420
    assert entry["credits_charged"] == 0  # CRITICAL: 0 credits guarantee
    assert "api_key" not in entry["filters"]  # Security: API key stripped

    # 2. Verify file logs exist and contain the audit entry
    assert os.path.exists(LOG_FILE_TXT)
    assert os.path.exists(LOG_FILE_JSONL)

    with open(LOG_FILE_JSONL, "r", encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
        matches = [m for m in lines if m.get("account_email") == "test_audit@nestacktech.com"]
        assert len(matches) >= 1
        last = matches[-1]
        assert last["credits_charged"] == 0
        assert last["returned_count"] == 100


def test_log_people_search_call_db():
    """Test that log_people_search_call writes to MySQL people_search_api_log table."""
    try:
        from backend.api import get_connection
        conn = get_connection()
    except Exception:
        pytest.skip("MySQL connection not available in this environment")

    try:
        ensure_people_search_log_table(conn)

        test_email = "db_audit_test@nestacktech.com"
        log_people_search_call(
            endpoint="https://api.apollo.io/api/v1/mixed_people/api_search",
            payload={"page": 1, "per_page": 10, "q_keywords": "Hospitality"},
            status_code=200,
            returned_count=10,
            total_entries=1200,
            duration_ms=180,
            account_email=test_email,
            credits_charged=0,
            conn=conn,
        )

        with conn.cursor() as cur:
            cur.execute("""
                SELECT account_email, endpoint, page_number, keywords, returned_count, total_entries, credits_charged
                FROM `people_search_api_log`
                WHERE `account_email` = %s
                ORDER BY id DESC LIMIT 1
            """, (test_email,))
            row = cur.fetchone()

        assert row is not None
        acc, ep, pg, kw, ret_cnt, tot, credits = row
        assert acc == test_email
        assert "mixed_people/api_search" in ep
        assert pg == 1
        assert kw == "Hospitality"
        assert ret_cnt == 10
        assert tot == 1200
        assert credits == 0  # CRITICAL: Zero credits in database
    finally:
        conn.close()


def test_query_apollo_search_direct_integration():
    """Test query_apollo_search triggers log_people_search_call with 0 credits and captures duration."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "people": [{"id": "p1", "name": "John Doe", "title": "VP Sales"}],
        "total_entries": 42,
        "pagination": {"total_entries": 42, "page": 1, "per_page": 10}
    }

    with patch("requests.post", return_value=mock_response):
        with patch("scripts.apollo_search_direct.log_people_search_call") as mock_logger:
            res = query_apollo_search(
                api_key="fake_key_123",
                payload={"page": 1, "per_page": 10, "q_keywords": "Healthcare"},
                account_email="test_integration@nestacktech.com"
            )

            assert res["total_entries"] == 42
            assert len(res["people"]) == 1

            mock_logger.assert_called_once()
            call_kwargs = mock_logger.call_args.kwargs
            assert call_kwargs["account_email"] == "test_integration@nestacktech.com"
            assert call_kwargs["status_code"] == 200
            assert call_kwargs["returned_count"] == 1
            assert call_kwargs["total_entries"] == 42
            assert call_kwargs["credits_charged"] == 0  # ZERO credits!
            assert call_kwargs["duration_ms"] >= 0


def test_load_logs_helpers():
    """Test log viewer load functions return structured data."""
    entries = load_logs_from_jsonl(limit=10)
    assert isinstance(entries, list)

    db_entries = load_logs_from_db(limit=10)
    assert isinstance(db_entries, list)
