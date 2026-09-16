import os
import re
import glob

def scan_dir(base_dir):
    print(f"Scanning {base_dir}...")
    for root, dirs, files in os.walk(base_dir):
        if "leveldb" in root.lower() or "indexeddb" in root.lower():
            for f in files:
                if f.endswith((".ldb", ".log")):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, "rb") as f_in:
                            content = f_in.read()
                            if b"finder-recent-view" in content or b'modality" people' in content or b'modality\x00people' in content or b'"people"' in content:
                                # Find all occurrences of "name" followed by string
                                # In V8 serialization: "name" + single byte or characters + string + "modality"
                                matches = re.findall(rb'"name"[\x00-\x20]*([^\x00-\x1f"]{2,80})[\x00-\x20]*"modality"', content)
                                if matches:
                                    print(f"[{fp}] Matches with modality:")
                                    for m in matches:
                                        print(f"   --> {m.decode('utf-8', errors='ignore')}")
                                
                                # Also find any "finder-recent-view-people-"
                                frv = re.findall(rb'finder-recent-view-people-([a-f0-9]{24})', content)
                                if frv:
                                    print(f"[{fp}] Found finder-recent-view for user IDs: {[x.decode() for x in set(frv)]}")
                    except Exception:
                        pass

scan_dir(r"C:\Users\test\AppData\Local\Google\Chrome\User Data")
scan_dir(r"C:\Users\test\AppData\Local\Microsoft\Edge\User Data")
