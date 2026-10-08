from datetime import date, datetime, timedelta, timezone

from scripts.send_extension_analytics_report import (
    IST,
    aggregate_daily_report,
    build_report_csv,
    format_email_report,
    report_window,
)


def _row(row_id, email, event_type="PAGE_EVALUATED", **overrides):
    row = {
        "id": row_id,
        "event_type": event_type,
        "account_email": email,
        "cycle_tag": "oct-08-nov-08",
        "batch": "daily-directors",
        "search_word": "IT directors",
        "search_bar_word": "fintech",
        "page_number": row_id,
        "total_on_page": 25,
        "required_on_page": 7,
        "not_required_on_page": 18,
        "existing_on_page": 14,
        "guardrail_rejected_on_page": 4,
        "collected_total": 7,
        "created_at": datetime(2026, 10, 8, 8, row_id),
    }
    row.update(overrides)
    return row


def test_report_window_uses_ist_calendar_day_and_stops_at_generation_time():
    now_utc = datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc)  # 18:00 IST
    target, start, end = report_window(now_utc=now_utc)

    assert target == date(2026, 10, 8)
    assert start == datetime(2026, 10, 7, 18, 30, tzinfo=timezone.utc)
    assert end == now_utc
    assert start.astimezone(IST).hour == 0


def test_daily_report_includes_only_logins_present_in_daily_rows():
    rows = [
        _row(1, "active@example.com"),
        _row(2, "active@example.com", event_type="PAGE_SAVED", total_on_page=0, required_on_page=0),
        _row(3, "second@example.com", required_on_page=2),
    ]
    report = aggregate_daily_report(
        rows,
        saved_counts={"active@example.com": 6, "inactive@example.com": 99},
        account_names={"active@example.com": "Active User"},
        report_date=date(2026, 10, 8),
        generated_at_utc=datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc),
    )

    assert report["totals"]["active_logins"] == 2
    assert report["totals"]["events"] == 3
    assert report["totals"]["evaluated"] == 50
    assert report["totals"]["saved"] == 6
    assert {login["email"] for login in report["logins"]} == {"active@example.com", "second@example.com"}
    assert "inactive@example.com" not in {login["email"] for login in report["logins"]}


def test_report_formats_email_and_csv_without_dispatching():
    report = aggregate_daily_report(
        [_row(1, "active@example.com")],
        saved_counts={"active@example.com": 3},
        account_names={"active@example.com": "Active & User"},
        report_date=date(2026, 10, 8),
        generated_at_utc=datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc),
    )

    html = format_email_report(report)
    csv_data = build_report_csv(report)

    assert "Active &amp; User" in html
    assert "No Apollo API lookup" in html
    assert "active@example.com" in csv_data
    assert "Saved Today" in csv_data


def test_non_page_activity_keeps_login_in_report_with_zero_contact_metrics():
    report = aggregate_daily_report([
        _row(1, "startup@example.com", event_type="EXTENSION_STARTED", total_on_page=0, required_on_page=0)
    ])

    assert report["totals"]["active_logins"] == 1
    assert report["totals"]["events"] == 1
    assert report["totals"]["pages"] == 0
    assert report["totals"]["evaluated"] == 0
