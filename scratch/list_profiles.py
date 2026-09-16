import os
import json

chrome_base = os.path.expanduser('~') + r'\AppData\Local\Google\Chrome\User Data'
edge_base = os.path.expanduser('~') + r'\AppData\Local\Microsoft\Edge\User Data'

for name, base in [('Chrome', chrome_base), ('Edge', edge_base)]:
    if not os.path.exists(base):
        continue
    for p in os.listdir(base):
        pref_file = os.path.join(base, p, 'Preferences')
        if os.path.exists(pref_file):
            try:
                with open(pref_file, 'r', encoding='utf-8', errors='ignore') as f:
                    data = json.load(f)
                    acc = data.get('account_info', [])
                    email = acc[0].get('email', '') if acc else ''
                    name_prof = data.get('profile', {}).get('name', '')
                    user_name = acc[0].get('full_name', '') if acc else ''
                    print(f'{name} [{p}]: profile_name="{name_prof}", email="{email}", user="{user_name}"')
            except Exception as e:
                pass
