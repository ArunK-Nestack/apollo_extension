import json

with open(r"scratch\all_discovered_searches.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for email, searches in data.items():
    print(f"\n==========================================")
    print(f"ACCOUNT: {email}")
    print(f"==========================================")
    for idx, s in enumerate(searches, 1):
        params = s.get("params", {})
        tags = params.get("qOrganizationKeywordTags[]", [])
        locations = params.get("personLocations[]", [])
        seniorities = params.get("personSeniorities[]", [])
        keywords = params.get("qKeywords", [""])[0]
        rec_id = s.get("rec_id")
        
        print(f"Search #{idx}:")
        print(f"  Visited    : {s.get('visit')}")
        print(f"  RecConfigId: {rec_id}")
        print(f"  Locations  : {locations}")
        print(f"  Seniorities: {seniorities}")
        print(f"  Keywords   : '{keywords}'")
        print(f"  Tags ({len(tags)}): {tags[:6]} ...")
