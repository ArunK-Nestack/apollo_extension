import pytest
import pandas as pd
from unittest.mock import MagicMock
from scripts.clean_enriched_export import clean_apollo_dataframe, get_lead_priority


def test_lead_priority():
    assert get_lead_priority({"Owner": "Arun"}) == 1
    assert get_lead_priority({"Job Title": "CEO and Founder"}) == 2
    assert get_lead_priority({"Job Title": "Chief Executive Officer (CEO)"}) == 4
    assert get_lead_priority({"Job Title": "Vice President of Sales"}) == 7
    assert get_lead_priority({"Job Title": "Software Engineer"}) == 999


def test_clean_apollo_dataframe_offline():
    raw_data = [
        {"Email": "ceo@brandalpha.com", "Job Title": "CEO", "First Name": "Alice", "Last Name": "Smith", "Annual Revenue": "$1,000,000"},
        {"Email": "ceo@brandalpha.com", "Job Title": "Owner", "First Name": "Alice", "Last Name": "Smith", "Annual Revenue": "$1,000,000"},  # Priority 1 beats 4
        {"Email": "vp@brandbeta.com", "Job Title": "VP", "First Name": "Bob", "Last Name": "", "Annual Revenue": "1234567890123"},  # >11 digits revenue
        {"Email": "nan", "Job Title": "Manager", "First Name": "Ghost", "Last Name": "User"},
        {"Email": "", "Job Title": "Director", "First Name": "Empty", "Last Name": "Email"},
    ]
    df = pd.DataFrame(raw_data)
    df_clean, stats = clean_apollo_dataframe(df)

    assert stats["initial_count"] == 5
    assert stats["valid_email_count"] == 3
    assert stats["duplicates_removed"] == 1
    assert len(df_clean) == 2

    # Owner should have won priority over CEO for brandalpha
    alpha_row = df_clean[df_clean["Email"] == "ceo@brandalpha.com"].iloc[0]
    assert alpha_row["Job Title"] == "Owner"
    assert alpha_row["Account"] == "brandalpha.com"

    # Blank last name should be filled with first name
    beta_row = df_clean[df_clean["Email"] == "vp@brandbeta.com"].iloc[0]
    assert beta_row["Last Name"] == "Bob"
    assert beta_row["First Name"] == ""

    # Over 11 digits revenue should be emptied
    assert beta_row["Annual Revenue"] == ""


def test_clean_apollo_dataframe_with_crm_guardrails():
    raw_data = [
        {"Email": "existing_ceo@crmcompany.com", "Job Title": "CEO", "First Name": "Dave", "Last Name": "Old"},
        {"Email": "branch_manager@franchiseholding.com", "Job Title": "Manager", "First Name": "Eve", "Last Name": "Franchise"},
        {"Email": "founder@brandnewcorp999.com", "Job Title": "Founder", "First Name": "Frank", "Last Name": "New"},
        {"Email": "cto@brandnewcorp999.com", "Job Title": "CTO", "First Name": "Grace", "Last Name": "Tech"},
    ]
    df = pd.DataFrame(raw_data)

    # Mock DB connection
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Mock responses:
    # 1. Query email: matches "existing_ceo@crmcompany.com"
    # 2. Query domain: matches "franchiseholding.com"
    mock_cur.fetchall.side_effect = [
        [("existing_ceo@crmcompany.com",)],  # CRM email match
        [("franchiseholding.com",)],         # CRM domain match
    ]

    df_clean, stats = clean_apollo_dataframe(
        df,
        conn=mock_conn,
        filter_crm_emails=True,
        filter_crm_domains=True,
        dedup_accounts=False,
    )

    assert stats["crm_emails_removed"] == 1
    assert stats["crm_domains_removed"] == 1
    assert len(df_clean) == 2
    assert set(df_clean["Email"]) == {"founder@brandnewcorp999.com", "cto@brandnewcorp999.com"}


def test_clean_apollo_dataframe_dedup_accounts():
    raw_data = [
        {"Email": "cto@brandnewcorp999.com", "Job Title": "CTO", "First Name": "Grace", "Last Name": "Tech"},
        {"Email": "founder@brandnewcorp999.com", "Job Title": "Founder", "First Name": "Frank", "Last Name": "New"},
    ]
    df = pd.DataFrame(raw_data)

    df_clean, stats = clean_apollo_dataframe(df, dedup_accounts=True)
    assert len(df_clean) == 1
    assert stats["account_duplicates_removed"] == 1
    assert df_clean.iloc[0]["Job Title"] == "Founder"


def test_resolve_canonical_account():
    from scripts.clean_enriched_export import resolve_canonical_account

    # Lowercase enrich login
    email, name = resolve_canonical_account("vraghavan@nestack.com", "vraghavan@nestack.com")
    assert email == "VRAGHAVAN@NESTACK.COM"
    assert name == "V Raghavan"

    # Uppercase login
    email, name = resolve_canonical_account("VRAGHAVAN@NESTACK.COM", "V Raghavan")
    assert email == "VRAGHAVAN@NESTACK.COM"
    assert name == "V Raghavan"

    # Nestacktech account
    email, name = resolve_canonical_account("VRAGHAVAN@NESTACKTECH.COM", "V Raghavan")
    assert email == "VRAGHAVAN@NESTACKTECH.COM"
    assert name == "V Raghavan"

    # Shorthand batch-based resolution
    email, name = resolve_canonical_account("R Chandran", "R Chandran", "rchandran_nestack_biz")
    assert email == "RCHANDRAN@NESTACK.BIZ"
    assert name == "R Chandran"

