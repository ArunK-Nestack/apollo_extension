import sqlite3
import shutil
import tempfile
import os
import urllib.parse

tmp = os.path.join(tempfile.gettempdir(), 'hist_p7.sqlite')
shutil.copy2(r'C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 7\History', tmp)
conn = sqlite3.connect(tmp)
cur = conn.cursor()
cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo.io/#/people%' ORDER BY last_visit_time DESC")
rows = cur.fetchall()
print(f"Profile 7 has {len(rows)} people URLs:")
seen = set()
for u, t, v in rows:
    parsed = urllib.parse.urlparse(u)
    frag = parsed.fragment
    qs = frag.split("?", 1)[1] if "?" in frag else parsed.query
    params = urllib.parse.parse_qs(qs)
    rec_id = params.get("recommendationConfigId", [""])[0]
    titles = tuple(params.get("personTitles[]", []))
    tags = tuple(params.get("qOrganizationKeywordTags[]", []))
    locs = tuple(params.get("personLocations[]", []))
    sig = (rec_id, titles, tags, locs)
    if sig not in seen:
        seen.add(sig)
        print(f"\n[Visit: {v}] Title: {t}")
        print(f"  RecId: {rec_id}")
        if titles: print(f"  Titles: {titles}")
        if tags: print(f"  Tags: {tags[:5]}")
        if locs: print(f"  Locs: {locs}")
        print(f"  URL: {u[:150]}")
conn.close()
