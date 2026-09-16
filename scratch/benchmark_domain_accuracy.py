import sys, os, random, re, socket, urllib.request, urllib.parse, json
sys.path.insert(0, os.path.abspath('.'))
from backend.api import get_connection

def clean_domain(domain_str: str) -> str:
    if not domain_str:
        return ""
    d = domain_str.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.split("/")[0].strip()

def resolve_clearbit(name):
    try:
        url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={urllib.parse.quote(name)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode('utf-8'))
            if data and data[0].get('domain'):
                return clean_domain(data[0]['domain'])
    except Exception:
        pass
    return None

def resolve_dns_candidates(name):
    clean = re.sub(r'(?i)\b(inc|llc|ltd|corp|corporation|group|technologies|technology|solutions|services|consulting|global|holdings|company|co)\b', '', name).strip()
    clean_alnum = re.sub(r'[^a-zA-Z0-9]', '', clean).lower()
    full_alnum = re.sub(r'[^a-zA-Z0-9]', '', name).lower()
    
    candidates = []
    for base in [clean_alnum, full_alnum]:
        if base and len(base) >= 3:
            for ext in ['.com', '.io', '.co', '.org', '.net']:
                d = f"{base}{ext}"
                if d not in candidates:
                    candidates.append(d)
                    
    for c in candidates:
        try:
            socket.gethostbyname(c)
            return clean_domain(c)
        except Exception:
            pass
    return None

with get_connection() as conn:
    with conn.cursor() as cur:
        # Fetch 100 companies saved from Apollo
        cur.execute("SELECT company_name, domain FROM detected_companies WHERE domain != '' AND domain IS NOT NULL ORDER BY id DESC LIMIT 100")
        sample = cur.fetchall()

print(f"Testing {len(sample)} real companies against Apollo's ground truth domain...")

exact_matches = 0
partial_matches = 0
mismatches = 0
failed = 0

details = []

for comp_name, apollo_domain in sample:
    apollo_dom = clean_domain(apollo_domain)
    
    # Try Clearbit
    pred_dom = resolve_clearbit(comp_name)
    source = "Clearbit"
    
    # Fallback to DNS
    if not pred_dom:
        pred_dom = resolve_dns_candidates(comp_name)
        source = "DNS Candidates"
        
    if not pred_dom:
        failed += 1
        details.append((comp_name, apollo_dom, "None", "Failed"))
    elif pred_dom == apollo_dom:
        exact_matches += 1
        details.append((comp_name, apollo_dom, pred_dom, f"Exact ({source})"))
    elif pred_dom in apollo_dom or apollo_dom in pred_dom:
        partial_matches += 1
        details.append((comp_name, apollo_dom, pred_dom, f"Partial ({source})"))
    else:
        mismatches += 1
        details.append((comp_name, apollo_dom, pred_dom, f"Mismatch ({source})"))

total = len(sample)
print("\n" + "="*80)
print("BENCHMARK RESULTS VS APOLLO GROUND TRUTH DOMAIN (100 Real Companies)")
print("="*80)
print(f"Total Sample Tested: {total}")
print(f"Exact Matches:       {exact_matches} ({exact_matches/total*100:.1f}%)")
print(f"Partial Matches:     {partial_matches} ({partial_matches/total*100:.1f}%)")
print(f"Mismatches (Wrong):  {mismatches} ({mismatches/total*100:.1f}%)")
print(f"Failed (No Domain):  {failed} ({failed/total*100:.1f}%)")
print(f"Total Usable Rate:   {(exact_matches + partial_matches)/total*100:.1f}%")
print("="*80)

print("\nSample 15 Comparisons (Company | Apollo Ground Truth | Predicted | Result):")
for item in details[:15]:
    print(f"  • {item[0][:25]:<25} | Apollo: {item[1]:<25} | Predicted: {item[2]:<25} | {item[3]}")
