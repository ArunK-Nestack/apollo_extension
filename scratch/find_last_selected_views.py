import os
import re

chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"
pattern = re.compile(rb'last-selected-view"[^"]*"([a-f0-9]{24})')

for p in os.listdir(chrome_dir):
    p_path = os.path.join(chrome_dir, p)
    if not os.path.isdir(p_path):
        continue
    idb = os.path.join(p_path, "IndexedDB")
    if not os.path.exists(idb):
        continue
    for root, dirs, files in os.walk(idb):
        if "apollo" in root.lower():
            for f in files:
                if f.endswith((".ldb", ".log")):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, "rb") as f_in:
                            content = f_in.read()
                            matches = pattern.findall(content)
                            if matches:
                                print(f"[{p}] {f}: {list(set(m.decode() for m in matches))}")
                    except Exception:
                        pass
