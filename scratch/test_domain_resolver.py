import urllib.request, urllib.parse, re, socket

def test_company(query):
    print(f"\n--- Testing: {query} ---")
    
    # 1. Clearbit
    try:
        url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as r:
            import json
            data = json.loads(r.read().decode('utf-8'))
            if data:
                print(f"  [Clearbit] Found: {data[0].get('domain')}")
            else:
                print("  [Clearbit] Not found")
    except Exception as e:
        print(f"  [Clearbit] Error: {e}")

    # 2. DNS check on candidate domains
    # Candidate clean
    clean = re.sub(r'(?i)\b(inc|llc|ltd|corp|corporation|group|technologies|solutions|health)\b', '', query).strip()
    clean = re.sub(r'[^a-zA-Z0-9]', '', clean).lower()
    full_clean = re.sub(r'[^a-zA-Z0-9]', '', query).lower()
    
    candidates = [f"{full_clean}.com", f"{clean}.com", f"{full_clean}.io", f"{clean}.io"]
    for c in candidates:
        try:
            socket.gethostbyname(c)
            print(f"  [DNS Check] Live active domain: {c}")
            break
        except socket.gaierror:
            pass

test_company("Intempo Health")
test_company("Nestack Technologies")
test_company("Stripe")
