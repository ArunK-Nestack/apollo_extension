import os
import shutil
import tempfile

p = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb"
for f in os.listdir(p):
    fp = os.path.join(p, f)
    if os.path.isfile(fp):
        try:
            with open(fp, "rb") as f_in:
                content = f_in.read()
                if b"Construction Services-saas" in content:
                    print(f"Direct open: {f}, size {len(content)}")
        except Exception as e:
            # Try copying to temp
            try:
                temp_dir = tempfile.gettempdir()
                tf = os.path.join(temp_dir, f"test_{f}")
                # Use powershell or cmd copy if needed
                print(f"Locked: {f} ({e})")
            except Exception:
                pass
