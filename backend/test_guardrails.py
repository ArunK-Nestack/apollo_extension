"""
Test Suite: Guardrails & AI Job Title Hierarchy Evaluation
Based on job-title-hierarchy-guardrails (1).md

Verifies:
  1. Company Contact Cap (Max 5 contacts per company -> company_limit_exceeded)
  2. Seniority Tiers 1-4 (Required Decision-Makers: Founder, C-Suite, VP, Director)
  3. Manager Tiers 5-6 (Included by default as Required: Engineering Manager, Product Manager)
  4. Role-Type Tagging (decision_maker vs evaluator based on company size)
  5. Keyword Overrides for Tiers 5/6 (AI/Automation upgrades role_type to decision_maker)
  6. Regional Synonym Mappings (AU/NZ/SG MD -> Tier 2 CEO, Head of X -> Tier 3/4)
  7. Functional Relevance Filtering (Exclude HR, Legal, pure Sales/Marketing, Customer Support)
  8. Tier 7 Explicit Exclusions (Intern, Recruiter, Analyst, Associate, Coordinator, SDR)
"""
import json
import os
import sys

# Ensure backend root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api import evaluate_job_title_with_ai, match_apollo, ApolloMatchRequest, ApolloContact


def test_guardrail_1_domain_check():
    print("\n" + "=" * 75)
    print("TEST 1: Guardrail 1 (Target New Domains — Ignore Existing DB Domains)")
    print("=" * 75)

    # Contacts from companies whose domains are known to exist in DB (e.g. 'Deloitte' -> 'deloitte.com')
    # and a net-new company that does NOT exist in DB.
    contacts = [
        ApolloContact(
            key="test-existing-domain",
            name="Unknown Person 1",
            job_title="VP Engineering",
            company="Deloitte",
            location="New York, US",
            employee_count=50000,
        ),
        ApolloContact(
            key="test-new-domain",
            name="Unknown Person 2",
            job_title="VP Engineering",
            company="BrandNewUniqueAILabs9999",
            location="San Francisco, US",
            employee_count=30,
        ),
    ]

    req = ApolloMatchRequest(contacts=contacts, title_guardrail_enabled=False)
    resp = match_apollo(req)
    results = resp.get("results", {})
    summary = resp.get("summary", {})

    print(f"Total contacts sent: {len(contacts)}")
    print(f"Summary: {summary}")

    # Contact 1 (Deloitte) should be ignored because deloitte.com already exists in CRM DB
    res_deloitte = results["test-existing-domain"]
    assert res_deloitte.get("required") is False, "Expected required=False because domain exists in DB"
    assert res_deloitte.get("ignored") is True, "Expected ignored=True for existing domain"
    assert res_deloitte.get("guardrail_status") == "domain_already_in_db", "Expected guardrail_status=domain_already_in_db"
    print(f"  PASS | Deloitte => required={res_deloitte.get('required')}, ignored={res_deloitte.get('ignored')}, guardrail_status={res_deloitte.get('guardrail_status')}")

    # Contact 2 (BrandNewUniqueAILabs9999) is a net-new domain and title_guardrail_enabled=False => Required
    res_new = results["test-new-domain"]
    assert res_new.get("exists") is False, "Expected exists=False for non-CRM person"
    assert res_new.get("required") is True, "Expected required=True for new target domain"
    assert res_new.get("ignored") is False, "Expected ignored=False for new target domain"
    assert res_new.get("guardrail_status") == "qualified", "Expected qualified for new target domain"
    print(f"  PASS | BrandNewUniqueAILabs9999 => required={res_new.get('required')}, ignored={res_new.get('ignored')}, guardrail_status={res_new.get('guardrail_status')}")

    print("[PASS] Guardrail 1 (Target New Domains) test PASSED.")


