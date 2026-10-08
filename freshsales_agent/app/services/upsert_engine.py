from __future__ import annotations

from typing import Any
from app.models import ContactPayload


def resolve_delta_update(
    incoming: ContactPayload,
    existing_crm_record: dict[str, Any],
    tag: str,
) -> dict[str, Any]:
    """
    Builds non-overwrite update payload for an existing Freshsales contact:
    - Never overwrites existing non-empty fields in CRM.
    - Fills only fields that are currently None or empty string in CRM.
    - Appends the run tag to the existing tag list.
    """
    contact_data = existing_crm_record.get("contact", existing_crm_record)
    existing_custom = contact_data.get("custom_field", {}) or {}

    delta_payload: dict[str, Any] = {}

    # 1. Standard fields check (only write if CRM field is empty/null)
    std_fields = {
        "first_name": incoming.first_name,
        "last_name": incoming.last_name,
        "job_title": incoming.job_title,
        "company_name": incoming.company_name,
        "work_number": incoming.work_number,
        "mobile_number": incoming.mobile_number,
        "address": incoming.address,
        "city": incoming.city,
        "state": incoming.state,
        "country": incoming.country,
    }

    for key, new_val in std_fields.items():
        if new_val:
            curr_val = contact_data.get(key)
            if curr_val is None or str(curr_val).strip() == "":
                delta_payload[key] = new_val

    # 2. Custom fields check
    delta_custom: dict[str, Any] = {}
    for cf_key, new_cf_val in incoming.custom_fields.items():
        if new_cf_val:
            curr_cf_val = existing_custom.get(cf_key)
            if curr_cf_val is None or str(curr_cf_val).strip() == "":
                delta_custom[cf_key] = new_cf_val

    if delta_custom:
        delta_payload["custom_field"] = delta_custom

    # 3. Always append run tag
    existing_tags = contact_data.get("tags") or []
    if isinstance(existing_tags, str):
        existing_tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
    elif not isinstance(existing_tags, list):
        existing_tags = []

    merged_tags = list(set(existing_tags + [tag]))
    delta_payload["tags"] = merged_tags

    return delta_payload
