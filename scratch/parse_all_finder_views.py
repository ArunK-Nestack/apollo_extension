import os
import re
import json

db_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb"

all_bytes = b""
for f in os.listdir(db_dir):
    if f.endswith((".ldb", ".log")):
        p = os.path.join(db_dir, f)
        try:
            with open(p, "rb") as fp:
                all_bytes += fp.read() + b"\n"
        except Exception:
            pass

# Let's search for patterns like "name" "..." "modality" "people"
pattern = re.compile(rb'\{[^{}]*"id"\s*:\s*"([a-f0-9]{24})"[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*"modality"\s*:\s*"people"', re.IGNORECASE)

# Or broader regex: find all occurrences of 24-char hex id and name
matches = re.findall(rb'"id"\s*:\s*"([a-f0-9]{24})"[^}]{0,200}"name"\s*:\s*"([^"]+)"', all_bytes)
print(f"Direct regex matches: {len(matches)}")
seen = set()
for mid, mname in matches:
    name_str = mname.decode("utf-8", errors="ignore")
    if name_str not in seen:
        seen.add(name_str)
        print(f"  • ID: {mid.decode()} | Name: {name_str}")

# Also look for any occurrence of the specific names from the user's screenshot
target_names = [
    b"Construction Services-saas",
    b"Construction service-Regular",
    b"Director IT/Others/NA EST",
    b"construction services regular 2",
    b"Default view",
    b"My enrichable people"
]

print("\nSearching for screenshot targets:")
for t in target_names:
    t_str = t.decode()
    idx = all_bytes.find(t)
    if idx != -1:
        start = max(0, idx - 200)
        end = min(len(all_bytes), idx + 3000)
        chunk = all_bytes[start:end]
        print(f"\nTarget: '{t_str}' found at index {idx}")
        # Let's find any surrounding JSON object or string tokens
        # Print readable parts
        text = "".join(chr(c) if 32 <= c <= 126 or c in (10, 13) else " " for c in chunk)
        text = re.sub(r"\s+", " ", text)
        print(text[:500])
