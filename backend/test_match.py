import argparse

from api import (
    company_matches,
    domains_equivalent,
    resolve_contact_domains,
)


DETERMINISTIC_CASES = [
    ("Liquid AI", "liquid.ai", True),
    ("Liquid AI", "liquid.liquid.ai", True),
    ("VE GROUP", "ve-group.com", True),
    ("Fito Ag.", "fitoag.com.br", True),
    (
        "WSO Worldwide Security Options",
        "wso-security.com",
        True,
    ),
    (
        "Simplon Fahrrad GmbH",
        "simplon.com",
        True,
    ),
    (
        "Global Technology",
        "globalconstruction.com",
        False,
    ),
    (
        "AI Solutions",
        "aidental.com",
        False,
    ),
]


DOMAIN_EQUIVALENCE_CASES = [
    (
        "liquid.ai",
        "liquid.liquid.ai",
        True,
    ),
    (
        "example.com",
        "portal.example.com",
        True,
    ),
    (
        "example.com",
        "example.org",
        False,
    ),
]


def run_local_tests():
    failures = 0

    print(
        "Deterministic company/domain tests"
    )
    print("=" * 70)

    for (
        company,
        domain,
        expected,
    ) in DETERMINISTIC_CASES:

        actual = company_matches(
            company,
            domain,
        )

        status = (
            "PASS"
            if actual == expected
            else "FAIL"
        )

        print(
            f"{status:4} | "
            f"{company!r} <-> "
            f"{domain!r} "
            f"=> {actual}"
        )

        if actual != expected:
            failures += 1


    print()
    print(
        "Resolved-domain equivalence tests"
    )
    print("=" * 70)


    for (
        left,
        right,
        expected,
    ) in DOMAIN_EQUIVALENCE_CASES:

        actual = domains_equivalent(
            left,
            right,
        )

        status = (
            "PASS"
            if actual == expected
            else "FAIL"
        )

        print(
            f"{status:4} | "
            f"{left!r} <-> "
            f"{right!r} "
            f"=> {actual}"
        )

        if actual != expected:
            failures += 1


    print()

    if failures:
        raise SystemExit(
            f"{failures} test(s) failed."
        )

    print(
        "All local tests passed."
    )


def run_live_resolver_test():
    print()
    print(
        "Live Name + Company domain resolver"
    )
    print("=" * 70)

    name = input(
        "Person name: "
    ).strip()

    company = input(
        "Company name: "
    ).strip()

    location = input(
        "Company location (optional): "
    ).strip()


    result = resolve_contact_domains(
        name,
        company,
        location,
    )


    print()
    print("Resolver status:")

    print(
        "  status:",
        result.get(
            "status"
        ),
    )

    print(
        "  method:",
        result.get(
            "method"
        ),
    )

    print(
        "  confidence:",
        result.get(
            "confidence"
        ),
    )


    if (
        "coverage_complete"
        in result
    ):
        print(
            "  coverage_complete:",
            result.get(
                "coverage_complete"
            ),
        )


    domains = result.get(
        "domains",
        [],
    )


    print()
    print("Resolved domains:")

    if not domains:
        print("  None")
    else:
        for item in domains:
            print(
                "  - "
                f"{item.get('domain')} | "
                f"{item.get('type')} | "
                "confidence="
                f"{item.get('confidence')}"
            )


    evidence_urls = result.get(
        "evidence_urls",
        [],
    )


    if evidence_urls:
        print()
        print("Web evidence:")

        for url in evidence_urls:
            print(
                f"  - {url}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--resolve",
        action="store_true",
        help=(
            "Run one live Name + Company "
            "domain-resolution test after "
            "the local tests."
        ),
    )

    args = parser.parse_args()

    run_local_tests()

    if args.resolve:
        run_live_resolver_test()
