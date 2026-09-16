with open("scripts/apollo_search_direct.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for idx, line in enumerate(lines, 1):
    if any(k in line for k in ["requests.", "http", "url", "api.apollo"]):
        print(f"L{idx}: {line.strip()[:100]}")
