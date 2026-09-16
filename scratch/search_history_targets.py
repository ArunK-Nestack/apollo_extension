import sqlite3
import urllib.parse
import json

conn = sqlite3.connect(r"scratch\profile16_history.sqlite")
cur = conn.cursor()

queries = [
    "SELECT url, title, last_visit_time FROM urls WHERE url LIKE '%saas%'",
    "SELECT url, title, last_visit_time FROM urls WHERE url LIKE '%director%'",
    "SELECT url, title, last_visit_time FROM urls WHERE url LIKE '%EST%'",
    "SELECT url, title, last_visit_time FROM urls WHERE url LIKE '%finderViewId%'"
]

found = {}
for q in queries:
    cur.execute(q)
    rows = cur.fetchall()
    for r in rows:
        url = r[0]
        if "apollo.io/#/people" in url:
            found[url] = r[1]

print(f"Found {len(found)} matching Apollo People URLs in history:")
for url, title in list(found.items())[:15]:
    parsed = urllib.parse.urlparse(url)
    fragment = parsed.fragment
    qs = fragment.split("?", 1)[1] if "?" in fragment else parsed.query
    params = urllib.parse.parse_qs(qs)
    
    rec_id = params.get("recommendationConfigId", [""])[0]
    finder_id = params.get("finderViewId", [""])[0]
    tags = params.get("qOrganizationKeywordTags[]", [])
    titles = params.get("personTitles[]", [])
    keywords = params.get("qKeywords", [""])[0]
    
    print(f"\nTitle: {title}")
    print(f"  RecConfigId: {rec_id} | FinderViewId: {finder_id}")
    print(f"  Keywords: '{keywords}' | Titles: {titles[:5]}")
    print(f"  Tags ({len(tags)}): {tags[:6]}")
    print(f"  URL: {url[:180]}...")