def test_guardrail_2_toggle():
    print("\n" + "=" * 75)
    print("TEST 2: Guardrail 2 (AI Title Hierarchy Toggle Control)")
    print("=" * 75)

    contact_intern = ApolloContact(
        key="test-intern-new-domain",
        name="Intern User",
        job_title="Software Engineering Intern",
        company="AnotherBrandNewAIStartupXYZ",
        location="San Francisco, US",
        employee_count=25,
    )

    # 1. When title_guardrail_enabled=False -> Intern on new domain is auto-qualified as Required
    req_off = ApolloMatchRequest(contacts=[contact_intern], title_guardrail_enabled=False)
    resp_off = match_apollo(req_off)
    res_off = resp_off["results"]["test-intern-new-domain"]
    assert res_off["required"] is True, "Expected required=True when title guardrail is OFF"
    assert res_off["ignored"] is False, "Expected ignored=False when title guardrail is OFF"
    print(f"  PASS | Title Guardrail OFF => required={res_off['required']}, status={res_off['guardrail_status']}")

    # 2. When title_guardrail_enabled=True -> Intern is disqualified by 7-tier hierarchy (Tier 7)
    req_on = ApolloMatchRequest(contacts=[contact_intern], title_guardrail_enabled=True)
    resp_on = match_apollo(req_on)
    res_on = resp_on["results"]["test-intern-new-domain"]
    assert res_on["required"] is False, "Expected required=False when title guardrail is ON (Tier 7 intern excluded)"
    assert res_on["ignored"] is True, "Expected ignored=True when title guardrail is ON"
    assert res_on["guardrail_status"] == "disqualified_title", "Expected disqualified_title"
    print(f"  PASS | Title Guardrail ON => required={res_on['required']}, ignored={res_on['ignored']}, status={res_on['guardrail_status']}")

    print("[PASS] Guardrail 2 toggle test PASSED.")


