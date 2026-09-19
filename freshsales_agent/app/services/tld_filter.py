from __future__ import annotations

DELETE_TLDS = {
    ".ee", ".fi", ".fr", ".hr", ".hu", ".ie", ".lv", ".no", ".pt", ".se", ".uk",
    ".at", ".be", ".ca", ".ch", ".de", ".lu", ".bg", ".cy", ".cz", ".dk", ".es",
    ".gr", ".is", ".it", ".li", ".lt", ".mt", ".nl", ".pl", ".ro", ".si", ".sk"
}


def is_blocked_tld(email: str | None) -> bool:
    """
    Checks if an email's domain matches any of the 33 country-code TLDs.
    Handles direct TLDs (e.g. user@company.fr) and compound TLDs (e.g. user@company.co.uk).
    """
    if not email or "@" not in email:
        return False

    domain = email.strip().lower().split("@")[-1]

    for tld in DELETE_TLDS:
        # Exact match or suffix match with dot boundary
        if domain == tld.lstrip(".") or domain.endswith(tld):
            return True

    return False


def filter_by_tld(
    records: list[dict[str, str]],
    email_col: str = "email",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Splits records into (valid_records, excluded_tld_records).
    """
    valid: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []

    for row in records:
        email = row.get(email_col, "")
        if is_blocked_tld(email):
            excluded.append(row)
        else:
            valid.append(row)

    return valid, excluded
