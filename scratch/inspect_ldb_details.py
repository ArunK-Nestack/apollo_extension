import os
import re

ldb_file = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb\043010.ldb"

with open(ldb_file, "rb") as f:
    data = f.read()

print(f"File size: {len(data):,d} bytes")

# Find all occurrences of "finder-"
for m in re.finditer(rb'finder-[a-zA-Z0-9_-]+', data):
    print("Found key:", m.group(0).decode("ascii", errors="ignore"))

# Find all occurrences of "name":"..." where nearby is "modality"
for m in re.finditer(rb'"name"\s*:\s*"([^"]+)"', data):
    nearby = data[max(0, m.start() - 100):min(len(data), m.end() + 200)]
    if b'modality' in nearby or b'people' in nearby or b'view' in nearby or b'search' in nearby:
        print(f"View Name: {m.group(1).decode('utf-8', errors='ignore')}")

# Specifically look for 'Construction', 'Director', 'saas', 'Regular'
for kw in [b'Construction', b'Director', b'saas', b'Regular', b'EST']:
    matches = [m.start() for m in re.finditer(kw, data, re.IGNORECASE)]
    print(f"Keyword '{kw.decode()}' occurrences: {len(matches)}")
    for idx in matches[:5]:
        start = max(0, idx - 40)
        end = min(len(data), idx + 80)
        snippet = data[start:end]
        text = "".join(chr(c) if 32 <= c <= 126 else "." for c in snippet)
        print(f"  [{idx}] {text}")
