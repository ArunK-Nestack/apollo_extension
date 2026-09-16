import os
import re
import json

needle_utf16 = "people-table-state".encode("utf-16le")
chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

found_states = {}

for p in os.listdir(chrome_dir):
    p_path = os.path.join(chrome_dir, p)
    if not os.path.isdir(p_path):
        continue
    idb_dir = os.path.join(p_path, "IndexedDB", "https_app.apollo.io_0.indexeddb.leveldb")
    if not os.path.exists(idb_dir):
        continue
    
    for f in os.listdir(idb_dir):
        if not f.endswith((".ldb", ".log")):
            continue
        fp = os.path.join(idb_dir, f)
        try:
            with open(fp, "rb") as f_in:
                data = f_in.read()
                pos = 0
                while True:
                    idx = data.find(needle_utf16, pos)
                    if idx == -1:
                        break
                    pos = idx + len(needle_utf16)
                    chunk = data[idx:idx+10000]
                    # Find last-selected-view
                    lsv = re.search(rb'last-selected-view"[\x00-\x20]*([a-f0-9]{24})', chunk)
                    view_id = lsv.group(1).decode() if lsv else None
                    # Find any viewId-userId pattern
                    v_u = re.findall(rb'([a-f0-9]{24})-([a-f0-9]{24})', chunk)
                    found_states.setdefault(p, []).append({
                        "file": f,
                        "view_id": view_id,
                        "view_user_pairs": [(v.decode(), u.decode()) for v, u in v_u]
                    })
        except Exception:
            pass

for p, items in found_states.items():
    print(f"\n[{p}] ({len(items)} instances):")
    for it in items[:3]:
        print(f"   View: {it['view_id']} | Pairs: {it['view_user_pairs']}")
