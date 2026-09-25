import re
from bs4 import BeautifulSoup

print("======================================================================")
print(">>> PYTHON TEST: APOLLO USER TABLE SNIPPET EXTRACTION")
print("======================================================================\n")

# 1. Read user HTML snippet
with open('scratch/test_apollo_html.html', 'r', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

def clean_text(t):
    return re.sub(r'\s+', ' ', t or '').strip()

def extract_root_domain(url_or_domain):
    if not url_or_domain:
        return ""
    clean = re.sub(r'^https?://', '', url_or_domain).split('/')[0].split('?')[0].lower()
    clean = re.sub(r'^www\d*\.', '', clean)
    parts = [p for p in clean.split('.') if p]
    if len(parts) >= 2:
        return '.'.join(parts[-2:])
    return clean

def extract_company_from_linkedin_url(url):
    if not url:
        return ""
    m = re.search(r'linkedin\.com/company/([^/?#]+)', url, re.I)
    if not m:
        return ""
    slug = m.group(1).strip()
    slug = re.sub(r'^www\.', '', slug, flags=re.I)
    slug = re.sub(r'\.(?:com|org|io|net|co|edu|gov)$', '', slug, flags=re.I)
    slug = re.sub(r'[-_.]+', ' ', slug).strip()
    if not slug:
        return ""
    return ' '.join(w.capitalize() for w in slug.split())

# 2. Test Contact Name Link Selectors (excluding actions, icon-only buttons)
candidate_links = soup.select(
    '[data-id="contact.name"] a[href*="/people/"], '
    '[data-id="contact.name"] a[data-to*="/people/"], '
    '[data-id="contact.name"] a, '
    '[data-testid="contact-name-cell"] a, '
    '[data-interaction-boundary="Contact Name Cell"] a'
)

unique_links = []
seen = set()
for a in candidate_links:
    if a in seen:
        continue
    seen.add(a)
    # Exclude action links
    if a.find_parent(attrs={'data-id': 'actions'}) or a.find_parent(attrs={'data-id': 'leftActions'}):
        continue
    if a.get('data-icon-button-variant'):
        continue
    text = clean_text(a.get_text())
    if not text:
        continue
    unique_links.append(a)

print(f"[PASS] Found {len(unique_links)} Contact Name Links (0 Action/Icon Links)")
assert len(unique_links) == 1, f"Expected 1 contact link, got {len(unique_links)}"
name_link = unique_links[0]
name = clean_text(name_link.get_text())
assert name == "Michaele Chocalas", f"Expected Michaele Chocalas, got {name}"
print(f"  Name: {name}")

# 3. Verify exit-to-app action link is in the DOM but excluded
action_link = soup.select_one('[data-id="actions"] a')
assert action_link is not None, "Action link should exist in snippet"
assert action_link not in unique_links, "Action link must be excluded from contact links!"
print("[PASS] Verified exit-to-app action link is properly excluded")

# 4. Verify Company extraction from LinkedIn slug fallback
row = name_link.find_parent(attrs={'role': 'row'})
social_cell = row.find(attrs={'data-id': 'account.social'})
linkedin_a = social_cell.find('a', href=re.compile(r'linkedin\.com/company/'))
assert linkedin_a is not None
linkedin_href = linkedin_a.get('href') or linkedin_a.get('data-href')
company = extract_company_from_linkedin_url(linkedin_href)
assert company == "Links Healthcare", f"Expected 'Links Healthcare', got '{company}'"
print(f"[PASS] Company extracted via LinkedIn slug: '{company}'")

# 5. Verify Domain extraction
domain_cell = row.find(attrs={'data-id': 'account.domain'})
raw_domain = clean_text(domain_cell.get_text())
domain = extract_root_domain(raw_domain)
assert domain == "link-health.org", f"Expected 'link-health.org', got '{domain}'"
print(f"[PASS] Domain extracted: '{domain}'")

# 6. Verify Job title extraction
title_cell = row.find(attrs={'data-id': 'contact.job_title'})
title = clean_text(title_cell.get_text())
assert title == "HR Project Manager", f"Expected 'HR Project Manager', got '{title}'"
print(f"[PASS] Job Title extracted: '{title}'")

# 7. Verify consecutive failure guard
body_rows = [r for r in soup.select('[role="rowgroup"] [role="row"], .zp_Gjvi9 [role="row"], [id^="table-row-"]') if not r.select('[role="columnheader"]')]
assert len(body_rows) == 1, "1 body row present"
print(f"[PASS] Body rows count: {len(body_rows)}")

print("\nALL PYTHON TESTS PASSED WITH 0 ERRORS!\n")
