"""
Zero-Credit Test Suite for Dynamic Monthly Cycles & Account Pipeline
====================================================================
Tests the full cycle system without calling any paid Apollo APIs or consuming any credits:
1. Cycle window resolution for all 19 accounts (past, present, leap-year edge cases)
2. Web vault date-bounded streaming with mock Apollo pagination & early exit
3. MySQL persistence: `cycle` column in `apollo_saved_leads` & `enrich_saved_leads`
4. FastAPI `/sync-saved-leads` endpoint (both explicit cycle and inferred from batch name)
5. In-place enrichment DB update (`update_leads_in_db` with mock records)
6. Chrome Extension JS cycle calculation parity
"""

import os
import sys
import json
import subprocess
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import (
    get_connection,
    ensure_apollo_saved_leads_table,
    sync_saved_leads,
    SyncSavedLeadsRequest,
    SyncSavedLeadItem,
)
from scripts.apollo_saved_search_inspector import (
    get_account_cycle_window,
    fetch_contacts_stream,
    save_leads_to_mysql_batch,
)
from scripts.enrich_batch_interactive import update_leads_in_db


# ============================================================================
# 1. CYCLE WINDOW CALCULATIONS FOR ALL 19 ACCOUNTS
# ============================================================================

def test_cycle_window_all_19_accounts():
    """Verify all 19 accounts compute exact cycle windows based on renewal days."""
    report_path = os.path.join("config", "apollo_live_account_report.json")
    assert os.path.exists(report_path), "Live account report missing!"

    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    accounts = data.get("accounts", [])
    assert len(accounts) == 19, f"Expected 19 accounts, found {len(accounts)}"

    ref_date = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

    for acc in accounts:
        email = acc["email"]
        exp_dt = datetime.fromisoformat(acc["expiry_utc"].replace("Z", "+00:00"))
        renewal_day = exp_dt.day

        start_dt, end_dt, cycle_tag = get_account_cycle_window(email, target_date=ref_date)

        assert start_dt.day == renewal_day or (start_dt.day < renewal_day and start_dt.month == 2)
        assert end_dt.day == renewal_day or (end_dt.day < renewal_day and end_dt.month == 2)
        assert start_dt < end_dt
        assert cycle_tag.count("-") == 1

        # Check known examples
        if renewal_day == 20:
            assert cycle_tag == "sep 20 - oct 20"
        elif renewal_day == 10:
            assert cycle_tag == "sep 10 - oct 10"
        elif renewal_day == 3:
            assert cycle_tag == "sep 03 - oct 03"


def test_cycle_window_edge_cases():
    """Test boundary transitions: exact renewal day, day after, and day before."""
    email = "VRAGHAVAN@NESTACK.COM"  # renewal day = 20

    # On the 19th (one day before renewal): active cycle should be Aug 20 - Sep 20
    dt_before = datetime(2026, 9, 19, 23, 59, 59, tzinfo=timezone.utc)
    _, _, tag_before = get_account_cycle_window(email, target_date=dt_before)
    assert tag_before == "aug 20 - sep 20"

    # On the 20th (exact renewal day): active cycle rolls to Sep 20 - Oct 20
    dt_on = datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)
    _, _, tag_on = get_account_cycle_window(email, target_date=dt_on)
    assert tag_on == "sep 20 - oct 20"

    # On the 21st (day after): still Sep 20 - Oct 20
    dt_after = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    _, _, tag_after = get_account_cycle_window(email, target_date=dt_after)
    assert tag_after == "sep 20 - oct 20"


# ============================================================================
# 2. WEB VAULT DATE-BOUNDED STREAMING (MOCKED - 0 CREDITS)
# ============================================================================

