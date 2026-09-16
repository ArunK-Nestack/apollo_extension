import os
import re

db_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb"

for f in sorted(os.listdir(db_dir)):
    if f.endswith((".ldb", ".log")):
        p = os.path.join(db_dir, f)
        with open(p, "rb") as fp:
            data = fp.read()
        for term in [b"Director IT", b"Services-saas", b"service-Regular", b"regular 2", b"saved_search", b"finderViewId"]:
            if term in data:
                print(f"[{f}] Found {term.decode('utf-8', errors='ignore')}")
                idx = data.find(term)
                snippet = data[max(0, idx - 100):min(len(data), idx + 400)]
                clean = "".join(chr(c) if 32 <= c <= 126 else " " for c in snippet)
                clean = re.sub(r"\s+", " ", clean)
                print(f"  -> {clean}")
