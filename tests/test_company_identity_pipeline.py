import unittest

from backend.company_identity import (
    AMBIGUOUS,
    EXISTING_CONFIRMED,
    NEW_CONFIRMED,
    UNRESOLVED,
    CompanyDecision,
    MySQLCompanyRepository,
    classify_companies,
    conservative_company_key,
    partition_people_by_company_decision,
)


class FakeRepository:
    def __init__(self, exact=None, domains=None, candidates=None):
        self.exact = exact or {}
        self.domains = set(domains or [])
        self.candidates = candidates or {}

    def exact_matches(self, names):
        return {name: self.exact[name] for name in names if name in self.exact}

    def existing_domains(self, domains):
        return {domain for domain in domains if domain in self.domains}

    def candidate_matches(self, name, limit=5):
        return list(self.candidates.get(name, []))[:limit]


class FakeJev:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def decide(self, cases):
        self.calls.append(cases)
        return self.answers


class RecordingCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))

    def executemany(self, sql, params):
        self.calls.append((" ".join(sql.split()), tuple(params)))

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self):
        self.cursor_instance = RecordingCursor()

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        pass


class TestCompanyIdentityPipeline(unittest.TestCase):
    def test_conservative_normalization_removes_legal_suffix_only(self):
        self.assertEqual(conservative_company_key("Acme Technologies, Inc."), "acme technologies")
        self.assertNotEqual(
            conservative_company_key("Acme Technologies"),
            conservative_company_key("Acme Solutions"),
        )

    def test_exact_company_match_is_existing(self):
        repo = FakeRepository(exact={"Acme, Inc.": {"canonical_name": "Acme", "domain": "acme.com"}})
        result = classify_companies(["Acme, Inc."], repo, resolver=lambda _names: {})
        self.assertEqual(result["Acme, Inc."].status, EXISTING_CONFIRMED)
        self.assertGreaterEqual(result["Acme, Inc."].confidence, 0.95)

    def test_verified_domain_distinguishes_existing_and_new(self):
        repo = FakeRepository(domains={"known.com"})
        resolved = {
            "Known Company": {"company_domain": "known.com", "source": "clearbit_brand", "verified": True},
            "New Company": {"company_domain": "newco.com", "source": "dns_candidate", "verified": True},
        }
        result = classify_companies(
            ["Known Company", "New Company"], repo, resolver=lambda _names: resolved
        )
        self.assertEqual(result["Known Company"].status, EXISTING_CONFIRMED)
        self.assertEqual(result["New Company"].status, NEW_CONFIRMED)

    def test_deterministic_fallback_is_never_confirmed_new(self):
        resolved = {
            "Guess Ltd": {
                "company_domain": "guess.com",
                "source": "deterministic_candidate",
                "verified": False,
            }
        }
        result = classify_companies(
            ["Guess Ltd"], FakeRepository(), resolver=lambda _names: resolved
        )
        self.assertEqual(result["Guess Ltd"].status, UNRESOLVED)

    def test_name_candidate_is_ambiguous_not_existing(self):
        repo = FakeRepository(
            candidates={
                "Amazon Web Services Consulting": [
                    {"canonical_name": "Amazon Web Services", "domain": "aws.amazon.com"}
                ]
            }
        )
        result = classify_companies(
            ["Amazon Web Services Consulting"], repo, resolver=lambda _names: {}
        )
        self.assertEqual(result["Amazon Web Services Consulting"].status, AMBIGUOUS)

    def test_jev_receives_only_ambiguous_cases(self):
        repo = FakeRepository(
            exact={"Exact Co": {"canonical_name": "Exact Co", "domain": "exact.com"}},
            candidates={"Possible Co": [{"canonical_name": "Possible Company", "domain": "possible.com"}]},
        )
        jev = FakeJev(
            {
                "Possible Co": {
                    "choice": "candidate_0",
                    "confidence": 0.94,
                }
            }
        )
        result = classify_companies(
            ["Exact Co", "Possible Co", "Unknown Co"],
            repo,
            resolver=lambda _names: {},
            jev_decider=jev,
        )
        self.assertEqual(len(jev.calls), 1)
        self.assertEqual([case["company_name"] for case in jev.calls[0]], ["Possible Co"])
        self.assertEqual(result["Possible Co"].status, EXISTING_CONFIRMED)
        self.assertEqual(result["Unknown Co"].status, UNRESOLVED)

    def test_partition_holds_unresolved_instead_of_counting_it_existing(self):
        decisions = {
            "Known": classify_companies(
                ["Known"],
                FakeRepository(exact={"Known": {"canonical_name": "Known"}}),
                resolver=lambda _names: {},
            )["Known"],
            "New": classify_companies(
                ["New"],
                FakeRepository(),
                resolver=lambda _names: {
                    "New": {
                        "company_domain": "new.example",
                        "source": "dns_candidate",
                        "verified": True,
                    }
                },
            )["New"],
            "Unknown": classify_companies(
                ["Unknown"], FakeRepository(), resolver=lambda _names: {}
            )["Unknown"],
        }
        people = [
            {"id": "1", "organization_name": "Known"},
            {"id": "2", "organization_name": "New"},
            {"id": "3", "organization_name": "Unknown"},
        ]
        accepted, counts = partition_people_by_company_decision(people, decisions)
        self.assertEqual([person["id"] for person, _decision in accepted], ["2"])
        self.assertEqual(counts[EXISTING_CONFIRMED], 1)
        self.assertEqual(counts[UNRESOLVED], 1)

    def test_jev_requires_both_confidence_and_choice_probability(self):
        repo = FakeRepository(
            candidates={"Possible Co": [{"canonical_name": "Possible Company", "domain": "possible.com"}]}
        )
        jev = FakeJev(
            {"Possible Co": {"choice": "candidate_0", "confidence": 0.96, "probability": 0.61}}
        )
        result = classify_companies(
            ["Possible Co"], repo, resolver=lambda _names: {}, jev_decider=jev
        )
        self.assertEqual(result["Possible Co"].status, AMBIGUOUS)

    def test_mysql_candidate_lookup_is_batched_and_index_friendly(self):
        connection = RecordingConnection()
        repository = MySQLCompanyRepository(connection, active_batch="pilot")

        repository.candidate_matches_batch(["Bizerba", "Dynamic Machinery"])

        calls = connection.cursor_instance.calls
        self.assertEqual(len(calls), 3)
        self.assertTrue(any("FROM emails" in sql for sql, _params in calls))
        for sql, params in calls:
            self.assertNotIn("LOWER(", sql)
            for value in params:
                if isinstance(value, str) and value.endswith("%"):
                    self.assertFalse(value.startswith("%"))

    def test_mysql_exact_name_and_domain_lookups_keep_indexed_columns_bare(self):
        connection = RecordingConnection()
        repository = MySQLCompanyRepository(connection, active_batch="pilot")

        repository.exact_matches(["Bizerba, Inc.", "Dynamic Machinery"])
        repository.existing_domains(["bizerba.com", "dynamicmachinery.com"])

        calls = connection.cursor_instance.calls
        self.assertGreaterEqual(len(calls), 9)
        for sql, _params in calls:
            self.assertNotIn("LOWER(", sql)
            self.assertNotIn("TRIM(", sql)

    def test_classifier_uses_one_batch_candidate_lookup(self):
        class BatchRepository(FakeRepository):
            def __init__(self):
                super().__init__()
                self.batch_calls = []

            def candidate_matches_batch(self, names, limit=5):
                self.batch_calls.append((list(names), limit))
                return {name: [] for name in names}

        repository = BatchRepository()
        classify_companies(
            ["Bizerba", "Dynamic Machinery"],
            repository,
            resolver=lambda _names: {},
        )
        self.assertEqual(repository.batch_calls, [(["Bizerba", "Dynamic Machinery"], 5)])

    def test_alias_upsert_qualifies_target_confidence_column(self):
        connection = RecordingConnection()
        repository = MySQLCompanyRepository(connection, active_batch="pilot")
        decision = CompanyDecision(
            company_name="Acme Tech",
            normalized_name="acme tech",
            status=EXISTING_CONFIRMED,
            confidence=0.95,
            canonical_name="Acme Technologies",
            canonical_domain="acme.example",
            source="jev_candidate",
        )

        repository.record_decisions([decision])

        alias_sql = next(
            sql for sql, _params in connection.cursor_instance.calls
            if "INSERT INTO company_alias" in sql
        )
        self.assertIn(
            "GREATEST(company_alias.confidence, VALUES(confidence))",
            alias_sql,
        )


if __name__ == "__main__":
    unittest.main()
