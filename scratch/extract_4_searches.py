import sqlite3
import urllib.parse
import json
import re

conn = sqlite3.connect(r"scratch\profile16_history.sqlite")
cur = conn.cursor()
cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo.io/#/people?%' ORDER BY last_visit_time DESC")
rows = cur.fetchall()

searches = {}

for url, title, visit in rows:
    parsed = urllib.parse.urlparse(url)
    fragment = parsed.fragment
    qs = fragment.split("?", 1)[1] if "?" in fragment else parsed.query
    params = urllib.parse.parse_qs(qs)
    
    titles = params.get("personTitles[]", [])
    tags = params.get("qOrganizationKeywordTags[]", [])
    locs = params.get("personLocations[]", [])
    not_locs = params.get("personNotLocations[]", [])
    rec_id = params.get("recommendationConfigId", [""])[0]
    timezones = params.get("personTimezones[]", [])
    
    # Identify which search this matches
    # 1. "Director IT/Others/NA EST"
    if any("director" in t.lower() for t in titles):
        key = "Director IT/Others/NA EST"
    # 2. "Construction Services-saas"
    elif any("saas" in tag.lower() for tag in tags) or any("software" in tag.lower() for tag in tags):
        key = "Construction Services-saas"
    # 3. "construction services regular 2"
    elif rec_id == "6aa44c94f7723600201b0ec6" or (len(tags) > 30 and len(tags) < 70):
        key = "construction services regular 2"
    # 4. "Construction service-Regular"
    elif rec_id == "66c5da1ba9d74501b3a8df93" or len(tags) >= 70:
        key = "Construction service-Regular"
    else:
        continue
    
    if key not in searches:
        searches[key] = {
            "key": key,
            "visit": visit,
            "rec_id": rec_id,
            "titles": titles,
            "tags_count": len(tags),
            "tags": tags,
            "locations": locs,
            "timezones": timezones,
            "not_locations": not_locs,
            "url": url,
            "params": params
        }

print(f"Identified {len(searches)} creator searches for rahul@nestack.co.in:")
for k, s in searches.items():
    print(f"[*] {k}")
    print(f"=======================================================")
    print(f"  Last Visit   : {s['visit']}")
    print(f"  RecConfigId  : {s['rec_id']}")
    print(f"  Titles ({len(s['titles'])}): {s['titles'][:6]}")
    print(f"  Locations    : {s['locations']}")
    print(f"  Timezones    : {s['timezones']}")
    print(f"  Tags ({s['tags_count']}): {s['tags'][:6]}")

with open(r"scratch\extracted_4_searches.json", "w", encoding="utf-8") as f:
    json.dump(searches, f, indent=2)
print("\nSaved to scratch/extracted_4_searches.json")
