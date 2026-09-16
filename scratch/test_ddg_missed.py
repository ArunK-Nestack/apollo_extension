import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import urllib.request
import urllib.parse
import re
from scripts.domain_resolver_engine import clean_domain, AGGREGATOR_DOMAINS

comps = [
    'DC Design, LLC - Graphic Design',
    'Mash Creative Co.',
    'Panache PR & Marketing',
    'Jett Environmental Consulting',
    'HomePlace Furniture & Design',
    'InVogue Marketing',
    'Simms Design LLC',
    'Twenty-Six & Co. | A Creative Agency',
    'CRDN of Minnesota'
]

for c in comps:
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", c).strip()
    clean = re.sub(r"\s+", " ", clean)
    q = urllib.parse.quote(clean + " website")
    url = "https://html.duckduckgo.com/html/?q=" + q
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        html = urllib.request.urlopen(req, timeout=3).read().decode("utf-8", errors="ignore")
        matches = re.findall(r'<a class="result__url"[^>]*href="([^"]+)"', html)
        doms = []
        for m in matches[:6]:
            if "uddg=" in m:
                m = urllib.parse.unquote(m.split("uddg=")[1].split("&")[0])
            d = clean_domain(m)
            if d and d not in AGGREGATOR_DOMAINS and not any(d.endswith("." + a) or d == a for a in AGGREGATOR_DOMAINS):
                doms.append(d)
        print(f"'{c}' -> Found: {doms[:3]}")
    except Exception as ex:
        print(f"'{c}' -> Error: {ex}")
