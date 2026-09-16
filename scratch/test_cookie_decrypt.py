import os, sqlite3, shutil, tempfile, json, base64, ctypes, ctypes.wintypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

chrome_base = os.path.expanduser('~') + r'\AppData\Local\Google\Chrome\User Data'
with open(os.path.join(chrome_base, 'Local State'), 'r', encoding='utf-8') as f:
    ls = json.load(f)
enc_key = base64.b64decode(ls['os_crypt']['encrypted_key'])[5:]
class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', ctypes.wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
blob_in = DATA_BLOB(len(enc_key), ctypes.cast(ctypes.create_string_buffer(enc_key), ctypes.POINTER(ctypes.c_char)))
blob_out = DATA_BLOB()
ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
key = ctypes.string_at(blob_out.pbData, blob_out.cbData)

cookie_db = os.path.join(chrome_base, 'Profile 14', 'Network', 'Cookies')
tmp = tempfile.mktemp()
shutil.copy2(cookie_db, tmp)
conn = sqlite3.connect(tmp)
c = conn.cursor()
row = c.execute("SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%apollo.io%' AND name='remember_token_leadgenie_v2'").fetchone()
conn.close()
os.remove(tmp)

print('name:', row[0], 'len:', len(row[1]), 'prefix:', row[1][:3])
buff = row[1]
try:
    aesgcm = AESGCM(key)
    res = aesgcm.decrypt(buff[3:15], buff[15:], None)
    print('Decrypted:', res.decode('utf-8'))
except Exception as e:
    import traceback
    traceback.print_exc()
