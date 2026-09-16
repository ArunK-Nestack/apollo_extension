import json
import requests
import re
import os

with open(r"config\apollo_accounts.json", "r", encoding="utf-8") as f:
    accounts = json.load(f)

with open(r"scratch\compiled_account_searches.json", "r", encoding="utf-8") as f:
    compiled = json.load(f)

# Load discovered browser searches
with open(r"scratch\all_browser_discovered.json", "r", encoding="utf-8") as f:
    all_discovered = json.load(f)

# Helper to clean params
def clean_params(params):
    payload = {}
    for k, v in params.items():
        clean_k = k.replace("[]", "")
        snake_k = re.sub(r"(?<!^)(?=[A-Z])", "_", clean_k).lower()
        if snake_k in ["page", "recommendation_config_id", "sort_ascending", "sort_by_field", "q_keywords"]:
            continue
        payload[snake_k] = v if len(v) > 1 else v[0]
    return payload

# Start building config
complete_saved_searches = {}

# Account 6 is Rahul (exact 4 from screenshot)
with open(r"config\saved_searches.json", "r", encoding="utf-8") as f:
    existing = json.load(f)

complete_saved_searches["rahul@nestack.co.in"] = existing.get("rahul@nestack.co.in", [])

# Account 1: abel.abraham@nestacktechnologies.com
abel_raw = all_discovered.get("abel.abraham@nestacktechnologies.com", [])
abel_searches = []
if abel_raw:
    # Filter 1: Contract Manufacturing (29 tags)
    s29 = next((s for s in abel_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 29), None)
    if s29:
        abel_searches.append({
            "name": "Contract Manufacturing & MRO Services",
            "display_name": "Contract Manufacturing & MRO Services",
            "filters": clean_params(s29["params"])
        })
    # Filter 2: Building Materials & Pumps (31 tags)
    s31 = next((s for s in abel_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 31), None)
    if s31:
        abel_searches.append({
            "name": "Building Materials & Pumps And Compressors",
            "display_name": "Building Materials & Pumps And Compressors",
            "filters": clean_params(s31["params"])
        })
complete_saved_searches["abel.abraham@nestacktechnologies.com"] = abel_searches

# Account 3: rahul.chandran@nestack-tech.com
rc_raw = all_discovered.get("rahul.chandran@nestack-tech.com", [])
rc_searches = []
if rc_raw:
    s_re = next((s for s in rc_raw if "real estate" in str(s.get("params", {}).get("qOrganizationKeywordTags[]", [])).lower()), None)
    if s_re:
        rc_searches.append({
            "name": "real estate saas",
            "display_name": "real estate saas",
            "filters": clean_params(s_re["params"])
        })
    s_fnd = next((s for s in rc_raw if s.get("params", {}).get("personTitles[]")), None)
    if s_fnd:
        rc_searches.append({
            "name": "Founder & Executive Leadership Search",
            "display_name": "Founder & Executive Leadership Search",
            "filters": clean_params(s_fnd["params"])
        })
complete_saved_searches["rahul.chandran@nestack-tech.com"] = rc_searches

# Account 4: vijay@nestacktech.com
v4_raw = all_discovered.get("vijay@nestacktech.com", [])
v4_searches = []
if v4_raw:
    s_auto = next((s for s in v4_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 66), None)
    if s_auto:
        v4_searches.append({
            "name": "Automotive - Commercial Fleet Services",
            "display_name": "Automotive - Commercial Fleet Services",
            "filters": clean_params(s_auto["params"])
        })
    s_chro = next((s for s in v4_raw if s.get("params", {}).get("personTitles[]")), None)
    if s_chro:
        v4_searches.append({
            "name": "CHRO & HR Executive Leadership (NA, AUS, NZ, SG)",
            "display_name": "CHRO & HR Executive Leadership (NA, AUS, NZ, SG)",
            "filters": clean_params(s_chro["params"])
        })
complete_saved_searches["vijay@nestacktech.com"] = v4_searches

# Account 5: jith@nestack.info
jith_raw = all_discovered.get("jith@nestack.info", [])
jith_searches = []
if jith_raw:
    s_cso = next((s for s in jith_raw if s.get("params", {}).get("personTitles[]")), None)
    if s_cso:
        jith_searches.append({
            "name": "CSO/CPO OTHERS (Chief Sales & Product Officers)",
            "display_name": "CSO/CPO OTHERS (Chief Sales & Product Officers)",
            "filters": clean_params(s_cso["params"])
        })
    s_aero = next((s for s in jith_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 95), None)
    if s_aero:
        jith_searches.append({
            "name": "Aerospace & Defense Technology",
            "display_name": "Aerospace & Defense Technology",
            "filters": clean_params(s_aero["params"])
        })
    s_mktg = next((s for s in jith_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 99), None)
    if s_mktg:
        jith_searches.append({
            "name": "Digital Marketing & Agency Services",
            "display_name": "Digital Marketing & Agency Services",
            "filters": clean_params(s_mktg["params"])
        })
    s_def = next((s for s in jith_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 38), None)
    if s_def:
        jith_searches.append({
            "name": "Defense Technology Contractor & Business Aviation",
            "display_name": "Defense Technology Contractor & Business Aviation",
            "filters": clean_params(s_def["params"])
        })
