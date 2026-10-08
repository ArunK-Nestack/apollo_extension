import os
import sys
import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.apollo_search_optimizer import (
    generate_slicing_candidates_ai,
    probe_apollo_keyword_count,
    record_search_recommendations,
    get_search_history,
    STATIC_TOP_NAMES,
    STATIC_TOP_KEYWORDS,
)


def test_generate_slicing_candidates():
    # Test fallback generation with empty or sample filters
    filters = {"person_titles": ["VP Sales", "Director Sales"], "person_locations": ["United States"]}
    names, keywords = generate_slicing_candidates_ai(filters)
    assert len(names) >= 20
    assert len(keywords) >= 20
    assert "David" in names or "Michael" in names


def test_probe_apollo_keyword_count_mock():
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "pagination": {"page": 1, "per_page": 10, "total_entries": 4500, "total_pages": 100}
        }
        mock_post.return_value = mock_response

        res = probe_apollo_keyword_count("mock_api_key", {}, "David", "First Name")

        assert res["status"] == "success"
        assert res["keyword"] == "David"
        assert res["total_leads"] == 4500
        assert res["total_pages"] == 45


def test_history_memory_ledger():
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_history_path = Path(tmp_dir) / "test_history.json"

        with patch("scripts.apollo_search_optimizer.HISTORY_LEDGER_PATH", test_history_path):
            test_account = "test_user@example.com"
            test_search = "Director IT Search"

            # 1. Initially history is empty
            hist_before = get_search_history(test_account, test_search)
            assert hist_before == {}

            # 2. Record 2 recommendations
            recs_batch_1 = [
                {"keyword": "David", "category": "First Name", "total_leads": 18000, "total_pages": 100},
                {"keyword": "Michael", "category": "First Name", "total_leads": 16000, "total_pages": 100},
            ]
            record_search_recommendations(test_account, test_search, recs_batch_1)

            # 3. Verify history now remembers both keywords
            hist_after = get_search_history(test_account, test_search)
            assert "keywords" in hist_after
            assert "david" in hist_after["keywords"]
            assert "michael" in hist_after["keywords"]
            assert hist_after["keywords"]["david"]["total_pages"] == 100
            assert hist_after["keywords"]["david"]["times_recommended"] == 1

            # 4. Record next run with 1 new and 1 repeat
            recs_batch_2 = [
                {"keyword": "David", "category": "First Name", "total_leads": 18000, "total_pages": 100},
                {"keyword": "James", "category": "First Name", "total_leads": 15000, "total_pages": 100},
            ]
            record_search_recommendations(test_account, test_search, recs_batch_2)

            hist_run_2 = get_search_history(test_account, test_search)
            assert hist_run_2["keywords"]["david"]["times_recommended"] == 2
            assert hist_run_2["keywords"]["james"]["times_recommended"] == 1
