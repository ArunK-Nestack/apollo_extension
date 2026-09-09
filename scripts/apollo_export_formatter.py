"""
Apollo 75-Column Standard Contacts Export Formatter.
Maps database leads and raw Apollo enrichment payloads to the exact
schema of Apollo's official CSV Contact Export (75 columns).
"""

import json
from datetime import datetime
from typing import Dict, Any, List

APOLLO_75_HEADERS = [
    "First Name",
    "Last Name",
    "Title",
    "Company Name",
    "Company Name for Emails",
    "Email",
    "Email Status",
    "Primary Email Source",
    "Primary Email Verification Source",
    "Email Confidence",
    "Primary Email Catch-all Status",
    "Primary Email Last Verified At",
    "Seniority",
    "Departments",
    "Sub Departments",
    "Contact Owner",
    "Work Direct Phone",
    "Home Phone",
    "Mobile Phone",
    "Corporate Phone",
    "Other Phone",
    "Do Not Call",
    "Stage",
    "Lists",
    "Last Contacted",
    "Account Owner",
    "# Employees",
    "Industry",
    "Keywords",
    "Person Linkedin Url",
    "Website",
    "Company Linkedin Url",
    "Facebook Url",
    "Twitter Url",
    "Address",
    "City",
    "State",
    "Country",
    "Company Address",
    "Company City",
    "Company State",
    "Company Country",
    "Company Phone",
    "Technologies",
    "Annual Revenue",
    "Total Funding",
    "Latest Funding",
    "Latest Funding Amount",
    "Last Raised At",
    "Parent company (Apollo data)",
    "Parent company (Apollo data - Organization ID)",
    "Email Sent",
    "Email Open",
    "Email Bounced",
    "Replied",
    "Demoed",
    "Number of Retail Locations",
    "SIC Codes",
    "NAICS Codes",
    "Apollo Contact Id",
    "Apollo Account Id",
    "Apollo Record Id",
    "Secondary Email",
    "Secondary Email Source",
    "Secondary Email Status",
    "Secondary Email Verification Source",
    "Tertiary Email",
    "Tertiary Email Source",
    "Tertiary Email Status",
    "Tertiary Email Verification Source",
    "Primary Intent Topic",
    "Primary Intent Score",
    "Secondary Intent Topic",
    "Secondary Intent Score",
    "account stage"
]

def sanitize_csv_cell(val: Any) -> str:
    """Sanitize cell value to prevent CSV formula injection in spreadsheet software."""
    if val is None:
        return ""
    s = str(val).strip()
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s

