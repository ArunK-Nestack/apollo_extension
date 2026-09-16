import os
import re
import json
import sqlite3
import shutil
import urllib.parse

# 19 accounts with their verified user IDs
user_ids_to_account = {
    "611205633da89500f845a1b9": {"id": 1, "email": "abel.abraham@nestacktechnologies.com", "name": "Abel Abraham"},
    "606c4cc360a23900e25e4892": {"id": 2, "email": "VIJAY.RAGHAVAN@NESTACKTECHNOLOGIES.COM", "name": "Vijay Raghavan"},
    "6108c2817f6f7900a4e8bdca": {"id": 3, "email": "RAHUL.CHANDRAN@NESTACK-TECH.COM", "name": "Rahul Chandran"},
    "60bf62785f253a00f9445cb4": {"id": 4, "email": "VIJAY@NESTACKTECH.COM", "name": "Vijay"},
    "6112ab9707bf2a00da5d4625": {"id": 5, "email": "JITH@NESTACK.INFO", "name": "Jith"},
    "6113fdd98c8563008c94352f": {"id": 6, "email": "RAHUL@NESTACK.CO.IN", "name": "Rahul"},
    "60c6ecf9bcccaf00f60db0b4": {"id": 7, "email": "VIJAY.RAGHAVAN@NESTACK.COM", "name": "Vijay Raghavan"},
    "6112b87dd137dd00a4c7833a": {"id": 8, "email": "RECRUITING@NESTACK.COM", "name": "Recruiting"},
    "60f47b45f071c600daa4ee32": {"id": 9, "email": "RAHUL@NESTAKTECHNOLOGY.COM", "name": "Rahul"},
    "612c694e30fa8b00da35b0c3": {"id": 10, "email": "RAHUL@NESTACK-TECH.COM", "name": "Rahul"},
    "6119c60f4561c700a440c67b": {"id": 11, "email": "RCHANDRAN@NESTACK.BIZ", "name": "R Chandran"},
    "607d536db0991a00a4447a6a": {"id": 12, "email": "VRAGHAVAN@NESTACK.COM", "name": "V Raghavan"},
    "6093680d9a80be00a4639f9c": {"id": 13, "email": "VRAGHAVAN@NESTACKTECH.COM", "name": "V Raghavan"},
    "610cbd07aebce500a42487ff": {"id": 14, "email": "MADHAVA.REDDY@NESTACK-TECH.COM", "name": "Madhava Reddy"},
    "60f47e19afa22800a5dfee60": {"id": 15, "email": "RCHANDRAN@NESTACK.INFO", "name": "R Chandran"},
    "6108ce39f7b28400a5c3cac5": {"id": 16, "email": "MADHAVA.REDDY@NESTACKTECH.COM", "name": "Madhava Reddy"},
    "611ef797b7e4fa00dbb8c57c": {"id": 17, "email": "VIJAY.RAGHAVAN@NESTACKTECH.COM", "name": "Vijay Raghavan"},
    "60fe0e53536d0b0130770afc": {"id": 18, "email": "VIJAY.RAGHAVAN@NESTACK.NET", "name": "Vijay Raghavan"},
    "606c4cc360a23900e25e4892": {"id": 19, "email": "VRAGHAV@NESTACKTECHNOLOGY.COM", "name": "V Raghav"}
}

print(f"Scanning storage for {len(user_ids_to_account)} user IDs...")

found_views_by_account = {acc["email"].lower(): [] for acc in user_ids_to_account.values()}

chrome_base = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"

# Search all Chrome LevelDB files
for root, dirs, files in os.walk(chrome_base):
    if "https_app.apollo.io_0.indexeddb.leveldb" in root:
        for f in files:
            if f.endswith((".ldb", ".log")):
                fp = os.path.join(root, f)
                try:
                    with open(fp, "rb") as bfp:
                        content = bfp.read()
                        for uid, acc in user_ids_to_account.items():
                            b_uid = uid.encode("ascii")
                            if b_uid in content:
                                # Found this user ID in this leveldb!
                                # Look for view names near this user ID
                                pos = 0
                                while True:
                                    idx = content.find(b_uid, pos)
                                    if idx == -1:
                                        break
                                    pos = idx + len(b_uid)
                                    chunk = content[max(0, idx - 400):min(len(content), idx + 800)]
                                    text = "".join(chr(c) if 32 <= c <= 126 else " " for c in chunk)
                                    # Extract name" ... "
                                    m = re.search(r'name"\s*([^"]+)"', text)
                                    if m:
                                        v_name = m.group(1).strip()
                                        if len(v_name) > 3 and not any(w in v_name.lower() for w in ["default view", "enrichable", "schema", "table"]):
                                            found_views_by_account[acc["email"].lower()].append({
                                                "name": v_name,
                                                "source": fp,
                                                "user_id": uid
                                            })
                except Exception:
                    pass

print("Discovered Views by Account:")
for email, vs in found_views_by_account.items():
    uniq = list({v["name"]: v for v in vs}.values())
    if uniq:
        print(f"[{email}]: {len(uniq)} views")
        for u in uniq:
            print(f"  • {u['name']}")
