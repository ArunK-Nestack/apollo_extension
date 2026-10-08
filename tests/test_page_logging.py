"""
Test suite for per-page logging (required vs not required) and lead page numbers.
"""

import json
import pytest
from backend.api import (
    app,
    get_connection,
    ExtensionActivityRequest,
    log_extension_activity,
    SyncSavedLeadsRequest,
    SyncSavedLeadItem,
    sync_saved_leads,
    ensure_extension_activity_log_table,
    ensure_apollo_saved_leads_table,
)


def test_page_logging_endpoint():
    with get_connection() as conn:
        ensure_extension_activity_log_table(conn)

    sample_breakdown = [
        {
            "name": "Alice CEO",
            "title": "Chief Executive Officer",
            "company": "Tech Corp",
            "domain": "techcorp.com",
            "status": "REQUIRED",
            "reason": "Qualified Decision Maker",
        },
        {
            "name": "Bob Intern",
            "title": "Software Intern",
            "company": "Tech Corp",
            "domain": "techcorp.com",
            "status": "NOT_REQUIRED",
            "reason": "Title guardrail rejected",
        },
        {
            "name": "Charlie Manager",
            "title": "Sales Manager",
            "company": "Sales Inc",
            "domain": "salesinc.com",
            "status": "EXISTING",
            "reason": "Already in CRM",
        },
    ]

    req = ExtensionActivityRequest(
        event_type="PAGE_EVALUATED",
        account_email="test_user@example.com",
        cycle_tag="oct_test",
        batch="qa_page_test_batch",
        search_word="Director IT/Others/NA EST",
        search_bar_word="fintech",
        page_number=5,
        total_on_page=3,
        required_on_page=1,
        not_required_on_page=2,
        existing_on_page=1,
        guardrail_rejected_on_page=1,
        collected_total=1,
        page_url="https://app.apollo.io/#/people?page=5",
        breakdown_json=sample_breakdown,
    )

    res = log_extension_activity(req)
    assert res["status"] == "ok"

    # Verify database insertion
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_type, batch, search_word, search_bar_word, page_number, total_on_page,
                       required_on_page, not_required_on_page, existing_on_page,
                       guardrail_rejected_on_page, breakdown_json
                FROM extension_activity_log
                WHERE batch = 'qa_page_test_batch' AND page_number = 5
                ORDER BY id DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            assert row is not None
            ev, b, s_w, sb_w, pg, tot, req_cnt, not_req_cnt, exist_cnt, rej_cnt, b_json = row
            assert ev == "PAGE_EVALUATED"
            assert b == "qa_page_test_batch"
            assert s_w == "Director IT/Others/NA EST"
            assert sb_w == "fintech"
            assert pg == 5
            assert tot == 3
            assert req_cnt == 1
            assert not_req_cnt == 2
            assert exist_cnt == 1
            assert rej_cnt == 1
            
            parsed = json.loads(b_json)
            assert len(parsed) == 3
            assert parsed[0]["name"] == "Alice CEO"
            assert parsed[0]["status"] == "REQUIRED"
            assert parsed[1]["status"] == "NOT_REQUIRED"

            # Clean up test log
            cur.execute("DELETE FROM extension_activity_log WHERE batch = 'qa_page_test_batch'")
        conn.commit()


def test_saved_lead_page_number():
    with get_connection() as conn:
        ensure_apollo_saved_leads_table(conn)

    lead_item = SyncSavedLeadItem(
        apollo_id="qa_lead_page_test_1",
        name="Diana Prince",
        first_name="Diana",
        last_name="Prince",
        job_title="VP of Sales",
        company="Themyscira Global",
        domain="themyscira.com",
        company_domain="themyscira.com",
        website_link="https://themyscira.com",
        page_number=4,
    )

    req = SyncSavedLeadsRequest(
        batch="qa_sync_page_batch",
        contacts=[lead_item],
        page_number=4,
        cycle="oct_cycle",
        account_used="test_acc@example.com",
    )

    sync_res = sync_saved_leads(req)
    assert sync_res["status"] == "ok"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT apollo_id, batch, page_number FROM apollo_saved_leads WHERE apollo_id = 'qa_lead_page_test_1'"
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "qa_lead_page_test_1"
            assert row[1] == "qa_sync_page_batch"
            assert row[2] == 4

            # Cleanup
            cur.execute("DELETE FROM apollo_saved_leads WHERE apollo_id = 'qa_lead_page_test_1'")
        conn.commit()
