with open("extensions/content.js", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if "SYNC_SAVED_LEADS" in l:
        start = max(0, i - 10)
        end = min(len(lines), i + 35)
        print(f"--- Lines {start+1} to {end+1} ---")
        for j in range(start, end):
            print(f"{j+1}: {lines[j]}", end="")
