"""Join Freshsales account IDs to created contacts in the sync audits.

Input snapshot comes from fetch_freshsales_recent_accounts.py. This reports
evidence and leaves attribution to the caller; it does not change the ledger.
"""

import csv
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "exports" / "freshsales_reports"


def rows(path):
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
        yield from csv.DictReader(handle)


def created_emails(entry):
    path = Path(entry.get("audit_file") or "")
    if not path.is_file():
        return set()
    return {r.get("email", "").strip().lower() for r in rows(path)
            if r.get("action", "").strip().lower() == "created" and not r.get("error_reason")}


def source_emails(entry):
    path = Path(entry.get("file_path") or "")
    if not path.is_file():
        return set()
    return {str(r.get("Email") or r.get("email") or "").strip().lower()
            for r in rows(path)} - {""}


def contact_ids_by_email():
    result = defaultdict(set)
    for path in glob.glob(str(REPORTS / "*created_contacts*.csv")):
        for row in rows(path):
            email = (row.get("Contact : Emails") or "").strip().lower()
            contact_id = str(row.get("Contact : id") or "").strip()
            if email and contact_id:
                result[email].add(contact_id)
    return result


def analyze(ledger, snapshot):
    runs = list(ledger.items())
    email_ids = contact_ids_by_email()
    emails = {key: created_emails(entry) for key, entry in runs}
    # The first audit is a consolidated file updated after the retry. Its
    # retry-created contacts belong to the second run, not both runs.
    if len(runs) >= 2 and runs[0][1].get("tag") == runs[1][1].get("tag"):
        emails[runs[0][0]] -= emails[runs[1][0]]
    ids = {key: {cid for email in emails[key] for cid in email_ids[email]}
           for key, _ in runs}
    sources = {key: source_emails(entry) for key, entry in runs}
    contact_email = snapshot["contacts"]
    counts = defaultdict(Counter)
    examples = defaultdict(list)
    for account in snapshot["accounts"]:
        linked_ids = set(account.get("contact_ids") or [])
        linked_emails = {contact_email.get(cid, "").lower() for cid in linked_ids} - {""}
        matches = [key for key, entry in runs if linked_ids & ids[key]
                   or (not ids[key] and linked_emails & sources[key]
                       and entry.get("tag") in account.get("tags", []))]
        if not matches:
            continue
        date = account["created_at"][:10]
        for key in matches:
            counts[key][date] += 1
            if len(examples[key]) < 3:
                examples[key].append(account["id"])
        if len(matches) > 1:
            counts["__ambiguous__"][date] += 1
    return emails, ids, counts, examples


def confirm_tagged_run(entry, snapshot, created_date):
    """Confirm accounts created on a date with this run's tag and source contact."""
    source = source_emails(entry)
    if not source:
        raise ValueError("source file has no emails")
    tag = entry["tag"]
    contacts = snapshot["contacts"]
    tagged = []
    untagged_source = []
    for account in snapshot["accounts"]:
        if account["created_at"][:10] != created_date:
            continue
        linked = {contacts.get(i, "").lower() for i in account.get("contact_ids", [])}
        has_source = bool(linked & source)
        has_tag = tag in account.get("tags", [])
        if has_tag:
            if not has_source:
                raise ValueError(f"tagged account {account['id']} has no source contact")
            tagged.append(account)
        elif has_source:
            untagged_source.append(account["id"])
    if untagged_source:
        raise ValueError(f"{len(untagged_source)} source-linked accounts lack the run tag")
    return sorted({a["id"] for a in tagged})


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--confirm-run-index", type=int, help="1-based ledger run index")
    parser.add_argument("--created-date", help="YYYY-MM-DD account creation date")
    parser.add_argument("--apply", action="store_true", help="Store verified account IDs in the ledger")
    args = parser.parse_args()
    ledger = json.loads((ROOT / "config" / "freshsales_synced_batches.json").read_text(encoding="utf-8"))
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    if args.confirm_run_index:
        if not args.created_date:
            parser.error("--created-date is required with --confirm-run-index")
        key = list(ledger)[args.confirm_run_index - 1]
        entry = ledger[key]
        if sum(v.get("tag") == entry.get("tag") for v in ledger.values()) != 1:
            raise ValueError("run tag is shared; cannot confirm this way")
        account_ids = confirm_tagged_run(entry, snapshot, args.created_date)
        print(json.dumps({"run": key, "created_date": args.created_date,
                          "confirmed_account_count": len(account_ids)}))
        if args.apply:
            entry["account_ids"] = account_ids
            entry["account_reconciliation_status"] = "confirmed"
            entry["account_reconciliation_evidence"] = {
                "created_date": args.created_date,
                "method": "Freshsales account ID + created_at + exact run tag + linked source contact email",
            }
            (ROOT / "config" / "freshsales_synced_batches.json").write_text(
                json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    emails, ids, counts, examples = analyze(ledger, snapshot)
    for key, entry in ledger.items():
        print(json.dumps({"run": key, "audit_created_emails": len(emails[key]),
                          "exported_contact_ids": len(ids[key]),
                          "linked_accounts_by_creation_date": counts[key],
                          "sample_account_ids": examples[key]}))
    print(json.dumps({"ambiguous_accounts_by_date": counts["__ambiguous__"]}))


if __name__ == "__main__":
    main()
