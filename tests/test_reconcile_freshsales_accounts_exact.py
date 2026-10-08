import csv

from scripts.reconcile_freshsales_accounts_exact import confirm_tagged_run


def test_confirm_requires_account_tag_date_and_linked_source_email(tmp_path):
    source = tmp_path / "source.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Email"])
        writer.writeheader()
        writer.writerow({"Email": "a@example.com"})
    entry = {"file_path": str(source), "tag": "run-a"}
    snapshot = {
        "contacts": {"1": "a@example.com", "2": "b@example.com"},
        "accounts": [
            {"id": "42", "created_at": "2026-10-03T10:00:00+05:30", "tags": ["run-a"], "contact_ids": ["1"]},
            {"id": "43", "created_at": "2026-10-02T10:00:00+05:30", "tags": ["run-a"], "contact_ids": ["1"]},
            {"id": "44", "created_at": "2026-10-03T10:00:00+05:30", "tags": ["other"], "contact_ids": ["2"]},
        ],
    }
    assert confirm_tagged_run(entry, snapshot, "2026-10-03") == ["42"]
