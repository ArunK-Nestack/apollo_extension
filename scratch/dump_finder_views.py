import os
import re
import json

needle_utf16 = "finder-recent-view".encode("utf-16le")

chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

results = {}

for p in os.listdir(chrome_dir):
    p_path = os.path.join(chrome_dir, p)
    if not os.path.isdir(p_path):
        continue
    idb_dir = os.path.join(p_path, "IndexedDB", "https_app.apollo.io_0.indexeddb.leveldb")
    if not os.path.exists(idb_dir):
        continue
    
    print(f"\n==========================================")
    print(f"Checking Profile: {p}")
    print(f"==========================================")
    
    for f in os.listdir(idb_dir):
        if not f.endswith((".ldb", ".log")):
            continue
        fp = os.path.join(idb_dir, f)
        try:
            with open(fp, "rb") as f_in:
                data = f_in.read()
        except Exception:
            continue
        
        # Search for all occurrences of needle_utf16
        idx = 0
        while True:
            pos = data.find(needle_utf16, idx)
            if pos == -1:
                break
            idx = pos + len(needle_utf16)
            # Grab chunk
            chunk = data[pos:pos+15000]
            # Convert readable characters
            clean_str = "".join(chr(c) if 32 <= c <= 126 else " " for c in chunk)
            # Find all patterns like "name" ... "modality" "people"
            # In V8: "name" [junk] "Search Name" [junk] "modality" "people"
            views = re.findall(r'"name"\s*([A-Za-z0-9\s\-_/&,\.\*\+]+?)"modality"', clean_str)
            print(f"  [{f} at {pos}] Found raw views: {views}")
            # Also let's print a sample of the text
            print(f"     Sample: {clean_str[:400]}")
