import socket, urllib.request, json, urllib.parse

companies_to_verify = [
    ("Marissa", "Founder & CEO", "CLICK Real Estate", "clickrealestatephotography.com"),
    ("Geoffrey", "CEO", "CPi REPS", "cpi-reps.com"),
    ("Jean", "President / CEO", "Mass Mania Productions", "massmania.net"),
    ("Marko", "CEO", "Brandomania Custom Print", "brandomaniacustomprint.com"),
    ("Dana", "Founder & CEO", "Private Art Consulting", "privateartconsulting.com"),
    ("Greg", "President / CEO", "Owens Design Group", "owensdesign.com"),
    ("Joanne", "Co-Founder & CEO", "Photo Art Pavilion", "photoartpavilion.com"),
    ("Stella", "Founder and CEO", "Tiny Legends Productions", "tinylegendsproductions.com"),
    ("Omar", "Founder & CEO", "Ora Photography", "oraphotography.com"),
    ("Richard", "Founder/ CEO", "Keeplan Experiential", "keeplanexperiential.com")
]

print("=== VERIFYING EACH DOMAIN LIVE ===")
for person, title, comp, dom in companies_to_verify:
    # 1. DNS check
    ip = "FAILED"
    try:
        ip = socket.gethostbyname(dom)
    except Exception as e:
        ip = f"DNS Error: {e}"
        
    # 2. HTTP check
    http_status = "N/A"
    try:
        req = urllib.request.Request(f"https://{dom}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            http_status = f"HTTP {r.status} (Live Site)"
    except Exception as e:
        # try http
        try:
            req = urllib.request.Request(f"http://{dom}", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as r:
                http_status = f"HTTP {r.status} (Live Site via HTTP)"
        except Exception as e2:
            http_status = f"Connection failed: {str(e2)[:30]}"

    print(f"\nPerson:  {person} ({title})")
    print(f"Company: {comp}")
    print(f"Domain:  {dom}")
    print(f"IP:      {ip}")
    print(f"Website: {http_status}")
