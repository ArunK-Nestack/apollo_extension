import sqlite3
import urllib.parse
import json
import re

conn = sqlite3.connect(r"scratch\profile16_history.sqlite")
cur = conn.cursor()
cur.execute("SELECT url, title, last_visit_time FROM urls WHERE url LIKE '%apollo.io/#/people?%' ORDER BY last_visit_time DESC")
rows = cur.fetchall()

searches = {}

for url, title, visit_time in rows:
    parsed = urllib.parse.urlparse(url)
    fragment = parsed.fragment
    if "?" in fragment:
        qs = fragment.split("?", 1)[1]
    else:
        qs = parsed.query
    
    params = urllib.parse.parse_qs(qs)
    
    # Check if this URL has search filters
    # Identify search name if present in title or params
    # Look for recommendationConfigId, persona, tags, etc.
    rec_id = params.get("recommendationConfigId", [""])[0]
    keywords = params.get("qKeywords", [""])[0]
    org_tags = params.get("qOrganizationKeywordTags[]", [])
    seniorities = params.get("personSeniorities[]", [])
    locations = params.get("personLocations[]", [])
    
    # Key by sorted tags and locations
    tag_key = tuple(sorted(org_tags))
    if tag_key and tag_key not in searches:
        searches[tag_key] = {
            "title": title,
            "url": url,
            "org_tags": org_tags,
            "seniorities": seniorities,
            "locations": locations,
            "keywords": keywords,
            "rec_id": rec_id,
            "params": params
        }

print(f"Found {len(searches)} unique saved searches / filter sets in history:")
for idx, (k, s) in enumerate(searches.items(), 1):
    print(f"\n--- Search #{idx} ---")
    print("Title:", s["title"])
    print("Org Tags (first 10):", s["org_tags"][:10])
    print("Seniorities:", s["seniorities"])
    print("Locations:", s["locations"])
    print("RecommendationConfigId:", s["rec_id"])
    print("URL Sample:", s["url"][:200] + "...")
