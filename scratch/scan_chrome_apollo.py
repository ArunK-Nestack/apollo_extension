import os
import re

chrome_base = os.path.expanduser('~') + r'\AppData\Local\Google\Chrome\User Data'

for p in os.listdir(chrome_base):
    p_dir = os.path.join(chrome_base, p)
    if not os.path.isdir(p_dir):
        continue
    
    # Check IndexedDB
    idb = os.path.join(p_dir, 'IndexedDB')
    if os.path.exists(idb):
        for sub in os.listdir(idb):
            if 'apollo' in sub.lower():
                full_db = os.path.join(idb, sub)
                for f in os.listdir(full_db):
                    if f.endswith(('.ldb', '.log')):
                        fp = os.path.join(full_db, f)
                        try:
                            with open(fp, 'rb') as fl:
                                data = fl.read()
                                # Find any string after "name"
                                matches = re.findall(rb'"name"[^"]{0,10}"([^"]{3,60})"', data)
                                for m in matches:
                                    # filter out boring names
                                    if not any(b in m.lower() for b in [b'react', b'webpack', b'apollo', b'google', b'pageurl', b'email', b'password']):
                                        print(f"[{p} IDB {sub}/{f}] name: {m.decode('utf-8', errors='ignore')}")
                                
                                # Check for V8 pattern without strict modality
                                v8_matches = re.finditer(rb'"\x02id"\x18([a-f0-9]{24})"\x04name"([\x01-\x7f])([^\x00]{1,120})', data)
                                for vm in v8_matches:
                                    vid = vm.group(1).decode()
                                    nlen = ord(vm.group(2))
                                    vname = vm.group(3)[:nlen].decode('utf-8', errors='ignore')
                                    print(f"[{p} IDB V8] vid={vid} name='{vname}'")
                        except Exception:
                            pass

    # Check Local Storage
    ls = os.path.join(p_dir, 'Local Storage', 'leveldb')
    if os.path.exists(ls):
        for f in os.listdir(ls):
            if f.endswith(('.ldb', '.log')):
                fp = os.path.join(ls, f)
                try:
                    with open(fp, 'rb') as fl:
                        data = fl.read()
                        if b'apollo.io' in data:
                            # Look for finder_views or saved searches
                            v8_matches = re.finditer(rb'"\x02id"\x18([a-f0-9]{24})"\x04name"([\x01-\x7f])([^\x00]{1,120})', data)
                            for vm in v8_matches:
                                vid = vm.group(1).decode()
                                nlen = ord(vm.group(2))
                                vname = vm.group(3)[:nlen].decode('utf-8', errors='ignore')
                                print(f"[{p} LS V8] vid={vid} name='{vname}'")
                            # Also look for json with "display_name" or "name"
                            for jm in re.finditer(rb'"(?:display_)?name"\s*:\s*"([^"]{3,60})"', data):
                                dn = jm.group(1).decode('utf-8', errors='ignore')
                                if any(k in dn.lower() for k in ['service', 'regular', 'saas', 'director', 'manager', 'automotive', 'chro', 'estate', 'recruiting', 'construction', 'talent', 'marketing']):
                                    print(f"[{p} LS JSON] name='{dn}'")
                except Exception:
                    pass
