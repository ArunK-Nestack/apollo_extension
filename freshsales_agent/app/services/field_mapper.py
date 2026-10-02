from __future__ import annotations

from typing import Any
from app.models import ContactPayload


EXCEL_ERROR_VALUES = {
    "#error!", "#n/a", "#ref!", "#value!", "#div/0!", "#num!", "#name?", "#null!",
    "nan", "none", "null", "undefined",
}


def map_row_to_contact_payload(
    row: dict[str, str],
    tag: str,
    default_owner_id: str | None = None,
) -> ContactPayload:
    """
    Maps an Apollo + MillionVerifier CSV row into a structured Freshsales ContactPayload.
    """
    # Normalize row keys for case-insensitive lookup
    normalized: dict[str, str] = {}
    for k, v in row.items():
        if k is not None and str(v).strip() != "":
            val_str = str(v).strip()
            if val_str.lower() not in EXCEL_ERROR_VALUES:
                normalized[str(k).strip().lower()] = val_str

    def get_val(*keys: str) -> str | None:
        for key in keys:
            norm_k = key.strip().lower()
            if norm_k in normalized:
                val = normalized[norm_k]
                if val and val.lower() not in EXCEL_ERROR_VALUES:
                    return val
        return None

    email = get_val("email", "contact: emails", "email address", "contact: emails (primary)")
    if not email:
        raise ValueError(f"Missing required email field in row: {row}")


    # Standard Contact fields
    first_name = get_val("first name", "contact: first name")
    last_name = get_val("last name", "contact: last name")
    job_title = get_val("title", "job title", "contact: job title")
    company_name = get_val("company name", "contact: company name", "company")
    work_number = get_val("work direct phone", "work phone", "contact: work")
    mobile_number = get_val("mobile phone", "contact: other phone numbers", "phone")
    address = get_val("company address", "contact: address")
    city = get_val("city", "contact: city")
    state = get_val("state", "contact: state")
    country = get_val("country", "contact: country")
    owner_id = get_val("owner", "contact: owner") or default_owner_id

    # Custom Contact fields
    custom_fields: dict[str, Any] = {}

    quality = get_val("quality", "million quality", "contact: million quality")
    if quality:
        custom_fields["cf_million_quality"] = quality

    result = get_val("result", "million result", "contact: million result")
    if result:
        custom_fields["cf_million_result"] = result

    email_status = get_val("email status", "contact: apollo email status")
    if email_status:
        custom_fields["cf_apollo_email_status"] = email_status

    seniority = get_val("seniority", "contact: seniority")
    if seniority:
        custom_fields["cf_seniority"] = seniority

    departments = get_val("departments", "contact: departments", "department")
    if departments:
        custom_fields["cf_departments"] = departments

    contact_owner = get_val("contact owner", "contact: apollo login owner", "apollo login owner")
    if contact_owner:
        custom_fields["cf_apollo_login_owner"] = contact_owner

    corp_phone = get_val("corporate phone", "contact: corporate phone")
    if corp_phone:
        custom_fields["cf_corporate_phone"] = corp_phone

    industry = get_val("industry", "contact: contact industry")
    if industry:
        custom_fields["cf_contact_industry"] = industry

    linkedin = get_val("person linkedin url", "contact: linkedin", "linkedin url")
    if linkedin:
        custom_fields["cf_linkedin"] = linkedin

    facebook = get_val("facebook url", "contact: facebook")
    if facebook:
        custom_fields["cf_facebook"] = facebook

    twitter = get_val("twitter url", "contact: twitter")
    if twitter:
        custom_fields["cf_twitter"] = twitter

    revenue = get_val("annual revenue", "contact: revenue", "revenue")
    if revenue:
        custom_fields["cf_revenue"] = revenue

    apollo_contact_id = get_val("apollo contact id", "contact: apollo contact id")
    if apollo_contact_id:
        custom_fields["cf_apollo_contact_id"] = apollo_contact_id

    hierarchy = get_val("hierarchy", "contact: hierarchy")
    if hierarchy:
        custom_fields["cf_hierarchy"] = hierarchy

    # Sales Account (Company) fields
    sales_account: dict[str, Any] = {}
    company_name_emails = get_val("company name for emails", "account: company name") or company_name
    if company_name_emails:
        sales_account["name"] = company_name_emails

    account_industry_2 = get_val("industry 2", "account: sub industry")
    if account_industry_2:
        sales_account.setdefault("custom_field", {})["cf_sub_industry"] = account_industry_2

    keywords = get_val("keywords", "account: keywords")
    if keywords:
        sales_account.setdefault("custom_field", {})["cf_keywords"] = keywords

    website = get_val("website", "account: website")
    if website:
        sales_account["website"] = website

    company_linkedin = get_val("company linkedin url", "account: linkedin")
    if company_linkedin:
        sales_account.setdefault("custom_field", {})["cf_linkedin"] = company_linkedin

    company_phone = get_val("company phone", "account: corporate phone")
    if company_phone:
        sales_account["phone"] = company_phone

    technologies = get_val("technologies", "account: technologies")
    if technologies:
        sales_account.setdefault("custom_field", {})["cf_technologies"] = technologies

    employees = get_val("# employees", "account: number of employees", "employees")
    if employees:
        try:
            sales_account["number_of_employees"] = int(employees.replace(",", ""))
        except ValueError:
            pass

    total_funding = get_val("total funding", "account: total funding")
    if total_funding:
        sales_account.setdefault("custom_field", {})["cf_total_funding"] = total_funding

    apollo_account_id = get_val("apollo account id", "account: apollo account id")
    if apollo_account_id:
        sales_account.setdefault("custom_field", {})["cf_apollo_account_id"] = apollo_account_id

    return ContactPayload(
        email=email.lower().strip(),
        first_name=first_name,
        last_name=last_name,
        job_title=job_title,
        company_name=company_name,
        work_number=work_number,
        mobile_number=mobile_number,
        address=address,
        city=city,
        state=state,
        country=country,
        owner_id=owner_id,
        tags=[tag],
        custom_fields=custom_fields,
        sales_account=sales_account,
    )
