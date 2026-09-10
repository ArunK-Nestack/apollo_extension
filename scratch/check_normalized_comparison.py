import pandas as pd
import re
from urllib.parse import urlparse

def extract_root(url):
    if not url or pd.isna(url):
        return ""
    u = str(url).strip().lower()
    if u.startswith('http://') or u.startswith('https://'):
        try:
            u = urlparse(u).netloc
        except:
            pass
    u = re.sub(r'^www\d*\.', '', u)
    return u.split('/')[0].split(':')[0].strip()

batch_csv = 'exports/abel_abraham_nestacktechnologies_com-sep_4197_verified_unique_leads.csv'
df_scraped = pd.read_csv(batch_csv)
scraped_roots = set(df_scraped['Website'].apply(extract_root))

fs_csv = r'C:\Users\test\Downloads\fsales_report_192724_12000032465_12000090559__09_09_2026_17_34_13.csv'
df_fs = pd.read_csv(fs_csv)

fs_emails = [str(e).strip().lower() for e in df_fs['Contact : Emails'].dropna()]
fs_domains = [e.split('@')[1].strip().lower() for e in fs_emails if '@' in e]

print(f"Total Freshsales contacts: {len(df_fs)}")
print(f"Total Freshsales unique email domains: {len(set(fs_domains))}")
print(f"Total Scraped unique root domains in 4197 CSV: {len(scraped_roots)}")

matched_between_scraped_and_fs = set(fs_domains).intersection(scraped_roots)
print(f"How many Freshsales email domains match the 4,197 scraped website domains: {len(matched_between_scraped_and_fs)}")

# Check the remaining
not_in_scraped = set(fs_domains) - scraped_roots
print(f"How many Freshsales email domains were NOT in the 4,197 scraped website domains: {len(not_in_scraped)}")

# Let's inspect these not_in_scraped:
print("\nSample domains in Freshsales that differed from scraped websites:")
for d in list(not_in_scraped)[:20]:
    print(" ", d)
