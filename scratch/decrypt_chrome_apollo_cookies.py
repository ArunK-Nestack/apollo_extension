import os
import sqlite3
import shutil
import tempfile
import json
import base64
import ctypes
import ctypes.wintypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def get_secret(local_state_path):
    with open(local_state_path, 'r', encoding='utf-8') as f:
        ls = json.load(f)
    enc_key = base64.b64decode(ls['os_crypt']['encrypted_key'])[5:]
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', ctypes.wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    blob_in = DATA_BLOB(len(enc_key), ctypes.cast(ctypes.create_string_buffer(enc_key), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        target = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return target
    return None

def decrypt_val(buff, key):
    try:
        iv = buff[3:15]
        payload = buff[15:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(iv, payload, None).decode('utf-8', errors='ignore')
    except Exception as e:
        return f"<decryption error: {e}>"

chrome_base = os.path.expanduser('~') + r'\AppData\Local\Google\Chrome\User Data'
key = get_secret(os.path.join(chrome_base, 'Local State'))

for p in os.listdir(chrome_base):
    p_dir = os.path.join(chrome_base, p)
    cookie_db = os.path.join(p_dir, 'Network', 'Cookies')
    if not os.path.exists(cookie_db):
        cookie_db = os.path.join(p_dir, 'Cookies')
    if os.path.exists(cookie_db):
        tmp = tempfile.mktemp()
        try:
            shutil.copy2(cookie_db, tmp)
            conn = sqlite3.connect(tmp)
            c = conn.cursor()
            rows = c.execute("SELECT host_key, name, encrypted_value FROM cookies WHERE host_key LIKE '%apollo.io%'").fetchall()
            if rows:
                print(f"\n=== {p} ({len(rows)} Apollo cookies) ===")
                for host, name, enc_val in rows:
                    if any(k in name.lower() for k in ['remember', 'token', 'session', 'user', 'email', 'auth', 'logged', 'id']):
                        val = decrypt_val(enc_val, key)
                        print(f"  {name} = {val[:80]}")
            conn.close()
        except Exception as e:
            pass
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