def test_job_title_hierarchy_guardrails():
    print("\n" + "=" * 75)
    print("TEST 2: AI Job Title Hierarchy, Role-Type & Functional Relevance")
    print("=" * 75)

    # Test cases:
    # (name, job_title, company, location, employee_count, region, expected_required, expected_role_type_or_none, label)
    test_cases = [
        # --- TIER 1 & 2: Founders & C-Suite ---
        (
            "Alice Chen",
            "Founder & CEO",
            "Nova Robotics",
            "San Francisco, US",
            30,
            "US",
            True,
            "decision_maker",
            "Tier 1/2: Founder & CEO (<=50 emp -> decision_maker)",
        ),
        (
            "Marcus Vance",
            "Chief Technology Officer (CTO)",
            "Aether Dynamics",
            "Boston, US",
            500,
            "US",
            True,
            "decision_maker",
            "Tier 2: CTO at 500 emp (decision_maker)",
        ),

        # --- TIER 3 & 4: VP & Directors ---
        (
            "David Kim",
            "VP of Engineering",
            "HyperScale Data",
            "New York, US",
            200,
            "US",
            True,
            "decision_maker",
            "Tier 3: VP Engineering at 200 emp (decision_maker)",
        ),
        (
            "Sarah Jenkins",
            "Director of Product Management",
            "Fintech Dynamics",
            "London, UK",
            120,
            "",
            True,
            "decision_maker",
            "Tier 4: Director of Product (decision_maker)",
        ),

        # --- TIER 5 & 6: Managers (Required under new guardrails!) ---
        (
            "Carlos Gomez",
            "Engineering Manager",
            "CloudStack Systems",
            "Austin, US",
            250,
            "US",
            True,
            "evaluator",
            "Tier 5: Engineering Manager at 250 emp (evaluator, required=True)",
        ),
        (
            "Jessica Lee",
            "Product Manager",
            "Apex Software",
            "Seattle, US",
            180,
            "US",
            True,
            "evaluator",
            "Tier 6: Product Manager at 180 emp (evaluator, required=True)",
        ),
        (
            "Oliver Taylor",
            "Product Manager",
            "Startup Labs",
            "San Francisco, US",
            35,
            "US",
            True,
            "decision_maker",
            "Tier 6: Product Manager at <=50 emp startup (decision_maker, required=True)",
        ),

        # --- KEYWORD OVERRIDES FOR MANAGERS (Upgrade to decision_maker / high confidence) ---
        (
            "Priya Patel",
            "Engineering Manager, Applied AI",
            "Cognitive Automations",
            "Auckland, New Zealand",
            200,
            "NZ",
            True,
            "decision_maker",
            "Tier 5 with AI Override at 200 emp (role_type upgraded to decision_maker)",
        ),
        (
            "Kenji Sato",
            "Product Manager - Agentic Automation",
            "NextGen Robotics",
            "San Jose, US",
            300,
            "US",
            True,
            "decision_maker",
            "Tier 6 with Agentic/Automation Override (upgraded to decision_maker)",
        ),

        # --- REGIONAL SYNONYM MAPPINGS (AU, NZ, SG) ---
        (
            "Liam O'Connor",
            "Managing Director",
            "Sydney Cloud Solutions",
            "Sydney, Australia",
            80,
            "AU",
            True,
            "decision_maker",
            "Regional Tier 2: AU Managing Director (CEO equivalent)",
        ),
        (
            "Chloe Smith",
            "Head of Product",
            "Auckland FinTech",
            "Auckland, New Zealand",
            45,
            "NZ",
            True,
            "decision_maker",
            "Regional Tier 3/4: NZ Head of Product at startup (decision_maker)",
        ),
        (
            "Wei Zhang",
            "Country Manager",
            "DataSphere SG",
            "Singapore",
            100,
            "SG",
            True,
            "decision_maker",
            "Regional Tier 3: SG Country Manager (decision_maker)",
        ),

        # --- FUNCTIONAL EXCLUSIONS (Irrelevant functions -> required=False) ---
        (
            "Tom Miller",
            "Human Resources Director",
            "Global Logistics Hub",
            "Chicago, US",
            500,
            "US",
            False,
            None,
            "Functional Exclude: HR Director (Irrelevant Function)",
        ),
        (
            "Emily Watson",
            "Corporate Legal Counsel",
            "BioMed Tech",
            "Boston, US",
            200,
            "US",
            False,
            None,
            "Functional Exclude: Legal Counsel (Irrelevant Function)",
        ),
        (
            "Mark Davis",
            "VP of Sales",
            "Outbound Pro",
            "Atlanta, US",
            150,
            "US",
            False,
            None,
            "Functional Exclude: VP Sales (no RevOps/Tech)",
        ),

        # --- TIER 7 EXPLICIT EXCLUSIONS (IC/Junior/Admin -> required=False) ---
        (
            "Jake Taylor",
            "Software Engineer Intern",
            "CloudSphere Systems",
            "Austin, US",
            100,
            "US",
            False,
            None,
            "Tier 7 Explicit Exclude: Intern",
        ),
        (
            "Rachel Green",
            "Technical Talent Recruiter",
            "TalentForge",
            "Seattle, US",
            150,
            "US",
            False,
            None,
            "Tier 7 Explicit Exclude: Recruiter",
        ),
        (
            "Ashley Brown",
            "Sales Development Representative (SDR)",
            "LeadFlow SaaS",
            "Denver, US",
            90,
            "US",
            False,
            None,
            "Tier 7 Explicit Exclude: SDR",
        ),
        (
            "Brian Hall",
            "Marketing Coordinator",
            "OmniMedia",
            "New York, US",
            60,
            "US",
            False,
            None,
            "Tier 7 Explicit Exclude: Marketing Coordinator",
        ),
    ]

    passed = 0
    total = len(test_cases)

    for i, tc in enumerate(test_cases):
        name, title, company, loc, emp_count, reg, exp_req, exp_role, label = tc

        res = evaluate_job_title_with_ai(
            name=name,
            job_title=title,
            company=company,
            location=loc,
            employee_count=emp_count,
            region=reg,
            apollo_id=f"test-{i}",
        )

        actual_req = res.get("required")
        tier = res.get("tier")
        role_type = res.get("role_type")
        confidence = res.get("confidence")
        regional_synonym = res.get("regional_synonym_applied")
        reason = res.get("reason")

        req_match = (actual_req == exp_req)
        role_match = True if exp_role is None else (role_type == exp_role)

        case_passed = req_match and role_match
        status_str = "PASS" if case_passed else "FAIL"

        print(f"[{i+1:02d}/{total:02d}] {status_str} | [{label}]")
        print(f"       Title: '{title}' @ '{company}' ({emp_count or 'N/A'} emp, {loc})")
        print(f"       Expected: required={exp_req}, role_type={exp_role}")
        print(f"       Actual:   required={actual_req}, role_type={role_type}, tier={tier}, confidence={confidence}")
        if regional_synonym:
            print(f"       Regional Synonym: {regional_synonym}")
        print(f"       Reason:   {reason}\n")

        if case_passed:
            passed += 1

    print(f"Summary: {passed}/{total} hierarchy test cases passed.")
    assert passed == total, f"Some title test cases failed ({passed}/{total})"
    print("[PASS] AI Job title hierarchy guardrails PASSED perfectly!")


