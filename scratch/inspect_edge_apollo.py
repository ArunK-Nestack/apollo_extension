import os
import re

edge_base = os.path.expanduser('~') + r'\AppData\Local\Microsoft\Edge\User Data\Default'

targets = [
    os.path.join(edge_base, 'IndexedDB', 'https_app.apollo.io_0.indexeddb.leveldb'),
    os.path.join(edge_base, 'Local Storage', 'leveldb')
]

for t in targets:
    if not os.path.exists(t):
        continue
    print(f"=== Inspecting {t} ===")
    for f in os.listdir(t):
        if f.endswith(('.ldb', '.log')):
            fp = os.path.join(t, f)
            try:
                with open(fp, 'rb') as fl:
                    data = fl.read()
                    # Look for any email
                    emails = set(re.findall(rb'[a-zA-Z0-9_.+-]+@nestack[a-zA-Z0-9_.-]+', data))
                    if emails:
                        print(f"  {f} emails:", emails)
                    # Look for finder_views or saved searches
                    for m in re.finditer(rb'finder[^\x00]{1,60}', data, re.IGNORECASE):
                        print(f"  {f} finder:", m.group(0)[:60])
                    # Look for "name" followed by string
                    for m in re.finditer(rb'\"name\"[^\x00]{2,60}', data):
                        print(f"  {f} name:", m.group(0)[:60])
            except Exception as e:
                pass