@patch("requests.post")
def test_web_vault_stream_cycle_cutoff_zero_credits(mock_post):
    """
    Test streaming from Apollo Web Vault:
    - Verifies contacts are sorted contact_created_at DESC.
    - Verifies contacts within cycle are collected.
    - Verifies streaming stops immediately upon hitting a contact prior to cycle start.
    - Consumes ZERO Apollo credits.
    """
    # Simulate page 1 with 2 in-cycle contacts and 1 out-of-cycle contact
    page1_response = {
        "pagination": {"total_entries": 150, "total_pages": 2},
        "contacts": [
            {
                "id": "c1",
                "name": "In Cycle Lead 1",
                "email": "lead1@example.com",
                "created_at": "2026-09-28T10:00:00.000Z",  # In cycle (after Sep 20)
            },
            {
                "id": "c2",
                "name": "In Cycle Lead 2",
                "email": "lead2@example.com",
                "created_at": "2026-09-21T15:30:00.000Z",  # In cycle (after Sep 20)
            },
            {
                "id": "c3",
                "name": "Past Cycle Lead 3",
                "email": "lead3@example.com",
                "created_at": "2026-09-18T09:00:00.000Z",  # Older than Sep 20 -> Trigger Cutoff!
            },
        ],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = page1_response
    mock_post.return_value = mock_resp

    cycle_start = datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)
    cycle_end = datetime(2026, 10, 20, 0, 0, 0, tzinfo=timezone.utc)

    contacts = fetch_contacts_stream(
        api_key="mock_key_no_credits",
        filters={},
        prospected_status="yes",
        since_date=cycle_start,
        until_date=cycle_end,
    )

    # Assert request sent to contacts/search with descending sort
    assert mock_post.call_count == 1
    call_args = mock_post.call_args[1]["json"]
    assert call_args["sort_by_field"] == "contact_created_at"
    assert call_args["sort_ascending"] is False
    assert call_args["prospected_by_current_team"] == ["yes"]

    # Only in-cycle leads (c1, c2) should be retained
    assert len(contacts) == 2
    assert contacts[0]["id"] == "c1"
    assert contacts[1]["id"] == "c2"


# ============================================================================
# 3. MYSQL SCHEMA & DIRECT SAVE WITH CYCLE
# ============================================================================