def test_batch_match_apollo_endpoint():
    print("\n" + "=" * 75)
    print("TEST 3: End-to-End Batch Matching with Guardrails (/match-apollo)")
    print("=" * 75)

    contacts = [
        ApolloContact(
            key="batch-1-ceo",
            name="Alice Chen",
            job_title="CEO",
            company="Alpha AI",
            location="San Francisco, US",
            employee_count=40,
        ),
        ApolloContact(
            key="batch-2-mgr",
            name="Bob Smith",
            job_title="Product Manager",
            company="Beta Corp",
            location="Seattle, US",
            employee_count=120,
        ),
        ApolloContact(
            key="batch-3-intern",
            name="Charlie Intern",
            job_title="Engineering Intern",
            company="Gamma Systems",
            location="Austin, US",
            employee_count=200,
        ),
    ]

    req = ApolloMatchRequest(contacts=contacts, title_guardrail_enabled=True)
    resp = match_apollo(req)

    results = resp.get("results", {})
    summary = resp.get("summary", {})

    print("Batch Results Summary:", summary)

    assert results["batch-1-ceo"]["required"] is True, "CEO should be required"
    assert results["batch-1-ceo"]["role_type"] == "decision_maker", "CEO at <=50 emp should be decision_maker"

    assert results["batch-2-mgr"]["required"] is True, "Product Manager should be required"
    assert results["batch-2-mgr"]["role_type"] == "evaluator", "Product Manager at 120 emp should be evaluator"

    assert results["batch-3-intern"]["required"] is False, "Intern should be excluded"
    assert results["batch-3-intern"]["guardrail_status"] == "disqualified_title", "Intern should have disqualified_title status"

    print("[PASS] Batch Match Apollo Endpoint test PASSED.")


