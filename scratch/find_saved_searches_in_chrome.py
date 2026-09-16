import os

search_terms = [
    b"Director IT/Others/NA EST",
    b"construction services regular 2",
    b"Construction Services-saas",
    b"Construction service-Regular",
    b"Create saved search",
    b"My enrichable people"
]

profile_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16"
print("Scanning Profile 16...")
matches = []

for root, dirs, files in os.walk(profile_dir):
    for f in files:
        path = os.path.join(root, f)
        try:
            with open(path, "rb") as fp:
                content = fp.read()
                for term in search_terms:
                    if term in content:
                        print(f"Found '{term.decode('utf-8', errors='ignore')}' in: {path} (size {len(content):,d} bytes)")
                        matches.append((path, term))
        except Exception:
            pass

print(f"\nTotal matches found: {len(matches)}")