def format_apollo_lead_row(lead: Dict[str, Any], account_email: str = "") -> List[str]:
    """
    Format a database lead row into the exact 75-column Apollo contact export list.
    Handles raw_enrichment_data when available, falling back to core lead columns.
    """
    raw_match = lead.get("raw_enrichment_data") or {}
    if isinstance(raw_match, str):
        try:
            raw_match = json.loads(raw_match)
        except Exception:
            raw_match = {}

    org = raw_match.get("organization") or {}

    # 1. Names
    first_name = raw_match.get("first_name") or lead.get("first_name") or ""
    last_name = raw_match.get("last_name") or lead.get("last_name") or ""
    if not first_name and not last_name and lead.get("name"):
        parts = str(lead["name"]).strip().split(None, 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

    # 2. Title & Company
    title = raw_match.get("title") or lead.get("job_title") or ""
    comp_name = org.get("name") or lead.get("company") or ""

    # 3. Email & Status
    email = (raw_match.get("email") or lead.get("email") or "").strip()
    raw_status = (raw_match.get("email_status") or lead.get("email_status") or "").strip().lower()
    if raw_status == "verified":
        email_status = "Verified"
    elif raw_status == "unavailable":
        email_status = "Unavailable"
    elif raw_status:
        email_status = raw_status.capitalize()
    else:
        email_status = "Verified" if email else "Unavailable"

    primary_email_source = "Apollo" if email else ""
    primary_email_verification_source = "Apollo" if email else ""
    email_confidence = str(raw_match.get("extrapolated_email_confidence") or "")
    
    # Catch-all status
    is_catchall = raw_match.get("email_domain_catchall")
    if email:
        primary_email_catchall = "Catch-all" if is_catchall else "Not Catch-all"
    else:
        primary_email_catchall = ""

    # Last verified at
    enriched_at = lead.get("enriched_at")
    if isinstance(enriched_at, datetime):
        last_verified_at = enriched_at.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    elif enriched_at:
        last_verified_at = str(enriched_at)
    elif email:
        last_verified_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    else:
        last_verified_at = ""

    # 4. Seniority & Departments
    seniority = raw_match.get("seniority") or ""
    depts = ", ".join(raw_match.get("departments") or [])
    sub_depts = ", ".join(raw_match.get("subdepartments") or [])

    # 5. Owners & Lists
    owner = account_email or lead.get("account_used") or ""
    batch_name = lead.get("batch") or ""

    # 6. Phone Numbers
    corp_phone = org.get("sanitized_phone") or org.get("phone") or lead.get("company_phone") or ""

    # 7. Firmographics
    num_employees = org.get("estimated_num_employees") or lead.get("employee_count") or ""
    industry = org.get("industry") or lead.get("industry") or ""
    
    # Keywords
    kw = org.get("keywords") or lead.get("keywords")
    if isinstance(kw, str):
        try:
            kw = json.loads(kw)
        except Exception:
            pass
    if isinstance(kw, list):
        keywords = ", ".join([str(k) for k in kw if k])
    else:
        keywords = str(kw or "")

    # Links
    person_linkedin = raw_match.get("linkedin_url") or lead.get("linkedin_url") or ""
    
    domain = org.get("primary_domain") or lead.get("company_domain") or ""
    website = org.get("website_url") or lead.get("website_link") or ""
    if not website and domain:
        website = f"https://{domain}"

    comp_linkedin = org.get("linkedin_url") or lead.get("company_linkedin_url") or ""
    fb_url = org.get("facebook_url") or raw_match.get("facebook_url") or ""
    tw_url = org.get("twitter_url") or raw_match.get("twitter_url") or ""

    # Locations
    person_addr = raw_match.get("formatted_address") or lead.get("location") or ""
    person_city = raw_match.get("city") or ""
    person_state = raw_match.get("state") or ""
    person_country = raw_match.get("country") or ""

    comp_addr = org.get("raw_address") or lead.get("hq_address") or ""
    comp_city = org.get("city") or ""
    comp_state = org.get("state") or ""
    comp_country = org.get("country") or ""

    # Tech Stack
    tech = org.get("current_technologies") or lead.get("tech_stack")
    if isinstance(tech, str):
        try:
            tech = json.loads(tech)
        except Exception:
            pass
    if isinstance(tech, list):
        tech_names = [t.get("name") if isinstance(t, dict) else str(t) for t in tech if t]
        technologies = ", ".join(tech_names)
    else:
        technologies = str(tech or "")

    # Revenue & Funding
    annual_revenue = org.get("organization_revenue_printed") or org.get("annual_revenue_printed") or lead.get("annual_revenue") or ""
    total_funding = org.get("total_funding_printed") or org.get("total_funding") or ""
    latest_funding = org.get("latest_funding_round_date") or ""
    latest_funding_amount = org.get("latest_funding_amount") or ""
    last_raised_at = org.get("last_raised_at") or ""

    parent_org = org.get("parent_organization") or {}
    parent_company = parent_org.get("name") or ""
    parent_company_id = parent_org.get("id") or ""

    # Codes
    sic_codes = ", ".join([str(s) for s in (org.get("sic_codes") or [])])
    naics_codes = ", ".join([str(s) for s in (org.get("naics_codes") or [])])

    # Apollo Identifiers
    contact_id = raw_match.get("id") or lead.get("apollo_id") or ""
    account_id = raw_match.get("account_id") or org.get("id") or ""
    record_id = contact_id

    row = [
        sanitize_csv_cell(first_name),
        sanitize_csv_cell(last_name),
        sanitize_csv_cell(title),
        sanitize_csv_cell(comp_name),
        sanitize_csv_cell(comp_name),
        sanitize_csv_cell(email),
        sanitize_csv_cell(email_status),
        sanitize_csv_cell(primary_email_source),
        sanitize_csv_cell(primary_email_verification_source),
        sanitize_csv_cell(email_confidence),
        sanitize_csv_cell(primary_email_catchall),
        sanitize_csv_cell(last_verified_at),
        sanitize_csv_cell(seniority),
        sanitize_csv_cell(depts),
        sanitize_csv_cell(sub_depts),
        sanitize_csv_cell(owner),
        "",  # Work Direct Phone
        "",  # Home Phone
        "",  # Mobile Phone
        sanitize_csv_cell(corp_phone),
        "",  # Other Phone
        "false",  # Do Not Call
        "Cold",   # Stage
        sanitize_csv_cell(batch_name),  # Lists
        "",  # Last Contacted
        sanitize_csv_cell(owner),       # Account Owner
        sanitize_csv_cell(num_employees),
        sanitize_csv_cell(industry),
        sanitize_csv_cell(keywords),
        sanitize_csv_cell(person_linkedin),
        sanitize_csv_cell(website),
        sanitize_csv_cell(comp_linkedin),
        sanitize_csv_cell(fb_url),
        sanitize_csv_cell(tw_url),
        sanitize_csv_cell(person_addr),
        sanitize_csv_cell(person_city),
        sanitize_csv_cell(person_state),
        sanitize_csv_cell(person_country),
        sanitize_csv_cell(comp_addr),
        sanitize_csv_cell(comp_city),
        sanitize_csv_cell(comp_state),
        sanitize_csv_cell(comp_country),
        sanitize_csv_cell(corp_phone),
        sanitize_csv_cell(technologies),
        sanitize_csv_cell(annual_revenue),
        sanitize_csv_cell(total_funding),
        sanitize_csv_cell(latest_funding),
        sanitize_csv_cell(latest_funding_amount),
        sanitize_csv_cell(last_raised_at),
        sanitize_csv_cell(parent_company),
        sanitize_csv_cell(parent_company_id),
        "",       # Email Sent
        "false",  # Email Open
        "false",  # Email Bounced
        "false",  # Replied
        "false",  # Demoed
        sanitize_csv_cell(org.get("retail_location_count") or ""),
        sanitize_csv_cell(sic_codes),
        sanitize_csv_cell(naics_codes),
        sanitize_csv_cell(contact_id),
        sanitize_csv_cell(account_id),
        sanitize_csv_cell(record_id),
        "",  # Secondary Email
        "",  # Secondary Email Source
        "",  # Secondary Email Status
        "",  # Secondary Email Verification Source
        "",  # Tertiary Email
        "",  # Tertiary Email Source
        "",  # Tertiary Email Status
        "",  # Tertiary Email Verification Source
        "",  # Primary Intent Topic
        "",  # Primary Intent Score
        "",  # Secondary Intent Topic
        "",  # Secondary Intent Score
        ""   # account stage
    ]

    return row
