import os

search_terms = [
    b"Director IT",
    b"construction services",
    b"Construction Services",
    b"Construction service",
    b"All searches",
    b"Your searches"
]

profile_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16"
target_subdirs = [
    "IndexedDB",
    "Local Storage",
    "Session Storage",
    "Local Extension Settings",
    "Sync Data"
]

print("Targeted scan of Profile 16...", flush=True)

for sub in target_subdirs:
    full_sub = os.path.join(profile_dir, sub)
    if not os.path.exists(full_sub):
        continue
    for root, dirs, files in os.walk(full_sub):
        for f in files:
            path = os.path.join(root, f)
            try:
                with open(path, "rb") as fp:
                    content = fp.read()
                    for term in search_terms:
                        if term in content:
                            print(f"[{sub}] Found '{term.decode()}' in {os.path.basename(path)} ({len(content)} bytes)", flush=True)
            except Exception:
                pass

print("Done scanning targeted directories.", flush=True)
