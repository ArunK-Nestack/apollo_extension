import os

uid = b'6112b87dd137dd00a4c7833a'
uid_rot1 = b'7223ce98ee248ee11b5d8944b'
email = b'recruiting@nestack.com'

search_dirs = [
    os.path.expanduser('~') + r'\AppData\Local\Google\Chrome\User Data',
    os.path.expanduser('~') + r'\AppData\Local\Microsoft\Edge\User Data',
    os.path.expanduser('~') + r'\AppData\Roaming\Mozilla\Firefox\Profiles',
    os.path.expanduser('~') + r'\Downloads',
    os.path.expanduser('~') + r'\Desktop',
    os.path.expanduser('~') + r'\Documents'
]

matches = []

for base in search_dirs:
    if not os.path.exists(base):
        continue
    for root, dirs, files in os.walk(base):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ['.ldb', '.log', '.sqlite', '.sqlite-wal', '.json', '.txt', '.csv', '.xml', '']:
                fp = os.path.join(root, f)
                try:
                    with open(fp, 'rb') as fl:
                        data = fl.read()
                        has_uid = uid in data
                        has_rot = uid_rot1 in data
                        has_em = email in data.lower()
                        if has_uid or has_rot or has_em:
                            matches.append((fp, has_uid, has_rot, has_em, len(data)))
                            print(f"[FOUND] uid={has_uid}, rot={has_rot}, email={has_em} in {fp}")
                except Exception:
                    pass

print(f"Total files matched: {len(matches)}")
