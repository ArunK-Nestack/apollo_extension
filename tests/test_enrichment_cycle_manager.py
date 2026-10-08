"""Zero-credit tests for enrichment decisions, run auditing, and API usage accounting."""

from unittest.mock import MagicMock, patch

from backend.api import (
    record_lead_enrichment_decisions,
    start_enrichment_run,
    update_enrichment_run,
)
from scripts.enrich_batch_interactive import (
    calculate_safe_enrichment_limit,
    enrich_leads_chunk,
    filter_canonical_cycle_batches,
)


class CaptureCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append(("execute", " ".join(sql.split()), params))

    def executemany(self, sql, params):
        self.calls.append(("executemany", " ".join(sql.split()), list(params)))


class CaptureConnection:
    def __init__(self):
        self.cursor_instance = CaptureCursor()

    def cursor(self):
        return self.cursor_instance


def _lead(lead_id=1):
    return {
        "id": lead_id,
        "apollo_id": f"apollo-{lead_id}",
        "first_name": "Alex",
        "last_name": "Example",
        "name": "Alex Example",
        "company": "Example Manufacturing",
        "company_domain": "example.com",
    }


def test_credit_guard_preserves_reserve():
    assert calculate_safe_enrichment_limit(487, 510, 10) == 487
    assert calculate_safe_enrichment_limit(168, 95, 10) == 85
    assert calculate_safe_enrichment_limit(20, 7, 10) == 0


def test_current_cycle_filter_excludes_legacy_and_test_batches():
    accounts = [{"email": "RAHUL.CHANDRAN@NESTACK-TECH.COM"}]
    batches = [
        {
            "batch": "rahul_chandran_tech_nestack_oct-1",
            "account_used": "RAHUL.CHANDRAN@NESTACK-TECH.COM",
            "cycle": "sep 03 - oct 03",
            "total_leads": 4084,
        },
        {
            "batch": "RAHUL.CHANDRAN@NESTACK-TECH.COM(sep 03 - oct 03)",
            "account_used": "RAHUL.CHANDRAN@NESTACK-TECH.COM",
            "cycle": "sep 03 - oct 03",
            "total_leads": 42,
        },
        {
            "batch": "testing",
            "account_used": "RAHUL.CHANDRAN@NESTACK-TECH.COM",
            "cycle": "sep 03 - oct 03",
            "total_leads": 42,
        },
    ]

    current = filter_canonical_cycle_batches(
        accounts,
        batches,
        cycle_resolver=lambda _email: "sep 03 - oct 03",
    )

    assert [row["batch"] for row in current] == [
        "RAHUL.CHANDRAN@NESTACK-TECH.COM(sep 03 - oct 03)"
    ]
    assert sum(row["total_leads"] for row in current) == 42


@patch("scripts.enrich_batch_interactive.requests.post")
def test_dry_run_never_calls_apollo(mock_post):
    result = enrich_leads_chunk("unused", [_lead()], dry_run=True)

    mock_post.assert_not_called()
    assert len(result) == 1
    assert result.credits_consumed == 0
    assert result[0]["credits_charged"] == 0


@patch("scripts.enrich_batch_interactive.requests.post")
def test_response_level_credit_usage_is_recorded(mock_post):
    response = MagicMock()
    response.status_code = 200
    response.ok = True
    response.json.return_value = {"matches": [], "credits_consumed": 0.5}
    mock_post.return_value = response

    result = enrich_leads_chunk("mock-key", [_lead()], dry_run=False)

    assert result.request_failed is False
    assert result.credits_consumed == 0.5


def test_run_audit_tracks_attempted_enriched_and_not_enriched():
    conn = CaptureConnection()
    run_id = start_enrichment_run(
        conn,
        "sales01@example.com",
        "sales01@example.com(oct 08 - nov 08)",
        510,
        run_id="00000000-0000-0000-0000-000000000001",
    )
    update_enrichment_run(conn, run_id, attempted_leads=10, number_enriched=8, credits_used=8, run_status="completed")

    update_call = next(
        call for call in reversed(conn.cursor_instance.calls)
        if call[0] == "execute" and "UPDATE `enrichment_run_audit`" in call[1]
    )
    assert update_call[2][0:4] == (10, 8, 2, 8)
    assert update_call[2][4] == "completed"


def test_carry_forward_keeps_source_batch_and_sets_target_cycle():
    conn = CaptureConnection()
    count = record_lead_enrichment_decisions(
        conn,
        "apollo_saved_leads",
        "original-source-batch",
        [11, 12, 12],
        "carry_forward",
        "nov 08 - dec 08",
    )

    assert count == 2
    insert_call = next(call for call in conn.cursor_instance.calls if call[0] == "executemany")
    rows = insert_call[2]
    assert rows[0][0:2] == ("apollo_saved_leads", "original-source-batch")
    assert {row[2] for row in rows} == {11, 12}
    assert all(row[3:] == ("carry_forward", "nov 08 - dec 08") for row in rows)
