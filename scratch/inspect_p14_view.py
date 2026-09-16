fp = r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 14\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb\001385.log"
with open(fp, "rb") as f:
    d = f.read()

pos = d.find(b"6630c33f78e7df01c7cd4306")
print(f"Found at {pos}")
chunk = d[max(0, pos-500):pos+1500]
text = "".join(chr(c) if 32 <= c <= 126 or c in (10, 13) else " " for c in chunk)
print(text)
