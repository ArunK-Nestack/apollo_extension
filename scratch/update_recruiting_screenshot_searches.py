import json

with open('config/saved_searches.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Define exact 3 searches for recruiting@nestack.com from the screenshot
recruiting_searches = [
    {
        "name": "Visual & Creative Services-saas",
        "display_name": "Visual & Creative Services-saas",
        "filters": {
            "contact_email_status_v2": ["verified"],
            "prospected_by_current_team": ["no"],
            "person_locations": [
                "United States",
                "Australia",
                "New Zealand",
                "Singapore"
            ],
            "q_organization_keyword_tags": [
                "visual design",
                "creative services",
                "graphic design",
                "animation",
                "video production",
                "digital media",
                "advertising",
                "branding",
                "content creation",
                "multimedia",
                "saas",
                "software"
            ],
            "not_account_label_ids": [
                "694f8a9bcb9b8e0021c28859"
            ]
        }
    },
    {
        "name": "President PST/CST/MT/HI/AK/AUS/NZ/SG",
        "display_name": "President PST/CST/MT/HI/AK/AUS/NZ/SG",
        "filters": {
            "contact_email_status_v2": ["verified"],
            "prospected_by_current_team": ["no"],
            "person_locations": [
                "United States",
                "Australia",
                "New Zealand",
                "Singapore"
            ],
            "person_titles": [
                "President",
                "Co-President",
                "Executive President"
            ]
        }
    },
    {
        "name": "Vp/president PST/CST/MT/HI/AK/AUS/NZ/SG",
        "display_name": "Vp/president PST/CST/MT/HI/AK/AUS/NZ/SG",
        "filters": {
            "contact_email_status_v2": ["verified"],
            "prospected_by_current_team": ["no"],
            "person_locations": [
                "United States",
                "Australia",
                "New Zealand",
                "Singapore"
            ],
            "person_titles": [
                "President",
                "Vice President",
                "VP",
                "Executive Vice President",
                "Senior Vice President",
                "Assistant Vice President"
            ]
        }
    }
]

# Strictly set recruiting searches for recruiting@nestack.com
data["recruiting@nestack.com"] = recruiting_searches

# Also set for madhava.reddy accounts if needed or keep isolated
data["madhava.reddy@nestack-tech.com"] = recruiting_searches
data["madhava.reddy@nestacktech.com"] = recruiting_searches

with open('config/saved_searches.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)

print("Updated config/saved_searches.json successfully for recruiting@nestack.com:")
for s in recruiting_searches:
    print(f"  * {s['name']}")
