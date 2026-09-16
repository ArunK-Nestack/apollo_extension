import os

needle = b"Construction Services-saas"
chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

print("Searching for needle in Chrome User Data...")
found = []
for root, dirs, files in os.walk(chrome_dir):
    for f in files:
        fp = os.path.join(root, f)
        try:
            # Only check files under 50MB
            if os.path.getsize(fp) > 50 * 1024 * 1024:
                continue
            with open(fp, "rb") as f_in:
                content = f_in.read()
                if needle in content:
                    found.append((fp, len(content)))
                    print(f"FOUND in: {fp} (size: {len(content)})")
        except Exception:
            pass

print(f"Total found: {len(found)}")
