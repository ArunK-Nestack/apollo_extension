import urllib.request, urllib.parse, json, re

def search_ddg(query):
    try:
        url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=5) as r:
            html = r.read().decode('utf-8', errors='ignore')
            # Extract links from DDG HTML
            matches = re.findall(r'<a class="result__url"[^>]*href="([^"]+)"', html)
            clean_links = []
            for m in matches:
                # DDG uses redirect URLs: /l/?kh=-1&uddg=https%3A%2F%2Fwww.following-sea.com%2F
                if 'uddg=' in m:
                    actual = urllib.parse.unquote(m.split('uddg=')[1].split('&')[0])
                else:
                    actual = m
                dom = actual.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0].strip().lower()
                if dom and 'duckduckgo' not in dom and dom not in clean_links:
                    clean_links.append(dom)
            return clean_links[:3]
    except Exception as e:
        return [f'Error: {e}']

comps = [
    ('following sea', 'following-sea.com'),
    ('allied digital printing', 'allieddigitalprinting.com'),
    ('shuttersound pictures', 'shuttersoundpics.com'),
    ('site mechanix', 'sitemechanix.com'),
    ('caner aras studio', 'caneraras.com'),
    ('dynamic vision', 'dynamicvision.ca')
]

for name, truth in comps:
    res = search_ddg(f'"{name}" official website')
    matched = "EXACT MATCH!" if truth in res else ("Partial" if any(truth in r or r in truth for r in res) else "Mismatch")
    print(f"Company: {name:<25} | Apollo: {truth:<25} | Top DDG: {str(res[:2]):<35} | {matched}")