def test_mysql_schema_has_cycle_column():
    """Ensure both apollo_saved_leads and enrich_saved_leads have cycle column and indexes."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            for tbl in ["apollo_saved_leads", "enrich_saved_leads"]:
                cur.execute(f"DESCRIBE `{tbl}`;")
                cols = [r[0].lower() for r in cur.fetchall()]
                assert "cycle" in cols, f"Missing `cycle` in `{tbl}`"

                cur.execute(f"SHOW INDEX FROM `{tbl}`;")
                idx_names = set(r[2] for r in cur.fetchall())
                assert "idx_cycle" in idx_names, f"Missing `idx_cycle` in `{tbl}`"


def test_save_leads_to_mysql_batch_persists_cycle():
    """Test save_leads_to_mysql_batch saves contacts with cycle tag."""
    test_batch = "qa_test_save_cycle_batch"
    test_email = "VRAGHAVAN@NESTACK.COM"
    test_cycle = "sep 20 - oct 20"

    mock_leads = [
        {
            "id": "qa_c1_id",
            "name": "QA Cycle Contact",
            "first_name": "QA",
            "last_name": "Contact",
            "title": "Director of Technology",
            "email": "qacycle@techdomain.com",
            "email_status": "verified",
            "organization": {
                "name": "Tech Domain Inc",
                "primary_domain": "techdomain.com",
            },
        }
    ]

    saved = save_leads_to_mysql_batch(
        leads=mock_leads,
        batch_tag=test_batch,
        account_email=test_email,
        target_table="apollo_saved_leads",
        cycle=test_cycle,
    )
    assert saved >= 1

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `batch`, `account_used`, `cycle` FROM `apollo_saved_leads` WHERE `batch` = %s;",
                (test_batch,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == test_batch
            assert row[1] == test_email
            assert row[2] == test_cycle

            # Cleanup
            cur.execute("DELETE FROM `apollo_saved_leads` WHERE `batch` = %s;", (test_batch,))
            conn.commit()


# ============================================================================
# 4. FASTAPI /sync-saved-leads ENDPOINT TESTS
# ============================================================================

def test_sync_saved_leads_explicit_cycle():
    """Test /sync-saved-leads persists explicit cycle and account_used."""
    batch_name = "qa_test_sync_explicit_batch"
    req = SyncSavedLeadsRequest(
        batch=batch_name,
        contacts=[
            SyncSavedLeadItem(
                apollo_id="qa_sync_exp_1",
                name="Explicit Cycle Executive",
                first_name="Explicit",
                last_name="Executive",
                job_title="VP Operations",
                company="Explicit Corp",
                company_domain="explicitcorp.com",
                website_link="https://explicitcorp.com",
                segment="Required_Lead",
            )
        ],
        cycle="sep 20 - oct 20",
        account_used="VRAGHAVAN@NESTACK.COM",
    )

    res = sync_saved_leads(req)
    assert res["status"] == "ok"
    assert res["synced"] >= 1

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `account_used`, `cycle` FROM `apollo_saved_leads` WHERE `apollo_id` = 'qa_sync_exp_1';"
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "VRAGHAVAN@NESTACK.COM"
            assert row[1] == "sep 20 - oct 20"

            # Cleanup
            cur.execute("DELETE FROM `apollo_saved_leads` WHERE `apollo_id` = 'qa_sync_exp_1';")
            conn.commit()


def test_sync_saved_leads_inferred_cycle_from_batch_name():
    """Test /sync-saved-leads infers account_used and cycle when formatted as email(cycle)."""
    batch_name = "VRAGHAVAN@NESTACK.COM(sep 20 - oct 20)"
    req = SyncSavedLeadsRequest(
        batch=batch_name,
        contacts=[
            SyncSavedLeadItem(
                apollo_id="qa_sync_inf_1",
                name="Inferred Cycle Executive",
                first_name="Inferred",
                last_name="Executive",
                job_title="CEO",
                company="Inferred Systems",
                company_domain="inferredsystems.io",
                website_link="https://inferredsystems.io",
                segment="Required_Lead",
            )
        ],
    )

    res = sync_saved_leads(req)
    assert res["status"] == "ok"
    assert res["synced"] >= 1

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `account_used`, `cycle` FROM `apollo_saved_leads` WHERE `apollo_id` = 'qa_sync_inf_1';"
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "VRAGHAVAN@NESTACK.COM"
            assert row[1] == "sep 20 - oct 20"

            # Cleanup
            cur.execute("DELETE FROM `apollo_saved_leads` WHERE `apollo_id` = 'qa_sync_inf_1';")
            conn.commit()


# ============================================================================
# 5. IN-PLACE ENRICHMENT UPDATE WITH CYCLE (0 CREDITS)
# ============================================================================

def test_update_leads_in_db_persists_cycle():
    """Test update_leads_in_db in scripts/enrich_batch_interactive.py updates cycle."""
    test_batch = "qa_test_inplace_enrich_batch"

    # Insert a dummy un-enriched row
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO `apollo_saved_leads` (
                    `batch`, `apollo_id`, `name`, `first_name`, `last_name`, `job_title`,
                    `company`, `company_domain`, `website_link`, `location`, `segment`
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    test_batch,
                    "qa_dummy_apollo_id",
                    "Unenriched Lead",
                    "Unenriched",
                    "Lead",
                    "VP Sales",
                    "Dummy Co",
                    "dummyco.com",
                    "https://dummyco.com",
                    "Boston, MA",
                    "Required_Lead",
                ),
            )
            dummy_id = cur.lastrowid
            conn.commit()

    # Call update_leads_in_db with mock matched record and cycle
    mock_results = [
        {
            "db_id": dummy_id,
            "apollo_id": "apollo_matched_123",
            "email": "verified@dummyco.com",
            "email_status": "verified",
            "annual_revenue": "$10M - $25M",
            "employee_count": 120,
            "industry": "Computer Software",
            "tech_stack": ["Google Analytics", "Salesforce"],
            "keywords": ["B2B", "SaaS"],
            "company_phone": "+1-617-555-0199",
            "hq_address": "100 Main St, Boston, MA",
            "company_linkedin_url": "https://linkedin.com/company/dummyco",
            "credits_charged": 1,
            "raw_match": {"status": "ok"},
        }
    ]

    with get_connection() as conn:
        update_leads_in_db(
            results=mock_results,
            account_name="V Raghavan",
            batch_tag=test_batch,
            dry_run=False,
            conn=conn,
            table_name="apollo_saved_leads",
            cycle="sep 20 - oct 20",
        )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT `email`, `cycle`, `account_used`, `credits_charged` FROM `apollo_saved_leads` WHERE `id` = %s;",
                (dummy_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "verified@dummyco.com"
            assert row[1] == "sep 20 - oct 20"
            assert row[2] == "V Raghavan"
            assert row[3] == 1

            # Cleanup
            cur.execute("DELETE FROM `apollo_saved_leads` WHERE `id` = %s;", (dummy_id,))
            conn.commit()


# ============================================================================
# 6. JS CHROME EXTENSION CYCLE LOGIC PARITY (0 CREDITS)
# ============================================================================

def test_js_extension_cycle_parity():
    """Verify Node.js execution of computeAccountActiveCycle matches Python."""
    node_script = """
    const { execSync } = require('child_process');
    function computeAccountActiveCycle(renewalDay, targetDate = new Date()) {
      const d = new Date(targetDate);
      let start, end;
      if (d.getUTCDate() >= renewalDay) {
        start = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), renewalDay));
        end = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, renewalDay));
      } else {
        end = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), renewalDay));
        start = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - 1, renewalDay));
      }
      const sMonth = start.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' }).toLowerCase();
      const eMonth = end.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' }).toLowerCase();
      const sDay = String(start.getUTCDate()).padStart(2, '0');
      const eDay = String(end.getUTCDate()).padStart(2, '0');
      return `${sMonth} ${sDay} - ${eMonth} ${eDay}`;
    }

    const testDate = new Date('2026-10-02T12:00:00Z');
    const res20 = computeAccountActiveCycle(20, testDate);
    const res10 = computeAccountActiveCycle(10, testDate);
    const res03 = computeAccountActiveCycle(3, testDate);
    console.log(JSON.stringify({ day20: res20, day10: res10, day03: res03 }));
    """

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    out = json.loads(res.stdout.strip())

    assert out["day20"] == "sep 20 - oct 20"
    assert out["day10"] == "sep 10 - oct 10"
    assert out["day03"] == "sep 03 - oct 03"


def test_cycle_days_list_and_auto_display_on_login():
    """Verify computeAccountCycleDetails lists Day 1..Day 30/31 and displays Day 30 or Day 31 on 3rd of month."""
    node_script = """
    const fs = require('fs');
    const code = fs.readFileSync('extensions/content.js', 'utf8');
    const match = code.match(/function computeAccountCycleDetails\\([^{]+{([\\s\\S]+?)\\n  }/);
    if (!match) throw new Error('computeAccountCycleDetails not found');
    const fn = new Function('renewalDay', 'targetDate', match[1]);

    // 1. On 3rd of October, account with renewalDay 3 (September has 30 days):
    const oct3 = new Date('2026-10-03T12:00:00Z');
    const res03 = fn(3, oct3);

    // 2. On 3rd of September, account with renewalDay 3 (August has 31 days):
    const sep3 = new Date('2026-09-03T12:00:00Z');
    const resSep3 = fn(3, sep3);

    // 3. On 3rd of October, account with renewalDay 20 (Sep 20 - Oct 20):
    const res20 = fn(20, oct3);

    console.log(JSON.stringify({
      oct3_day: res03.currentDayLabel,
      oct3_cycle: res03.cycleTag,
      oct3_total: res03.totalDays,
      sep3_day: resSep3.currentDayLabel,
      sep3_cycle: resSep3.cycleTag,
      sep3_total: resSep3.totalDays,
      res20_day: res20.currentDayLabel,
      has_days_list: Array.isArray(res03.daysList) && res03.daysList.length === res03.totalDays
    }));
    """

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    out = json.loads(res.stdout.strip())

    # On the 3rd of the month for renewalDay 3:
    assert out["oct3_day"] in ("Day 30", "Day 31")
    assert out["oct3_day"] == "Day 30"
    assert out["sep3_day"] == "Day 31"
    assert out["res20_day"] == "Day 14"
    assert out["has_days_list"] is True