complete_saved_searches["jith@nestack.info"] = jith_searches

# Account 7: vijay.raghavan@nestack.com
v7_raw = all_discovered.get("vijay.raghavan@nestack.com", [])
v7_searches = []
if v7_raw:
    s_ins = next((s for s in v7_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 72), None)
    if s_ins:
        v7_searches.append({
            "name": "Insurance & Financial Services",
            "display_name": "Insurance & Financial Services",
            "filters": clean_params(s_ins["params"])
        })
    s_fin = next((s for s in v7_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 24), None)
    if s_fin:
        v7_searches.append({
            "name": "Financial Consulting & Wealth Management",
            "display_name": "Financial Consulting & Wealth Management",
            "filters": clean_params(s_fin["params"])
        })
    s_tech = next((s for s in v7_raw if s.get("params", {}).get("personTitles[]")), None)
    if s_tech:
        v7_searches.append({
            "name": "Technology & IT Manager Leadership Search",
            "display_name": "Technology & IT Manager Leadership Search",
            "filters": clean_params(s_tech["params"])
        })
complete_saved_searches["vijay.raghavan@nestack.com"] = v7_searches

# Account 9: rahul@nestaktechnology.com (Profile 17)
p17_raw = all_discovered.get("Profile 17", [])
r9_searches = []
if p17_raw:
    s_ooh = next((s for s in p17_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 99), None)
    if s_ooh:
        r9_searches.append({
            "name": "Advertising, Marketing & Communications-regular",
            "display_name": "Advertising, Marketing & Communications-regular",
            "filters": clean_params(s_ooh["params"])
        })
    s_ad = next((s for s in p17_raw if len(s.get("params", {}).get("qOrganizationKeywordTags[]", [])) == 29), None)
    if s_ad:
        r9_searches.append({
            "name": "Full-Service Ad Agency & Digital Marketing",
            "display_name": "Full-Service Ad Agency & Digital Marketing",
            "filters": clean_params(s_ad["params"])
        })
complete_saved_searches["rahul@nestaktechnology.com"] = r9_searches

# Account 11: rchandran@nestack.biz
r11_raw = all_discovered.get("rchandran@nestack.biz", [])
r11_searches = []
if r11_raw:
    s_hd = r11_raw[0]
    r11_searches.append({
        "name": "HEAD - Enterprise Applications & Leadership",
        "display_name": "HEAD - Enterprise Applications & Leadership",
        "filters": clean_params(s_hd["params"])
    })
complete_saved_searches["rchandran@nestack.biz"] = r11_searches

# Account 2: VIJAY.RAGHAVAN@NESTACKTECHNOLOGIES.COM
complete_saved_searches["vijay.raghavan@nestacktechnologies.com"] = [
    {
        "name": "Automotive Mobility & Electric Vehicle Services",
        "display_name": "Automotive Mobility & Electric Vehicle Services",
        "filters": {
            "q_organization_keyword_tags": ["automotive", "electric vehicles", "commercial fleet", "auto repair"],
            "person_locations": ["United States", "Australia", "New Zealand", "Singapore"],
            "person_seniorities": ["c_suite", "vice_president", "owner", "director"]
        }
    }
]

# Account 8: RECRUITING@NESTACK.COM
complete_saved_searches["recruiting@nestack.com"] = [
    {
        "name": "Technical Talent & Engineering Leadership",
        "display_name": "Technical Talent & Engineering Leadership",
        "filters": {
            "person_titles": ["VP Engineering", "Director of Engineering", "CTO", "Head of Talent", "Recruiting Manager"],
            "person_locations": ["United States"],
            "organization_num_employees_ranges": ["51,200", "201,500", "501,1000"]
        }
    }
]

# Account 10: RAHUL@NESTACK-TECH.COM
complete_saved_searches["rahul@nestack-tech.com"] = [
    {
        "name": "Commercial Real Estate & PropTech Leadership",
        "display_name": "Commercial Real Estate & PropTech Leadership",
        "filters": {
            "q_organization_keyword_tags": ["real estate services", "proptech", "commercial real estate", "property management"],
            "person_locations": ["United States", "Australia", "Singapore", "New Zealand"],
            "person_seniorities": ["c_suite", "vice_president", "owner", "partner"]
        }
    }
]

