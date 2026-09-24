#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apollo Search Optimizer & AI Keyword Slicing Engine
===================================================
1. Selects an Apollo account and saved search without consuming any Apollo credits.
2. Uses lightweight AI (gpt-4o-mini at <$0.0001) to generate high-yield first names
   and tailored sub-industry / functional keywords for search slicing.
3. Automatically probes Apollo People Search API in parallel (0 credits / $0.00)
   to discover the exact lead volume and page counts for each keyword.
4. Tracks state and history in config/search_keyword_history.json:
   - Remembers keywords previously recommended/used.
   - Informs the user: "These were already given to you. This time, use these fresh ones."
5. Ranks and presents the Top 20 highest-yielding keywords (80-100 pages / 2,000-2,500 leads)
   ready to copy directly into Apollo Web or launch via apollo_search_direct.py.
"""

from __future__ import annotations

import os
import sys
import json
import time
import copy
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

from scripts.apollo_search_direct import (
    fetch_live_apollo_searches,
    load_account_creator_searches,
    load_all_creator_searches,
    save_account_creator_search,
    probe_searches_volume,
    parse_apollo_url,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

CONFIG_PATH = PROJECT_ROOT / "config" / "apollo_accounts.json"
SAVED_SEARCHES_PATH = PROJECT_ROOT / "config" / "saved_searches.json"
HISTORY_LEDGER_PATH = PROJECT_ROOT / "config" / "search_keyword_history.json"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_DOMAIN_MODEL", "gpt-4o-mini").strip()

# Comprehensive high-yield census & executive first names (including common professional nicknames)
STATIC_TOP_NAMES = [
    # Top Common Executive Male Names & Professional Nicknames
    "James", "Jim", "John", "Jack", "Robert", "Bob", "Rob", "Michael", "Mike", "William", "Bill", "Will",
    "David", "Dave", "Richard", "Rick", "Rich", "Joseph", "Joe", "Thomas", "Tom", "Charles", "Charlie", "Chuck",
    "Christopher", "Chris", "Daniel", "Dan", "Danny", "Matthew", "Matt", "Anthony", "Tony", "Donald", "Don",
    "Mark", "Marc", "Paul", "Steven", "Steve", "Stephen", "Andrew", "Andy", "Drew", "Kenneth", "Ken",
    "Joshua", "Josh", "Kevin", "Brian", "Bryan", "George", "Edward", "Ed", "Ronald", "Ron",
    "Timothy", "Tim", "Jason", "Jeffrey", "Jeff", "Ryan", "Jacob", "Jake", "Gary", "Nicholas", "Nick",
    "Eric", "Erik", "Jonathan", "Jon", "Larry", "Justin", "Scott", "Brandon", "Benjamin", "Ben",
    "Samuel", "Sam", "Gregory", "Greg", "Frank", "Alexander", "Alex", "Raymond", "Ray", "Patrick", "Pat",
    "Dennis", "Jerry", "Tyler", "Aaron", "Jose", "Adam", "Nathan", "Nate", "Henry", "Douglas", "Doug",
    "Zachary", "Zach", "Peter", "Pete", "Kyle", "Walter", "Harold", "Jeremy", "Ethan", "Carl",
    "Keith", "Roger", "Gerald", "Christian", "Terry", "Sean", "Shawn", "Arthur", "Austin", "Noah",
    "Lawrence", "Jesse", "Jordan", "Bryan", "Billy", "Bruce", "Gabriel", "Gabe", "Alan", "Allan", "Allen",
    "Juan", "Logan", "Wayne", "Ralph", "Roy", "Eugene", "Gene", "Randy", "Vincent", "Vince",
    "Russell", "Russ", "Louis", "Lou", "Philip", "Phil", "Bobby", "Bradley", "Brad", "Travis",
    "Trevor", "Brett", "Todd", "Craig", "Kurt", "Curt", "Troy", "Derek", "Chad", "Corey", "Cory",
    "Dean", "Brent", "Mitchell", "Mitch", "Victor", "Vic", "Martin", "Marty", "Shane", "Glenn", "Glen",
    "Darren", "Dale", "Marcus", "Lance", "Barry", "Lee", "Cameron", "Colin", "Grant", "Spencer",
    "Ross", "Neil", "Blake", "Wesley", "Wes", "Joel", "Kent", "Kirk", "Clark", "Luke", "Lucas",
    "Ian", "Evan", "Owen", "Cole", "Max", "Leo", "Seth",

    # Top Common Executive Female Names & Professional Nicknames
    "Mary", "Patricia", "Pat", "Patty", "Jennifer", "Jen", "Jenny", "Linda", "Elizabeth", "Beth", "Liz",
    "Barbara", "Barb", "Susan", "Sue", "Jessica", "Jess", "Sarah", "Sara", "Karen", "Lisa",
    "Nancy", "Margaret", "Maggie", "Sandra", "Sandy", "Ashley", "Kimberly", "Kim", "Emily", "Donna",
    "Michelle", "Carol", "Carolyn", "Amanda", "Mandy", "Melissa", "Deborah", "Debbie", "Stephanie", "Steph",
    "Rebecca", "Becky", "Sharon", "Laura", "Cynthia", "Cindy", "Kathleen", "Kathy", "Amy", "Angela", "Angie",
    "Shirley", "Anna", "Anne", "Brenda", "Pamela", "Pam", "Emma", "Nicole", "Nikki", "Helen",
    "Samantha", "Sam", "Katherine", "Kate", "Katie", "Christine", "Chrissy", "Debra", "Rachel", "Janet",
    "Maria", "Heather", "Diane", "Diana", "Julie", "Joyce", "Victoria", "Vicky", "Kelly", "Christina", "Tina",
    "Joan", "Evelyn", "Judith", "Judy", "Andrea", "Cheryl", "Hannah", "Jacqueline", "Jackie", "Martha",
    "Gloria", "Teresa", "Terri", "Ann", "Madison", "Frances", "Kathryn", "Janice", "Jean", "Abigail", "Abby",
    "Alice", "Julia", "Sophia", "Grace", "Denise", "Amber", "Doris", "Marilyn", "Danielle", "Beverly",
    "Megan", "Brittany", "Jane", "Lori", "Tiffany", "Natalie", "Allison", "Ally", "Rose", "Alexis",
    "Kayla", "Shannon", "Courtney", "Paige", "Morgan", "Vanessa", "Audrey", "Monica", "Holly", "Lindsay",
    "Melanie", "Valerie", "Jill", "Leslie", "Wendy", "Heidi", "Tara", "Carrie", "Stacy", "Erica", "Kristen",
    "April", "Leah", "Erin", "Renee", "Dawn", "Tracy"
]

STATIC_TOP_KEYWORDS = [
    "Enterprise", "Cloud", "Security", "Infrastructure", "Engineering", "Data", "Operations",
    "Management", "Systems", "Architect", "DevOps", "Cybersecurity", "Software", "Digital",
    "Commercial", "Corporate", "Global", "Solutions", "Platform", "Strategy", "Analytics"
]

# High-yield job title anchors that match all variations via Apollo substring search
# e.g., "Director" matches Creative Director, Visual Director, Managing Director, etc.
STATIC_JOB_TITLES = [
    # Top Broad Seniority Anchors (catches ALL variations via substring/token search)
    "Director", "Manager", "Vice President", "VP", "President", "Chief", "Officer",
    "Head", "Partner", "Owner", "Founder", "Principal", "Executive", "Lead",
    "Producer", "Strategist", "Consultant", "Supervisor", "Coordinator", "Specialist",
    "Administrator", "Advisor", "Architect", "Associate",

    # High-Yield Seniority Compounds (ideal slices when broad title exceeds 2,500 leads)
    "Managing Director", "Executive Director", "Operations Director", "Sales Director",
    "Creative Director", "Visual Director", "Marketing Director", "Finance Director",
    "Art Director", "Design Director", "Technical Director", "Regional Director",
    "General Manager", "Operations Manager", "Sales Manager", "Account Manager",
    "Project Manager", "Production Manager", "Marketing Manager", "Creative Manager",
    "Senior Vice President", "Executive Vice President", "SVP", "EVP",
    "Chief Executive Officer", "Chief Operating Officer", "Chief Technology Officer",
    "Chief Creative Officer", "Chief Marketing Officer", "Chief Commercial Officer",
    "Head of Creative", "Head of Operations", "Head of Sales", "Head of Marketing",
    "Head of Design", "Head of Production", "Head of Digital", "Head of Strategy",
    "Managing Partner", "Senior Partner", "Co-Founder", "Senior Director",
    "Executive Producer", "Digital Producer", "Creative Lead", "Design Lead",
    "Creative Strategist", "Brand Strategist"
]

# High-Yield Functional & Departmental Leadership Roles
STATIC_FUNCTIONAL_KEYWORDS = [
    "Creative", "Visual", "Operations", "Sales", "Marketing", "Commercial",
    "Design", "Production", "Digital", "Strategy", "Content", "Brand",
    "Media", "Communications", "Technology", "Engineering", "Business Development",
    "Client Services", "Accounts", "Finance", "Product", "Growth", "Editorial",
    "Advertising", "Public Relations", "PR", "Analytics", "Solutions", "Enterprise"
]


# =====================================================================
# 1. ACCOUNT, SEARCH & HISTORY LEDGER MANAGEMENT
# =====================================================================

def load_accounts() -> List[Dict[str, Any]]:
    """Load configured Apollo accounts from config/apollo_accounts.json."""
    if not CONFIG_PATH.exists():
        return []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def load_saved_searches_catalog() -> Dict[str, List[Dict[str, Any]]]:
    """Load saved searches mapped by account email from config/saved_searches.json."""
    if not SAVED_SEARCHES_PATH.exists():
        return {}
    try:
        with open(SAVED_SEARCHES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_keyword_history() -> Dict[str, Any]:
    """Load historical keyword tracking ledger from config/search_keyword_history.json."""
    if not HISTORY_LEDGER_PATH.exists():
        return {}
    try:
        with open(HISTORY_LEDGER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_keyword_history(history_data: Dict[str, Any]) -> None:
    """Save updated keyword history ledger."""
    HISTORY_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2)


def get_search_history(account_email: str, search_name: str) -> Dict[str, Any]:
    """Retrieve history record for a specific account and saved search."""
    history_data = load_keyword_history()
    acc_key = account_email.strip().lower()
    search_key = search_name.strip()
    return history_data.get(acc_key, {}).get(search_key, {})


def record_search_recommendations(
    account_email: str,
    search_name: str,
    recommended_items: List[Dict[str, Any]]
) -> None:
    """Record newly recommended keywords into the persistent history ledger."""
    history_data = load_keyword_history()
    acc_key = account_email.strip().lower()
    search_key = search_name.strip()

    if acc_key not in history_data:
        history_data[acc_key] = {}
    if search_key not in history_data[acc_key]:
        history_data[acc_key][search_key] = {
            "search_name": search_name,
            "account_email": account_email,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "keywords": {}
        }

    search_record = history_data[acc_key][search_key]
    if "keywords" not in search_record:
        search_record["keywords"] = {}

    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for item in recommended_items:
        kw = item["keyword"].strip()
        kw_key = kw.lower()
        if kw_key not in search_record["keywords"]:
            search_record["keywords"][kw_key] = {
                "keyword": kw,
                "category": item.get("category", "keyword"),
                "total_leads": item.get("total_leads", 0),
                "total_pages": item.get("total_pages", 0),
                "first_recommended": timestamp_str,
                "times_recommended": 1,
                "status": "recommended"
            }
        else:
            search_record["keywords"][kw_key]["times_recommended"] += 1
            search_record["keywords"][kw_key]["last_recommended"] = timestamp_str

    save_keyword_history(history_data)


# =====================================================================
# 2. AI KEYWORD & NAME GENERATION (gpt-4o-mini at < $0.0001)
# =====================================================================

def generate_title_and_functional_slicing_candidates(
    filters: Dict[str, Any]
) -> Tuple[List[str], List[str], List[str]]:
    """
    Generate high-yield job title anchors, compound titles, functional leadership roles,
    and fallback first names tailored to the target criteria.
    
    Job titles act as substring/token matches in Apollo's search bar:
      - 'Director' automatically matches 'Visual Director', 'Creative Director', 'Managing Director', etc.
      - 'Manager' automatically matches 'Operations Manager', 'General Manager', etc.
    """
    titles_in_filter = filters.get("person_titles") or []
    if isinstance(titles_in_filter, list):
        titles_str = ", ".join(titles_in_filter[:10])
    else:
        titles_str = str(titles_in_filter)

    locations = filters.get("person_locations") or []
    loc_str = ", ".join(locations[:6]) if isinstance(locations, list) else str(locations)

    tags = filters.get("q_organization_keyword_tags") or []
    tags_str = ", ".join(tags[:8]) if isinstance(tags, list) else str(tags)

    prompt_summary = f"Job Titles: {titles_str or 'Executives/Decision Makers'}. Location: {loc_str or 'United States'}. Industry/Keywords: {tags_str or 'Corporate/Tech'}."

    if not OPENAI_API_KEY:
        return list(STATIC_JOB_TITLES), list(STATIC_FUNCTIONAL_KEYWORDS), list(STATIC_TOP_NAMES[:35])

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY, timeout=12.0)
        sys_prompt = (
            "You are an Apollo.io search optimization specialist. The goal is to slice large Apollo searches into "
            "high-yield lead batches right below Apollo's 2,500 lead / 100-page limit using search bar keyword substring matching. "
            "CRITICAL REQUIREMENT: Focus primarily on BROAD JOB TITLES and FUNCTIONAL LEADERSHIP ROLES (not personal first names). "
            "Job titles like 'Director' or 'Manager' yield 10x-50x more leads because Apollo search matches all substring variations "
            "(e.g., 'Director' matches Creative Director, Visual Director, Managing Director, Director of Operations, etc.). "
            "Given the target criteria, return a JSON object with: "
            "1) 'titles': list of 35 broad job title keyword anchors and high-yield title compounds (e.g., Director, Manager, Vice President, VP, Head, Partner, Owner, Chief, Officer, Creative Director, Visual Director, Operations Manager, etc.), "
            "2) 'keywords': list of 30 functional, departmental, and sub-industry keywords (e.g., Creative, Visual, Operations, Commercial, Sales, Marketing, Strategy, Digital, Media, Design, etc.), "
            "3) 'names': list of 15 common executive first names (e.g., Michael, David, John, James, etc.) as fallback. "
            "JSON output only."
        )
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt_summary}
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=500
        )
        content = resp.choices[0].message.content or "{}"
        parsed = json.loads(content)
        ai_titles = parsed.get("titles", [])
        ai_keywords = parsed.get("keywords", [])
        ai_names = parsed.get("names", [])

        # Merge titles with fallback
        combined_titles: List[str] = []
        for t in ai_titles + STATIC_JOB_TITLES:
            clean_t = str(t).strip()
            if clean_t and clean_t not in combined_titles:
                combined_titles.append(clean_t)

        # Merge functional keywords
        combined_functional: List[str] = []
        for k in ai_keywords + STATIC_FUNCTIONAL_KEYWORDS:
            clean_k = str(k).strip()
            if clean_k and clean_k not in combined_functional:
                combined_functional.append(clean_k)

        # Merge names
        combined_names: List[str] = []
        for n in ai_names + STATIC_TOP_NAMES:
            clean_n = str(n).strip().title()
            if clean_n and clean_n not in combined_names:
                combined_names.append(clean_n)

        return combined_titles, combined_functional, combined_names

    except Exception as ex:
        print(f"[Notice] AI generation fallback ({ex}); using pre-indexed job title & functional lexicon.")
        return list(STATIC_JOB_TITLES), list(STATIC_FUNCTIONAL_KEYWORDS), list(STATIC_TOP_NAMES[:35])


def generate_slicing_candidates_ai(filters: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """
    Backward-compatible candidate generator.
    Returns (job_titles_with_fallback_names, functional_keywords).
    """
    titles, functional, names = generate_title_and_functional_slicing_candidates(filters)
    # Combine titles first, followed by names to ensure backward compatibility with tests checking for names
    combined_primary = list(titles) + [n for n in names if n not in titles]
    return combined_primary, functional


# =====================================================================
# 3. 0-CREDIT APOLLO PROBING ENGINE (MIXED_PEOPLE/API_SEARCH)
# =====================================================================

def probe_apollo_keyword_count(
    api_key: str,
    base_payload: Dict[str, Any],
    keyword: str,
    category: str
) -> Dict[str, Any]:
    """
    Probe Apollo Search API with a single keyword/name at 0 credits.
    Returns total matching leads and 100-page limit calculation.
    """
    url = "https://api.apollo.io/api/v1/mixed_people/api_search"
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }

    test_payload = copy.deepcopy(base_payload)
    test_payload["q_keywords"] = keyword
    test_payload["page"] = 1
    test_payload["per_page"] = 1

    try:
        res = requests.post(url, headers=headers, json=test_payload, timeout=12)
        if res.status_code == 200:
            data = res.json()
            total = data.get("total_entries")
            if total is None:
                pagination = data.get("pagination") or {}
                total = pagination.get("total_entries", 0)
            total_leads = int(total or 0)
            total_pages = min(100, max(1, (total_leads + 99) // 100)) if total_leads > 0 else 0
            return {
                "keyword": keyword,
                "category": category,
                "total_leads": total_leads,
                "total_pages": total_pages,
                "status": "success",
            }
        elif res.status_code == 429:
            time.sleep(2.0)
            return {
                "keyword": keyword,
                "category": category,
                "total_leads": 0,
                "total_pages": 0,
                "status": "rate_limited",
            }
        else:
            return {
                "keyword": keyword,
                "category": category,
                "total_leads": 0,
                "total_pages": 0,
                "status": f"http_{res.status_code}",
            }
    except Exception as ex:
        return {
            "keyword": keyword,
            "category": category,
            "total_leads": 0,
            "total_pages": 0,
            "status": "error",
            "error": str(ex)
        }


def batch_probe_candidates(
    api_key: str,
    base_payload: Dict[str, Any],
    candidates: List[Tuple[str, str]],
    max_workers: int = 5
) -> List[Dict[str, Any]]:
    """
    Probe candidate keywords concurrently against Apollo People Search API.
    Costs 0 credits.
    """
    results: List[Dict[str, Any]] = []
    print(f"  • Rapidly probing {len(candidates)} candidate keywords/names across Apollo (0 Credits)...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(probe_apollo_keyword_count, api_key, base_payload, kw, cat): (kw, cat)
            for kw, cat in candidates
        }
        completed = 0
        total = len(candidates)
        for future in as_completed(futures):
            res = future.result()
            completed += 1
            if res.get("status") == "success" and res.get("total_leads", 0) > 0:
                results.append(res)
            # Progress dot indicator
            if completed % 5 == 0 or completed == total:
                print(f"    -> Scanned {completed}/{total} candidates...", end="\r", flush=True)

    print()
    return results


# =====================================================================
# 4. OPTIMIZER WORKFLOW & INTERACTIVE UI
# =====================================================================

def run_apollo_search_optimizer():
    """Main interactive search optimizer and keyword slicing action."""
    print("\n" + "=" * 100)
    print("           APOLLO SEARCH OPTIMIZER & AI KEYWORD SLICING ENGINE (0 CREDITS)")
    print("=" * 100)
    print("  • Solves Apollo's 100-page limit by finding high-yield search slices.")
    print("  • Uses AI (gpt-4o-mini at <$0.0001) to discover optimal executive names & sub-industries.")
    print("  • Checks real lead & page counts via Apollo Search API at $0.00 / 0 CREDITS.")
    print("  • Remembers previously used keywords so you never repeat slices.")
    print("=" * 100)

    # 1. Select Account
    accounts = load_accounts()
    if not accounts:
        print("\n[!] No accounts configured in config/apollo_accounts.json.")
        return

    print("\nSelect Target Apollo Account:")
    for idx, acc in enumerate(accounts, 1):
        print(f"  [{idx}] {acc.get('name', 'User')} ({acc.get('email', '')})")

    acc_choice = input(f"\nSelect Account [1-{len(accounts)}, default 1]: ").strip()
    try:
        acc_idx = int(acc_choice) - 1 if acc_choice else 0
        selected_account = accounts[acc_idx]
    except Exception:
        selected_account = accounts[0]

    api_key = selected_account["api_key"]
    account_email = selected_account.get("email", "").strip().lower()
    print(f"\n✓ Selected Account: {selected_account.get('name')} ({account_email})")

    # 2. Extract & Fetch Searches for this specific login (live from Apollo API + saved catalog)
    print(f"\n>> Querying Apollo REST API for live saved searches for {account_email}...")
    try:
        live_searches = fetch_live_apollo_searches(api_key)
        if live_searches:
            print(f"[+] Retrieved {len(live_searches)} live creator searches from Apollo!")
            for ls in live_searches:
                save_account_creator_search(account_email, ls["name"], ls["filters"], quiet=True, is_live=True)
    except Exception as ex:
        print(f"[Notice] Could not fetch live saved searches from Apollo: {ex}")

    def reload_catalog():
        acc_searches = load_account_creator_searches(account_email)
        all_searches = load_all_creator_searches()
        other_searches = [
            s for s in all_searches 
            if s.get("source_account", "").lower() != account_email.lower()
        ]
        combined = list(acc_searches) + list(other_searches)
        return acc_searches, all_searches, other_searches, combined

    account_searches, all_searches, other_searches, selectable_searches = reload_catalog()

    # Probe lead volume for active account searches
    print(f">> Probing data volume for {account_email} searches (0 credits)...")
    probe_searches_volume(api_key, account_searches, max_workers=4)

    selected_search = None
    while True:
        print(f"\nAvailable Searches for {account_email}:")
        if account_searches:
            for idx, cs in enumerate(account_searches, 1):
                count_val = cs.get("count")
                count_str = f" ({count_val:,d} leads)" if count_val is not None else ""
                live_badge = " [Apollo Live]" if cs.get("is_live") else ""
                print(f"  [{idx:>2}] ★ [This Account] {cs['name']}{live_badge}{count_str}")
        else:
            print("  (No creator searches registered specifically for this login)")

        if other_searches:
            start_offset = len(account_searches) + 1
            print(f"\nRegistered Searches Available Across Other Logins (Optional):")
            for offset, cs in enumerate(other_searches):
                idx = start_offset + offset
                count_val = cs.get("count")
                count_str = f" ({count_val:,d} leads)" if count_val is not None else ""
                acc_tag = cs.get("source_account", "Other Account")
                live_badge = " [Live]" if cs.get("is_live") else ""
                print(f"  [{idx:>2}] [{acc_tag}] {cs['name']}{live_badge}{count_str}")

        total_catalog = len(selectable_searches)
        print("\nOptions:")
        if total_catalog > 0:
            print(f"  [1-{total_catalog}] Select Search by number")
        print("  [r] Refresh live searches directly from Apollo for this login")
        print("  [a] Add / Register a new Search (paste Apollo People URL)")

        cmd = input(f"\nSelect search [1-{max(1, total_catalog)}, r, or a]: ").strip()
        if not cmd and total_catalog > 0:
            cmd = "1"

        if cmd.isdigit():
            val = int(cmd)
            if 1 <= val <= total_catalog:
                selected_search = selectable_searches[val - 1]
                break
            else:
                print(f"[!] Invalid number. Please select between 1 and {total_catalog}.")
        elif cmd.lower() == "r":
            print(f"\n>> Refreshing live searches directly from Apollo for {account_email}...")
            live = fetch_live_apollo_searches(api_key)
            if live:
                for ls in live:
                    save_account_creator_search(account_email, ls["name"], ls["filters"], quiet=True, is_live=True)
                print(f"[+] Refreshed {len(live)} live searches from Apollo!")
            account_searches, all_searches, other_searches, selectable_searches = reload_catalog()
            probe_searches_volume(api_key, account_searches, max_workers=4)
            continue
        elif cmd.lower() == "a":
            s_name = input("\nEnter name for this search: ").strip() or "Custom Apollo Search"
            url_in = input("Paste Apollo search URL from the People tab: ").strip()
            new_filters = parse_apollo_url(url_in) if url_in else {}
            save_account_creator_search(account_email, s_name, new_filters, is_live=False)
            selected_search = {"name": s_name, "display_name": s_name, "filters": new_filters}
            break
        else:
            print("[!] Invalid choice. Enter a search number, 'r' to refresh, or 'a' to add a search.")

    search_name = selected_search.get("name") or selected_search.get("display_name") or "Search"
    base_filters = copy.deepcopy(selected_search.get("filters", {}))
    print(f"\n✓ Selected Search: '{search_name}'")

    # Probe Base Search Count
    print("\nProbing base search baseline count...")
    base_probe = probe_apollo_keyword_count(api_key, base_filters, keyword="", category="base")
    base_total = base_probe.get("total_leads", 0)
    base_pages = base_probe.get("total_pages", 0)
    print(f"  • Base Search Volume: {base_total:,d} Total Leads ({base_pages} Pages at 100 leads/page)")
    if base_total >= 2500:
        print("  ⚠️ Apollo 100-page ceiling reached (2,500 leads cap). Slicing is REQUIRED to extract the rest!")

    # 3. Check State & History Ledger
    history_record = get_search_history(account_email, search_name)
    prev_keywords_dict = history_record.get("keywords", {})
    prev_keywords_set = set(prev_keywords_dict.keys())

    if prev_keywords_dict:
        print("\n" + "-" * 100)
        print(f"📜 HISTORY MEMORY: PREVIOUSLY RECOMMENDED / USED KEYWORDS FOR THIS SEARCH ({len(prev_keywords_dict)} recorded)")
        print("-" * 100)
        for prev_kw, details in sorted(prev_keywords_dict.items(), key=lambda x: x[1].get("total_pages", 0), reverse=True)[:10]:
            print(f"  • '{details['keyword']}': {details.get('total_leads', 0):,d} leads ({details.get('total_pages', 0)} pages) - First given: {details.get('first_recommended', 'N/A')}")
        if len(prev_keywords_dict) > 10:
            print(f"  ... and {len(prev_keywords_dict) - 10} more previously recorded in history ledger.")
        print("-" * 100)

    # 4. Generate AI Candidates focusing on Job Titles & Functional Leadership
    print("\n>>> Analyzing search criteria with AI (gpt-4o-mini at <$0.0001)...")
    titles, functional, names = generate_title_and_functional_slicing_candidates(base_filters)
    print(f"  ✓ Generated {len(titles)} tailored job titles and {len(functional)} functional leadership terms.")

    # Prioritize Job Title Anchors first (highest yield via substring search), then Functional Roles
    fresh_candidates: List[Tuple[str, str]] = []
    for t in titles:
        if t.lower() not in prev_keywords_set:
            fresh_candidates.append((t, "Job Title"))
    for f in functional:
        if f.lower() not in prev_keywords_set:
            fresh_candidates.append((f, "Functional Role"))

    # Fallback to names only if we have fewer than 25 fresh title/functional candidates
    if len(fresh_candidates) < 25:
        for n in names:
            if n.lower() not in prev_keywords_set:
                fresh_candidates.append((n, "First Name"))

    # Cap at top 60 candidates to keep scan lightning-fast (~15 seconds)
    fresh_candidates = fresh_candidates[:60]

    print(f"  ✓ Identified {len(fresh_candidates)} fresh, high-yield candidates for testing.")

    if not fresh_candidates:
        print("\n[Notice] All primary candidates have been previously recommended. Re-testing base pool...")
        fresh_candidates = [(t, "Job Title") for t in titles[:35]] + [(f, "Functional Role") for f in functional[:25]]

    # 5. Fast Parallel Probing against Apollo
    t0 = time.perf_counter()
    probe_results = batch_probe_candidates(api_key, base_filters, fresh_candidates, max_workers=8)
    print(f"  ✓ Completed scan in {time.perf_counter() - t0:.2f}s! (0 Apollo Credits Deducted)")

    if not probe_results:
        print("\n[Notice] No matching slicing keywords yielded leads. Try broader filters.")
        return

    # 6. Rank Results by Highest Lead & Page Count First
    # Sort by total_pages descending, then total_leads descending
    ranked = sorted(probe_results, key=lambda x: (x["total_pages"], x["total_leads"]), reverse=True)
    top_picks = ranked[:25]

    # 7. Display Top Recommendations
    print("\n" + "=" * 105)
    print(f"   TOP {len(top_picks)} HIGH-YIELD SEARCH SLICES FOR: '{search_name.upper()}'")
    print("=" * 105)
    print(f"{'#':<3} | {'Keyword / Title to use in Apollo Web':<38} | {'Category':<18} | {'Leads Found':<14} | {'Pages':<8} | {'Yield Quality'}")
    print("-" * 105)

    for idx, item in enumerate(top_picks, 1):
        kw_disp = f"'{item['keyword']}'"
        cat_disp = item["category"]
        leads_disp = f"{item['total_leads']:,d}"
        pages_disp = f"{item['total_pages']} pgs"

        if item["total_pages"] >= 100 or item["total_leads"] >= 2500:
            quality_tag = "🟢 MAX YIELD (100 pgs / 2,500+ Cap)"
        elif item["total_pages"] >= 50:
            quality_tag = "🟢 OPTIMAL (50-99 Pages)"
        elif item["total_pages"] >= 20:
            quality_tag = "🟡 HIGH (20-49 Pages)"
        elif item["total_pages"] >= 5:
            quality_tag = "🟠 MEDIUM (5-19 Pages)"
        else:
            quality_tag = "⚪ LOW (<5 Pages)"

        print(f"[{idx:<2}] | {kw_disp:<38} | {cat_disp:<18} | {leads_disp:<14} | {pages_disp:<8} | {quality_tag}")

    print("=" * 105)
    print("  🟢 OPTIMAL / MAX YIELD: Delivers 50-100 full pages (1,000-2,500+ leads) under Apollo's page cap.")
    print("  💡 Substring Matching: Searching 'Director' automatically matches Visual Director, Managing Director,")
    print("     Creative Director, Art Director, etc. across the entire Apollo search!")
    print("  💡 Tip: Copy any of these titles/keywords directly into the Apollo Web Search Bar, or use below!")
    print("=" * 105)

    # 8. Record in Persistent History Ledger
    record_search_recommendations(account_email, search_name, top_picks)
    print(f"\n✓ Saved these {len(top_picks)} recommendations into {HISTORY_LEDGER_PATH.name}!")
    print("  Next time you run this search, the engine will remember these were already given to you.")

    # 9. Next Steps / Direct Launch Option
    print("\nOPTIONS:")
    print("  [1] Copy/Use one of these keywords now and launch streaming scraper (apollo_search_direct.py)")
    print("  [2] Done (Exit)")
    next_action = input("\nSelect option [1/2, default 2]: ").strip()

    if next_action == "1":
        pick = input(f"Enter the # of the keyword to launch [1-{len(top_picks)}]: ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(top_picks):
            selected_kw = top_picks[int(pick) - 1]["keyword"]
            print(f"\nLaunching direct search scraper with keyword: '{selected_kw}'...")
            import subprocess
            cmd = [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "apollo_search_direct.py"),
                "--account", str(selected_account.get("id", 1)),
                "--search", search_name,
                "--keyword", selected_kw,
            ]
            subprocess.run(cmd)


if __name__ == "__main__":
    run_apollo_search_optimizer()
