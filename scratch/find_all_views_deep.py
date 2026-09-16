import os
import re

chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

all_views = {}

for p in os.listdir(chrome_dir):
    p_path = os.path.join(chrome_dir, p)
    if not os.path.isdir(p_path):
        continue
    idb_dir = os.path.join(p_path, "IndexedDB", "https_app.apollo.io_0.indexeddb.leveldb")
    if not os.path.exists(idb_dir):
        continue
    
    views_for_p = []
    for f in os.listdir(idb_dir):
        if not f.endswith((".ldb", ".log")):
            continue
        fp = os.path.join(idb_dir, f)
        try:
            with open(fp, "rb") as f_in:
                data = f_in.read()
                # Find all occurrences of "name" ... "modality" "people"
                # Let's search with regex on bytes
                # Pattern in V8: b'"name"' followed by string bytes, then b'"modality"'
                # Or b'"id"' ... b'"name"' ... b'"modality"'
                for m in re.finditer(rb'"id"\s*"([a-f0-9]{24})"\s*"name"\s*"([^"]+)"\s*"modality"\s*"people"', data):
                    vid = m.group(1).decode()
                    vname = m.group(2).decode("utf-8", errors="ignore")
                    views_for_p.append({"id": vid, "name": vname, "file": f})
                
                # Also try matching when quotes are standard JSON or V8
                for m in re.finditer(rb'"id":\s*"([a-f0-9]{24})",\s*"name":\s*"([^"]+)"', data):
                    vid = m.group(1).decode()
                    vname = m.group(2).decode("utf-8", errors="ignore")
                    views_for_p.append({"id": vid, "name": vname, "file": f})

                # Also search for 'name"' followed by anything up to '"modality'
                for m in re.finditer(rb'name"([^"]{2,100})"modality', data):
                    vname = m.group(1).decode("utf-8", errors="ignore").strip()
                    if vname and not any(vname == x.get("name") for x in views_for_p):
                        views_for_p.append({"id": "unknown", "name": vname, "file": f})
        except Exception:
            pass
    if views_for_p:
        all_views[p] = views_for_p

for p, views in all_views.items():
    print(f"\n[{p}] ({len(views)} views):")
    for v in views:
        print(f"   • {v['name']} (ID: {v['id']})")