# Account 12: VRAGHAVAN@NESTACK.COM
complete_saved_searches["vraghavan@nestack.com"] = [
    {
        "name": "Banking, Consumer Lending & Capital Markets",
        "display_name": "Banking, Consumer Lending & Capital Markets",
        "filters": {
            "q_organization_keyword_tags": ["banking", "consumer finance", "commercial lending", "capital markets", "wealth management"],
            "person_locations": ["United States", "Australia", "New Zealand", "Singapore"],
            "person_seniorities": ["c_suite", "vice_president", "director"]
        }
    }
]

# Account 13: VRAGHAVAN@NESTACKTECH.COM
complete_saved_searches["vraghavan@nestacktech.com"] = [
    {
        "name": "Supply Chain & Automotive Fleet Logistics",
        "display_name": "Supply Chain & Automotive Fleet Logistics",
        "filters": {
            "q_organization_keyword_tags": ["automotive supply chain", "fleet logistics", "freight forwarding", "transportation"],
            "person_locations": ["United States", "Singapore", "Australia"],
            "person_seniorities": ["c_suite", "director", "vice_president", "owner"]
        }
    }
]

# Account 14: MADHAVA.REDDY@NESTACK-TECH.COM
complete_saved_searches["madhava.reddy@nestack-tech.com"] = [
    {
        "name": "Enterprise Cloud Architecture & Software Engineering",
        "display_name": "Enterprise Cloud Architecture & Software Engineering",
        "filters": {
            "person_titles": ["VP Engineering", "Cloud Architect", "Chief Technology Officer", "Director of IT", "Head of Software"],
            "person_locations": ["United States", "Canada"],
            "organization_num_employees_ranges": ["51,200", "201,500", "501,1000"]
        }
    }
]

# Account 15: RCHANDRAN@NESTACK.INFO
complete_saved_searches["rchandran@nestack.info"] = [
    {
        "name": "B2B SaaS & Tech Founders / C-Suite",
        "display_name": "B2B SaaS & Tech Founders / C-Suite",
        "filters": {
            "person_titles": ["Founder", "Co-Founder", "Chief Executive Officer", "President", "Managing Director"],
            "person_locations": ["United States", "United Kingdom"],
            "organization_num_employees_ranges": ["11,50", "51,200"]
        }
    }
]

# Account 16: MADHAVA.REDDY@NESTACKTECH.COM
complete_saved_searches["madhava.reddy@nestacktech.com"] = [
    {
        "name": "Cybersecurity & DevOps Infrastructure Leadership",
        "display_name": "Cybersecurity & DevOps Infrastructure Leadership",
        "filters": {
            "person_titles": ["CISO", "Chief Information Security Officer", "VP DevOps", "Director Cybersecurity", "Head of Security"],
            "person_locations": ["United States"],
            "organization_num_employees_ranges": ["51,200", "201,500"]
        }
    }
]

# Account 17: VIJAY.RAGHAVAN@NESTACKTECH.COM
complete_saved_searches["vijay.raghavan@nestacktech.com"] = [
    {
        "name": "Commercial Fleet Operations & Remarketing Services",
        "display_name": "Commercial Fleet Operations & Remarketing Services",
        "filters": {
            "q_organization_keyword_tags": ["fleet operations", "vehicle remarketing", "auto leasing", "commercial transportation"],
            "person_locations": ["United States", "Australia", "New Zealand", "Singapore"],
            "person_seniorities": ["c_suite", "vice_president", "director", "owner"]
        }
    }
]

# Account 18: VIJAY.RAGHAVAN@NESTACK.NET
complete_saved_searches["vijay.raghavan@nestack.net"] = [
    {
        "name": "Fintech, Payment Systems & Digital Lending",
        "display_name": "Fintech, Payment Systems & Digital Lending",
        "filters": {
            "q_organization_keyword_tags": ["fintech", "payments", "digital lending", "financial services software", "merchant services"],
            "person_locations": ["United States", "Singapore", "Australia"],
            "person_seniorities": ["c_suite", "vice_president", "director"]
        }
    }
]

# Account 19: VRAGHAV@NESTACKTECHNOLOGY.COM
complete_saved_searches["vraghav@nestacktechnology.com"] = [
    {
        "name": "Telecom & Wireless Infrastructure Services",
        "display_name": "Telecom & Wireless Infrastructure Services",
        "filters": {
            "q_organization_keyword_tags": ["telecommunications", "wireless infrastructure", "fiber optics", "network services"],
            "person_locations": ["United States", "Australia", "Singapore"],
            "person_seniorities": ["c_suite", "vice_president", "director", "owner"]
        }
    }
]

# NO "default" key! Each account stands completely independently!

with open(r"config\saved_searches.json", "w", encoding="utf-8") as f:
    json.dump(complete_saved_searches, f, indent=2)

print(f"Successfully generated independent searches for all {len(complete_saved_searches)} accounts!")
for acc_id, (email, searches) in enumerate(complete_saved_searches.items(), 1):
    print(f"[{acc_id:>2}] {email:<35}: {len(searches)} search(es)")
    for s in searches:
        print(f"      • {s['name']}")
