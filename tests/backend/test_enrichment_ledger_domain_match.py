"""Ledger email discoveries must block matching on their company domain."""

from backend.api import ApolloContact, check_person_and_domains_in_crm_batch


class FakeCursor:
    def __init__(self, ledger_rows):
        self.ledger_rows = ledger_rows
        self.rows = []
        self.ledger_queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        if "FROM `batch_enrichment_ledger`" in sql:
            self.ledger_queries.append((sql, params))
            self.rows = self.ledger_rows
        else:
            self.rows = []

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, ledger_rows):
        self.cur = FakeCursor(ledger_rows)

    def cursor(self):
        return self.cur


def test_email_found_ledger_domain_is_existing_even_in_active_batch(monkeypatch):
    import backend.api as api

    monkeypatch.setattr(api, "get_target_table_schema", lambda _: {
        "table_name": "emails", "email_domain": "domain", "name": "full_name"
    })
    monkeypatch.setattr(api, "generate_candidate_domains", lambda _: [])
    monkeypatch.setattr(api, "_DNS_AVAILABLE", False)
    conn = FakeConnection([("ledger-only.example",)])
    contact = ApolloContact(
        key="lead", name="New Person", company="Ledger Only",
        job_title="Director", company_domain="ledger-only.example",
    )

    results = check_person_and_domains_in_crm_batch(
        [contact], {"lead": "ledger-only.example"}, connection=conn,
        active_batch="current_batch",
    )

    assert results["lead"]["exists"] is True
    assert results["lead"]["ignored"] is True
    assert results["lead"]["guardrail_status"] == "domain_already_in_db"
    sql, params = conn.cur.ledger_queries[0]
    assert "email_found" in sql
    assert "current_batch" not in params
