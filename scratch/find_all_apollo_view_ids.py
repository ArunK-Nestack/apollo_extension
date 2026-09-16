import os
import re

db_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb"

all_data = {}
for f in os.listdir(db_dir):
    if f.endswith((".ldb", ".log")):
        p = os.path.join(db_dir, f)
        try:
            with open(p, "rb") as fp:
                all_data[f] = fp.read()
        except:
            pass

print("Searching across all leveldb files for IDs and nearby text:")
# Look for 6a[0-9a-f]{22} which are recently created 2026 MongoDB IDs in Apollo
for fname, content in all_data.items():
    for m in re.finditer(rb'6a[0-9a-f]{22}', content):
        oid = m.group(0).decode()
        idx = m.start()
        surrounding = content[max(0, idx - 100):min(len(content), idx + 200)]
        text = "".join(chr(c) if 32 <= c <= 126 else " " for c in surrounding)
        text = re.sub(r"\s+", " ", text)
        if any(w in text.lower() for w in ["construct", "director", "saas", "view", "regular", "search", "name"]):
            print(f"[{fname}] ID: {oid} -> {text}")
