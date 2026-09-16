with open(r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Profile 16\IndexedDB\https_app.apollo.io_0.indexeddb.leveldb\043010.ldb", "rb") as f:
    data = f.read()

idx = data.find(b"last-selected-view")
print(f"Found 'last-selected-view' at {idx}")
start = max(0, idx - 500)
end = min(len(data), idx + 15000)
chunk = data[start:end]

# Let's decode or print with non-printables escaped
out_str = ""
for b in chunk:
    if 32 <= b <= 126 or b in (10, 13, 9):
        out_str += chr(b)
    else:
        out_str += f"\\x{b:02x}"

with open(r"scratch\last_selected_view_dump.txt", "w", encoding="utf-8") as f:
    f.write(out_str)

print("Dumped to scratch/last_selected_view_dump.txt")
