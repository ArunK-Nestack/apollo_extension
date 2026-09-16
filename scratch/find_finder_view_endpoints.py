import os
import re

chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"
pattern = re.compile(rb'[\'"](/api/v1/[^\'"]*finder_view[^\'"]*)[\'"]', re.IGNORECASE)
pattern2 = re.compile(rb'[\'"](/api/v1/[^\'"]*view[^\'"]*)[\'"]', re.IGNORECASE)

matches = set()
for root, dirs, files in os.walk(chrome_dir):
    if "cache" in root.lower() or "service worker" in root.lower():
        for f in files:
            fp = os.path.join(root, f)
            try:
                if os.path.getsize(fp) > 20 * 1024 * 1024:
                    continue
                with open(fp, "rb") as f_in:
                    content = f_in.read()
                    for m in pattern.finditer(content):
                        matches.add(m.group(1).decode(errors='ignore'))
            except Exception:
                pass

print(f"Found {len(matches)} finder_view endpoints:")
for m in sorted(matches):
    print("  ", m)
