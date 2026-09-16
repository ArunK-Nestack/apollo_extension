import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI
from backend.api import get_connection

def clean_dom(d):
    if not d: return ""
    d = d.strip().lower()
    for p in ("https://", "http://", "www."):
        if d.startswith(p): d = d[len(p):]
    return d.split("/")[0].strip()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT company_name, domain FROM detected_companies WHERE domain != '' AND domain IS NOT NULL ORDER BY id DESC LIMIT 50")
        sample = cur.fetchall()

prompt = "For each of the following company names, return the primary official corporate website domain (e.g. 'stripe.com', 'microsoft.com'). Return ONLY valid JSON as a dictionary mapping company_name to domain string:\n"
names = [s[0] for s in sample]
prompt += json.dumps(names)

print(f"Sending {len(names)} company names to gpt-4o-mini...")
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": "You are a specialized B2B database assistant that outputs clean JSON mapping company names to their exact official internet domain names."},
        {"role": "user", "content": prompt}
    ],
    response_format={"type": "json_object"},
    temperature=0.0
)

# calculate cost
usage = resp.usage
# gpt-4o-mini: $0.15 / 1M prompt tokens, $0.60 / 1M completion tokens
cost = (usage.prompt_tokens * 0.00000015) + (usage.completion_tokens * 0.00000060)

result = json.loads(resp.choices[0].message.content)
# If root key exists
if "companies" in result:
    result = result["companies"]
elif "domains" in result:
    result = result["domains"]

exact = 0
partial = 0
mismatch = 0
not_found = 0

print("\n--- RESULTS vs APOLLO GROUND TRUTH ---")
for comp, apollo_dom in sample:
    clean_apollo = clean_dom(apollo_dom)
    pred = clean_dom(result.get(comp) or "")
    if not pred:
        not_found += 1
        tag = "NONE"
    elif pred == clean_apollo:
        exact += 1
        tag = "EXACT MATCH"
    elif pred in clean_apollo or clean_apollo in pred:
        partial += 1
        tag = "PARTIAL MATCH"
    else:
        mismatch += 1
        tag = "MISMATCH"
    print(f"  • {comp[:25]:<25} | Apollo: {clean_apollo:<25} | AI: {pred:<25} | {tag}")

total = len(sample)
print("="*80)
print(f"Total Companies Tested: {total}")
print(f"Exact Matches:         {exact} ({exact/total*100:.1f}%)")
print(f"Partial Matches:       {partial} ({partial/total*100:.1f}%)")
print(f"Total Accurate:        {exact + partial} ({(exact+partial)/total*100:.1f}%)")
print(f"Total AI Token Cost:   ${cost:.6f} (Less than one-tenth of a cent!)")
print(f"Cost per 1,000 leads:  ${(cost / total) * 1000:.4f}")
print("="*80)
