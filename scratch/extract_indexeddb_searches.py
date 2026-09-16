import os
import re
import json

db_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb"

search_names = [
    "Construction Services-saas",
    "Construction service-Regular",
    "Director IT/Others/NA EST",
    "construction services regular 2"
]

all_bytes = b""
for f in os.listdir(db_dir):
    if f.endswith((".ldb", ".log")):
        p = os.path.join(db_dir, f)
        try:
            with open(p, "rb") as fp:
                all_bytes += fp.read() + b"\n"
        except Exception:
            pass

print(f"Read {len(all_bytes):,d} bytes from IndexedDB.")

for name in search_names:
    b_name = name.encode("utf-8")
    pos = 0
    found_count = 0
    while True:
        idx = all_bytes.find(b_name, pos)
        if idx == -1:
            break
        found_count += 1
        # Extract surrounding context (1000 bytes before and after)
        start = max(0, idx - 500)
        end = min(len(all_bytes), idx + 2500)
        chunk = all_bytes[start:end]
        
        # Look for JSON structures
        # Try to find { ... } around idx
        print(f"\n=======================================================")
        print(f"MATCH FOR '{name}' (occurrence {found_count}):")
        print(f"=======================================================")
        # Print printable text
        printable = "".join(chr(c) if 32 <= c <= 126 or c in (10, 13, 9) else " " for c in chunk)
        # Collapse multiple spaces
        printable = re.sub(r" +", " ", printable)
        print(printable[:800])
        pos = idx + len(b_name)
        if found_count >= 2:
            break
