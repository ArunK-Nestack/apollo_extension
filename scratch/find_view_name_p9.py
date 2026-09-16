import os

p = r'C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 9\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb'
for f in os.listdir(p):
    fp = os.path.join(p, f)
    if not os.path.isfile(fp):
        continue
    with open(fp, 'rb') as fl:
        data = fl.read()
        idx = 0
        while True:
            idx = data.find(b'6a885bea', idx)
            if idx == -1:
                break
            snippet = data[max(0, idx - 100):min(len(data), idx + 200)]
            print(f, f"found at {idx}:", snippet)
            idx += 8
