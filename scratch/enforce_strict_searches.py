import json

with open('config/saved_searches.json', 'r', encoding='utf-8') as f:
    current = json.load(f)

# Strictly isolate searches to their true creator accounts
strict_searches = {
    "rahul@nestack.co.in": current.get("rahul@nestack.co.in", []),
    "recruiting@nestack.com": [
        {
            "name": "Visual & Creative Services-saas",
            "display_name": "Visual & Creative Services-saas",
            "filters": {
                "contact_email_status_v2": ["verified"],
                "prospected_by_current_team": ["no"],
                "person_locations": ["United States", "Australia", "New Zealand", "Singapore"],
                "q_organization_keyword_tags": [
                    "visual design", "creative services", "graphic design", "animation",
                    "video production", "digital media", "advertising", "branding",
                    "content creation", "multimedia", "saas", "software"
                ],
                "not_account_label_ids": ["694f8a9bcb9b8e0021c28859"]
            }
        },
        {
            "name": "President PST/CST/MT/HI/AK/AUS/NZ/SG",
            "display_name": "President PST/CST/MT/HI/AK/AUS/NZ/SG",
            "filters": {
                "contact_email_status_v2": ["verified"],
                "prospected_by_current_team": ["no"],
                "person_locations": ["United States", "Australia", "New Zealand", "Singapore"],
                "person_titles": ["President", "Co-President", "Executive President"]
            }
        },
        {
            "name": "Vp/president PST/CST/MT/HI/AK/AUS/NZ/SG",
            "display_name": "Vp/president PST/CST/MT/HI/AK/AUS/NZ/SG",
            "filters": {
                "contact_email_status_v2": ["verified"],
                "prospected_by_current_team": ["no"],
                "person_locations": ["United States", "Australia", "New Zealand", "Singapore"],
                "person_titles": ["President", "Vice President", "VP", "Executive Vice President", "Senior Vice President", "Assistant Vice President"]
            }
        }
    ],
    "vijay@nestacktech.com": [
        s for s in current.get("vijay@nestacktech.com", [])
    ],
    "jith@nestack.info": [
        s for s in current.get("jith@nestack.info", [])
    ],
    "rahul.chandran@nestack-tech.com": [
        s for s in current.get("rahul.chandran@nestack-tech.com", []) if s.get("name") == "real estate saas"
    ],
    "rahul@nestacktechnology.com": [
        s for s in current.get("rahul@nestacktechnology.com", []) if "Advertising" in s.get("name", "")
    ],
    "rahul@nestaktechnology.com": [
        s for s in current.get("rahul@nestaktechnology.com", []) if "Advertising" in s.get("name", "")
    ],
    "rahul@nestack-tech.com": [
        s for s in current.get("rahul@nestack-tech.com", []) if "Founder" in s.get("name", "")
    ]
}

with open('config/saved_searches.json', 'w', encoding='utf-8') as f:
    json.dump(strict_searches, f, indent=2)

print("Saved strict, unpolluted searches:")
for k, v in strict_searches.items():
    print(f"  {k}: {[s['name'] for s in v]}")
