import os

targets = [
    b"6112b87dd137dd00a4c7833a",
    b"6112b87cd137dd00a4c782be",
    b"recruiting@nestack.com",
    b"RECRUITING@NESTACK.COM"
]

search_dirs = [
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
    os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles"),
]

for base in search_dirs:
    if not os.path.exists(base):
        continue
    for root, dirs, files in os.walk(base):
        for f in files:
            if f.endswith((".ldb", ".log", ".sqlite")):
                fp = os.path.join(root, f)
                try:
                    with open(fp, "rb") as f_in:
                        data = f_in.read()
                        for t in targets:
                            if t in data:
                                print(f"[MATCH] '{t.decode()}' in {fp} ({len(data)} bytes)")
                                # Find surrounding context
                                pos = data.find(t)
                                chunk = data[max(0, pos-200):pos+400]
                                clean = "".join(chr(c) if 32 <= c <= 126 else " " for c in chunk)
                                print(f"   Context: {clean}")
                                break
                except Exception:
                    pass
