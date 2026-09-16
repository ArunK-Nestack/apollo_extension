import os
import re

needle_utf16 = "finder-recent-view".encode("utf-16le")
needle_ascii = b"finder-recent-view"

chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"
for root, dirs, files in os.walk(chrome_dir):
    if "apollo" in root.lower():
        for f in files:
            if f.endswith((".ldb", ".log")):
                fp = os.path.join(root, f)
                try:
                    with open(fp, "rb") as f_in:
                        data = f_in.read()
                        pos_16 = data.find(needle_utf16)
                        pos_8 = data.find(needle_ascii)
                        if pos_16 != -1 or pos_8 != -1:
                            print(f"[FOUND] {fp} (utf16: {pos_16}, ascii: {pos_8})")
                except Exception as e:
                    pass
