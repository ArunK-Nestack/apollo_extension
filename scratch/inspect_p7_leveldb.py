import os
import re

p = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 7\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb"
for f in os.listdir(p):
    if f.endswith((".ldb", ".log")):
        fp = os.path.join(p, f)
        with open(fp, "rb") as f_in:
            data = f_in.read()
            # Look for any text after "name"
            matches = re.findall(rb'"name"[^\x00-\x1f"]{0,10}"([^"]{2,80})"', data)
            print(f"[{f}] Potential names ({len(matches)}):")
            for m in set(matches):
                m_str = m.decode("utf-8", errors="ignore")
                if any(w in m_str.lower() for w in ['view', 'search', 'recruit', 'coo', 'people', 'director', 'manager', 'regular', 'service', 'talent']):
                    print("  *", m_str)
            # Look for last-selected-view
            lsv = re.findall(rb'last-selected-view[^\"]*\"([a-f0-9]{24})', data)
            if lsv:
                print(f"  last-selected-view: {lsv}")
            # Look for recommendationConfigId
            rc = re.findall(rb'recommendationConfigId[^\"]*\"([a-f0-9]{24})', data)
            if rc:
                print(f"  recommendationConfigId: {rc}")
