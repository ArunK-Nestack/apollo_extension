"""
Test Script: 3-Layered Matching Verification
Demonstrates and validates:
  Layer 1: Deterministic Heuristic Match (Free, Instant, No AI)
  Layer 2: LLM Internal Knowledge Domain Resolution (High-confidence, no web search)
  Layer 3: Live Web Search Verification (Mandatory search for niche/ambiguous companies)
"""
import json
import urllib.request

API_URL = "http://127.0.0.1:8000/match-apollo"

# 1. Layer 1: Deterministic Case
# Direct company/domain normalization match (VE GROUP <-> ve-group.com)
LAYER_1_CASE = {
    "key": "layer-1-deterministic",
    "name": "Joffrey Hagendijk",
    "job_title": "COO",
    "company": "VE GROUP",
    "location": "Netherlands",
}

# 2. Layer 2: LLM Internal Knowledge Case
# Parent holding/subsidiary where GPT knows the official domain without browsing the web
LAYER_2_CASE = {
    "key": "layer-2-llm-knowledge",
    "name": "Joffrey Hagendijk",
    "job_title": "Chief Operations Officer",
    "company": "Vermeer Eemhaven Heat Exchangers",
    "location": "Rotterdam, Netherlands",
}

# 3. Layer 3: Live Web Search Case
# Apollo company name ("360 Protective Solutions") has zero token overlap with CRM domain ("wso-security.com").
# Triggers mandatory OpenAI Web Search, searches live web, verifies "wso-security.com" as company brand, and matches!
LAYER_3_CASE = {
    "key": "layer-3-web-search",
    "name": "Johnny Torres",
    "job_title": "Chief Operations Officer (COO)",
    "company": "360 Protective Solutions",
    "location": "New York, United States",
}


def run_test(title: str, contact: dict):
    print("\n" + "=" * 75)
    print(f"  {title}")
    print("=" * 75)
    print(f"Contact Input: {contact['name']} | {contact['job_title']} | {contact['company']}")

    payload = {"contacts": [contact]}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("results", {}).get(contact["key"], {})
            activity = data.get("activity", [])
            summary = data.get("summary", {})

            print(f"\nResult: exists = {result.get('exists')}")
            print(f"Match Method: {result.get('domain_match_method')}")
            if result.get("email"):
                print(f"Matched CRM Email: {result.get('email')}")
            if result.get("resolved_domain"):
                print(f"AI Resolved Domain: {result.get('resolved_domain')} ({result.get('resolved_domain_type')})")

            print("\nExecution Telemetry Events:")
            for act in activity:
                event = act.get("event")
                msg = act.get("message")
                details = act.get("details", {})
                print(f"  -> [{event}] {msg}")
                if event in (
                    "DETERMINISTIC_MATCH",
                    "DETERMINISTIC_NO_MATCH",
                    "LLM_KNOWLEDGE_START",
                    "LLM_KNOWLEDGE_RESULT",
                    "LLM_KNOWLEDGE_ACCEPTED",
                    "WEB_SEARCH_START",
                    "WEB_SEARCH_RESULT",
                    "WEB_SEARCH_ACCEPTED",
                    "DOMAIN_DB_MATCH",
                    "FINAL_EXISTING",
                ):
                    if details:
                        print(f"     Details: {details}")

            print(f"\nSummary Metrics: {summary}")

    except Exception as e:
        print(f"Error connecting to {API_URL}: {e}")
        print("Make sure FastAPI server is running: uvicorn api:app --reload")


if __name__ == "__main__":
    print("Testing 3-Layered Apollo Extension Matching Workflow against local API")
    run_test("LAYER 1: Deterministic Match (No AI, Zero Latency)", LAYER_1_CASE)
    run_test("LAYER 2: LLM Internal Knowledge", LAYER_2_CASE)
    run_test("LAYER 3: Live Web Search Verification (Web Search Triggered)", LAYER_3_CASE)
