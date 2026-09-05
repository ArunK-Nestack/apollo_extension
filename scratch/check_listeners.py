import re

with open("extensions/content.js", "r", encoding="utf-8") as f:
    lines = f.readlines()

print("Checking addEventListener calls in content.js:")
for i, l in enumerate(lines):
    if "addEventListener" in l:
        prev = lines[i-1].strip() if i > 0 else ""
        print(f"Line {i+1}: {prev} --> {l.strip()}")
