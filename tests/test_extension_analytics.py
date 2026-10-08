from datetime import datetime, timedelta

from backend.dashboard_routes import _aggregate_extension_analytics


def _row(row_id, created_at, event_type="PAGE_EVALUATED", **overrides):
    row = {
        "id": row_id,
        "event_type": event_type,
        "account_email": "analytics@example.com",
        "cycle_tag": "sep-08-oct-08",
        "batch": "oct-directors",
        "search_word": "Director IT / EST",
        "search_bar_word": "fintech",
        "page_number": 1,
        "total_on_page": 25,
        "required_on_page": 8,
        "not_required_on_page": 17,
        "existing_on_page": 12,
        "guardrail_rejected_on_page": 2,
        "collected_total": 8,
        "created_at": created_at,
    }
    row.update(overrides)
    return row


def test_extension_analytics_groups_searches_and_ignores_non_evaluation_events():
    start = datetime(2026, 10, 8, 10, 30)
    rows = [
        _row(1, start),
        _row(2, start + timedelta(minutes=3), page_number=2, required_on_page=5, existing_on_page=15),
        _row(3, start + timedelta(minutes=4), event_type="PAGE_SAVED", total_on_page=13, required_on_page=13),
        _row(
            4,
            start + timedelta(days=1),
            search_word="CTO California",
            search_bar_word="SaaS",
            page_number=1,
            required_on_page=2,
            existing_on_page=20,
        ),
    ]

    result = _aggregate_extension_analytics(rows, saved_leads=14)

    assert result["summary"]["pages"] == 3
    assert result["summary"]["evaluated"] == 75
    assert result["summary"]["required"] == 15
    assert result["summary"]["saved"] == 14
    assert result["summary"]["required_rate"] == 20.0
    assert len(result["searches"]) == 2
    assert len(result["daily_trend"]) == 2
    assert result["filters"]["search_bar_words"] == ["SaaS", "fintech"]


def test_extension_analytics_removes_only_near_identical_retry_events():
    start = datetime(2026, 10, 8, 10, 30)
    rows = [
        _row(1, start),
        _row(2, start + timedelta(seconds=45)),
        _row(3, start + timedelta(minutes=10)),
    ]

    result = _aggregate_extension_analytics(rows)

    assert result["summary"]["pages"] == 2
    assert result["summary"]["evaluated"] == 50
    assert result["data_quality"]["duplicate_retries_removed"] == 1


def test_extension_analytics_reports_missing_search_context_without_dropping_rows():
    rows = [
        _row(
            1,
            datetime(2026, 10, 8, 10, 30),
            search_word="",
            search_bar_word="",
            required_on_page=0,
        )
    ]

    result = _aggregate_extension_analytics(rows)

    assert result["summary"]["pages"] == 1
    assert result["data_quality"]["both_missing"] == 1
    assert result["data_quality"]["context_capture_rate"] == 0
    assert result["searches"][0]["search_word"] == "Unknown / Not captured"


def test_extension_analytics_empty_state_is_well_formed():
    result = _aggregate_extension_analytics([])

    assert result["summary"]["pages"] == 0
    assert result["summary"]["required_rate"] == 0.0
    assert result["searches"] == []
    assert result["pages"] == []

