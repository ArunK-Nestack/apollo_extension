import sqlite3
import urllib.parse

conn = sqlite3.connect(r"scratch\profile16_history.sqlite")
cur = conn.cursor()
cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo.io/#/people?%' ORDER BY last_visit_time DESC")
rows = cur.fetchall()

seen_configs = {}
for url, title, visit in rows:
    parsed = urllib.parse.urlparse(url)
    fragment = parsed.fragment
    qs = fragment.split("?", 1)[1] if "?" in fragment else parsed.query
    params = urllib.parse.parse_qs(qs)
    
    rec_id = params.get("recommendationConfigId", [""])[0]
    saved_search_id = params.get("savedSearchId", [""])[0]
    finderViewId = params.get("finderViewId", [""])[0]
    
    key = rec_id or saved_search_id or finderViewId or "no_id"
    if key not in seen_configs:
        seen_configs[key] = {
            "visit": visit,
            "title": title,
            "rec_id": rec_id,
            "savedSearchId": saved_search_id,
            "finderViewId": finderViewId,
            "qKeywords": params.get("qKeywords", []),
            "locations": params.get("personLocations[]", []),
            "seniorities": params.get("personSeniorities[]", []),
            "org_tags_count": len(params.get("qOrganizationKeywordTags[]", [])),
            "first_few_tags": params.get("qOrganizationKeywordTags[]", [])[:5],
            "url": url
        }

print(f"Found {len(seen_configs)} distinct search configurations:")
for k, v in seen_configs.items():
    print(f"\nID: {k}")
    print(f"  Visit: {v['visit']}")
    print(f"  Title: {v['title']}")
    print(f"  Locations: {v['locations']}")
    print(f"  Seniorities: {v['seniorities']}")
    print(f"  Org Tags ({v['org_tags_count']}): {v['first_few_tags']}")
    print(f"  Keywords: {v['qKeywords']}")
