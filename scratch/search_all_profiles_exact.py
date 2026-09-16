import os

search_terms = [
    b"Director IT/Others/NA EST",
    b"Construction Services-saas",
    b"Construction service-Regular",
    b"construction services regular 2",
    b"Director IT",
    b"Services-saas"
]

base_chrome = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

for p_dir in os.listdir(base_chrome):
    if not (p_dir.startswith("Profile") or p_dir == "Default"):
        continue
    full_p = os.path.join(base_chrome, p_dir)
    # Check IndexedDB and Local Storage
    for sub in ["IndexedDB", "Local Storage", "Session Storage", "Local Extension Settings"]:
        sub_path = os.path.join(full_p, sub)
        if not os.path.exists(sub_path):
            continue
        for root, dirs, files in os.walk(sub_path):
            for f in files:
                if f.endswith((".ldb", ".log")):
                    fp_path = os.path.join(root, f)
                    try:
                        with open(fp_path, "rb") as fp:
                            content = fp.read()
                            for term in search_terms:
                                if term in content:
                                    print(f"[{p_dir}] [{sub}] Found {term.decode('utf-8', errors='ignore')} in {f}")
                    except Exception:
                        pass