def test_guardrail_3_indian_name_filter():
    print("\n" + "=" * 75)
    print("TEST 4: Guardrail 3 (Pure Indian Name Origin Filter & Edge Case Safety)")
    print("=" * 75)

    contacts = [
        # Pure Indian Names (Must be excluded when guardrail ON)
        ApolloContact(key="ind-1", name="Rahul Sharma", job_title="CEO", company="SharmaTechXYZ99", location="Mumbai, IN"),
        ApolloContact(key="ind-2", name="Priya Patel", job_title="VP Engineering", company="PatelCloudXYZ99", location="Bangalore, IN"),
        ApolloContact(key="ind-3", name="Venkatesh Rao", job_title="Director", company="RaoSolutionsXYZ99", location="Hyderabad, IN"),
        ApolloContact(key="ind-4", name="Debashis Mukherjee", job_title="Founder", company="MukherjeeAIXYZ99", location="Kolkata, IN"),

        # Edge Cases & Ambiguous Names (MUST NOT BE EXCLUDED — Conservative Policy: Treated as Foreign/Lead)
        ApolloContact(key="edge-1", name="Peter D'Souza", job_title="CEO", company="DSouzaLogisticsXYZ99", location="Goa, IN"),
        ApolloContact(key="edge-2", name="Kevin Fernandes", job_title="Director", company="FernandesTradingXYZ99", location="Singapore"),
        ApolloContact(key="edge-3", name="Tariq Sheikh", job_title="VP Product", company="SheikhEnterprisesXYZ99", location="Dubai, UAE"),
        ApolloContact(key="edge-4", name="Maya Lin", job_title="Founder", company="LinCreativeXYZ99", location="San Francisco, US"),

        # Standard Foreign Names (MUST NOT BE EXCLUDED)
        ApolloContact(key="foreign-1", name="Alice Chen", job_title="CEO", company="ChenRoboticsXYZ99", location="San Francisco, US"),
        ApolloContact(key="foreign-2", name="John Miller", job_title="CTO", company="MillerDataXYZ99", location="London, UK"),
    ]

    # Run with Indian Guardrail ON
    req_on = ApolloMatchRequest(contacts=contacts, title_guardrail_enabled=False, indian_name_guardrail_enabled=True)
    resp_on = match_apollo(req_on)
    res_on = resp_on.get("results", {})

    print("\nVerifying Guardrail 3 (Indian Name Filter: ON)...")
    for key in ["ind-1", "ind-2", "ind-3", "ind-4"]:
        r = res_on[key]
        assert r["ignored"] is True, f"{key} should be IGNORED when Indian guardrail is ON"
        assert r["guardrail_status"] == "indian_name_disqualified", f"{key} status should be indian_name_disqualified"
        print(f"  [PASS] Pure Indian Name '{key}' ({contacts[next(i for i, c in enumerate(contacts) if c.key == key)].name}) correctly EXCLUDED (Ignored).")

    for key in ["edge-1", "edge-2", "edge-3", "edge-4", "foreign-1", "foreign-2"]:
        r = res_on[key]
        assert r["ignored"] is False, f"Edge case / Foreign '{key}' should NOT be ignored"
        assert r["required"] is True, f"Edge case / Foreign '{key}' should be QUALIFIED as Required"
        print(f"  [PASS] Edge case / Foreign '{key}' ({contacts[next(i for i, c in enumerate(contacts) if c.key == key)].name}) correctly QUALIFIED (Not ignored).")

    # Run with Indian Guardrail OFF
    print("\nVerifying Guardrail 3 Toggle (Indian Name Filter: OFF)...")
    req_off = ApolloMatchRequest(contacts=contacts, title_guardrail_enabled=False, indian_name_guardrail_enabled=False)
    resp_off = match_apollo(req_off)
    res_off = resp_off.get("results", {})

    for key in ["ind-1", "ind-2", "ind-3", "ind-4"]:
        r = res_off[key]
        assert r["required"] is True, f"{key} should be REQUIRED when guardrail is OFF"
        print(f"  [PASS] Pure Indian Name '{key}' correctly QUALIFIED when filter is OFF.")

    print("\n[PASS] Guardrail 3 (Pure Indian Name Origin Filter) passed perfectly!")


if __name__ == "__main__":
    print("Starting Comprehensive Guardrails Test Suite...")
    test_guardrail_1_domain_check()
    test_guardrail_2_toggle()
    test_job_title_hierarchy_guardrails()
    test_batch_match_apollo_endpoint()
    test_guardrail_3_indian_name_filter()
    print("\n" + "=" * 75)
    print("ALL GUARDRAIL TESTS COMPLETED AND PASSED PERFECTLY!")
    print("=" * 75)
