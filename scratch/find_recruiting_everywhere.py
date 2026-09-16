import os
import re

targets = [
    b"6112b87dd137dd00a4c7833a",
    b"6112b87cd137dd00a4c782be",
    b"recruiting@nestack.com",
    b"RECRUITING@NESTACK.COM",
    b"bF9xb0EUc1WlP5vhY4pxpA"
]

search_roots = [
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
    os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles"),
    r"c:\Users\test\Desktop\projects\apollo_extension",
    r"c:\Users\test\Downloads",
]

found = []

for root_dir in search_roots:
    if not os.path.exists(root_dir):
        continue
    print(f"Scanning {root_dir}...")
    for root, dirs, files in os.walk(root_dir):
        # Skip node_modules, .git
        if any(x in root.lower() for x in [".git", "node_modules", ".venv", "__pycache__"]):
            continue
        for f in files:
            fp = os.path.join(root, f)
            try:
                if os.path.getsize(fp) > 40 * 1024 * 1024:
                    continue
                with open(fp, "rb") as f_in:
                    content = f_in.read()
                    for t in targets:
                        if t in content:
                            found.append((t.decode(), fp))
                            print(f"  [FOUND] '{t.decode()}' in {fp}")
                            break
            except Exception:
                pass

print(f"\nTotal matches found: {len(found)}")
