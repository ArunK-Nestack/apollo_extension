import os
import re

chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

# Let's look inside Service Worker cache or ScriptCache
endpoints = set()
for root, dirs, files in os.walk(chrome_dir):
    if "apollo" in root.lower() or "cache" in root.lower():
        for f in files:
            if f.endswith((".js", ".bundle")) or "data_" in f or f.startswith("f_"):
                fp = os.path.join(root, f)
                try:
                    if os.path.getsize(fp) > 20 * 1024 * 1024:
                        continue
                    with open(fp, "rb") as f_in:
                        content = f_in.read()
                        if b"finder-recent-view" in content or b"recommendationConfig" in content:
                            matches = re.findall(rb'/(?:api/v1/|api/)[a-zA-Z0-9_/]+', content)
                            for m in matches:
                                m_str = m.decode(errors='ignore')
                                if any(w in m_str for w in ['view', 'search', 'preset', 'saved', 'rec', 'finder']):
                                    endpoints.add(m_str)
                except Exception:
                    pass

print(f"Discovered candidate endpoints ({len(endpoints)}):")
for ep in sorted(endpoints):
    print("  ", ep)
