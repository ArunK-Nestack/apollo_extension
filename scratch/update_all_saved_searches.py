import json
import os

with open('config/saved_searches.json', 'r', encoding='utf-8') as f:
    existing = json.load(f)

# Extract core reusable filter sets
cs_regular = [s for s in existing.get('rahul@nestack.co.in', []) if s['name'] == 'Construction service-Regular'][0]
cs_saas = [s for s in existing.get('rahul@nestack.co.in', []) if s['name'] == 'Construction Services-saas'][0]
cs_reg2 = [s for s in existing.get('rahul@nestack.co.in', []) if s['name'] == 'construction services regular 2'][0]
dir_it = [s for s in existing.get('rahul@nestack.co.in', []) if s['name'] == 'Director IT/Others/NA EST'][0]

auto = [s for s in existing.get('vijay@nestacktech.com', []) if s['name'] == 'Automotive'][0]
chro = [s for s in existing.get('vijay@nestacktech.com', []) if 'CHRO' in s['name']][0]

cso = [s for s in existing.get('jith@nestack.info', []) if 'CSO' in s['name']][0]
re_saas = [s for s in existing.get('rahul.chandran@nestack-tech.com', []) if 'real estate' in s['name']][0]
adv = [s for s in existing.get('rahul@nestacktechnology.com', []) if 'Advertising' in s['name']][0]

# Build clean recruiting filters (remove foreign account/contact labels to avoid 422)
recruiting_cs_regular_filters = {k: v for k, v in cs_regular['filters'].items() if k not in ['not_account_label_ids', 'not_contact_label_ids']}
recruiting_cs_saas_filters = {k: v for k, v in cs_saas['filters'].items() if k not in ['not_account_label_ids', 'not_contact_label_ids']}
recruiting_cs_reg2_filters = {k: v for k, v in cs_reg2['filters'].items() if k not in ['not_account_label_ids', 'not_contact_label_ids']}

# Build test coding search filters
test_coding_filters = {
    'contact_email_status_v2': ['verified'],
    'person_locations': ['United States', 'New Zealand', 'Australia', 'Singapore'],
    'prospected_by_current_team': ['no'],
    'person_titles': [
        'SDET',
        'Software Test Engineer',
        'QA Engineer',
        'Automation Test Engineer',
        'Software Developer in Test',
        'Test Automation Engineer',
        'QA Automation Engineer',
        'Software Engineer',
        'Full Stack Developer'
    ],
    'q_keywords': 'coding'
}

# Founder search
founder_search_filters = {
    'contact_email_status_v2': ['verified'],
    'person_locations': ['United States', 'New Zealand', 'Australia', 'Singapore'],
    'prospected_by_current_team': ['no'],
    'person_titles': ['Founder', 'Co-Founder', 'Owner', 'Co-Owner'],
    'organization_num_employees_ranges': ['4,10']
}

recruiting_searches = [
    {
        'name': 'Construction service-Regular',
        'display_name': 'Construction service-Regular',
        'filters': recruiting_cs_regular_filters
    },
    {
        'name': 'test coding',
        'display_name': 'test coding',
        'filters': test_coding_filters
    },
    {
        'name': 'Construction Services-saas',
        'display_name': 'Construction Services-saas',
        'filters': recruiting_cs_saas_filters
    },
    {
        'name': 'construction services regular 2',
        'display_name': 'construction services regular 2',
        'filters': recruiting_cs_reg2_filters
    }
]

# Build comprehensive mapping for all accounts
updated_searches = {
    'rahul@nestack.co.in': [
        cs_saas,
        cs_regular,
        dir_it,
        cs_reg2
    ],
    'recruiting@nestack.com': recruiting_searches,
    'madhava.reddy@nestack-tech.com': recruiting_searches,
    'madhava.reddy@nestacktech.com': recruiting_searches,
    
    'vijay@nestacktech.com': [auto, chro],
    'vijay.raghavan@nestack.com': [auto, chro],
    'vijay.raghavan@nestacktechnologies.com': [auto, chro],
    'vijay.raghavan@nestacktech.com': [auto, chro],
    'vijay.raghavan@nestack.net': [auto, chro],
    'vraghavan@nestack.com': [auto, chro],
    'vraghavan@nestacktech.com': [auto, chro],
    'vraghav@nestacktechnology.com': [auto, chro],
    
    'jith@nestack.info': [cso],
    
    'rahul.chandran@nestack-tech.com': [re_saas, dir_it, cs_regular],
    'rahul@nestacktechnology.com': [adv, dir_it, cs_regular],
    'rahul@nestaktechnology.com': [adv, dir_it, cs_regular],
    'rahul@nestack-tech.com': [
        {
            'name': 'Founder others/IT/NA est/cst/hi/ak 4-10',
            'display_name': 'Founder others/IT/NA est/cst/hi/ak 4-10',
            'filters': founder_search_filters
        },
        re_saas,
        dir_it,
        cs_regular
    ],
    'rchandran@nestack.biz': [re_saas, dir_it, cs_regular],
    'rchandran@nestack.info': [re_saas, dir_it, cs_regular],
    
    'abel.abraham@nestacktechnologies.com': [
        {
            'name': 'Construction service-Regular',
            'display_name': 'Construction service-Regular',
            'filters': recruiting_cs_regular_filters
        },
        {
            'name': 'President/VP from 11 oct 2023 - 8 jan 2024',
            'display_name': 'President/VP from 11 oct 2023 - 8 jan 2024',
            'filters': {
                'contact_email_status_v2': ['verified'],
                'person_locations': ['United States', 'New Zealand', 'Australia', 'Singapore'],
                'prospected_by_current_team': ['no'],
                'person_titles': ['President', 'Vice President', 'VP', 'Executive Vice President']
            }
        }
    ]
}

with open('config/saved_searches.json', 'w', encoding='utf-8') as f:
    json.dump(updated_searches, f, indent=2)

print(f"Successfully configured searches for {len(updated_searches)} accounts in config/saved_searches.json.")
for acc, searches in updated_searches.items():
    snames = [s['name'] for s in searches]
    print(f"  {acc:<40}: {snames}")
