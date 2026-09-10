import pandas as pd
import os
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

# Load Apollo exports (23) and (24)
f23 = r'C:\Users\test\Downloads\apollo-contacts-export (23).csv'
f24 = r'C:\Users\test\Downloads\apollo-contacts-export (24).csv'

df23 = pd.read_csv(f23, low_memory=False)
df24 = pd.read_csv(f24, low_memory=False)
df_apollo_all = pd.concat([df23, df24], ignore_index=True)
print(f"Total Apollo Exported rows from (23) and (24): {len(df_apollo_all)}")

# In Apollo export, check Website vs Email Domain
apollo_websites = df_apollo_all['Website'].apply(extract_root)
apollo_emails = df_apollo_all['Email'].fillna('').astype(str).str.lower()
apollo_email_domains = apollo_emails.apply(lambda e: e.split('@')[1] if '@' in e else '')

diff_mask = (apollo_websites != apollo_email_domains) & (apollo_email_domains != '') & (apollo_websites != '')
print(f"Rows where Apollo Scraped Website != Apollo Enriched Email Domain: {diff_mask.sum()} out of {len(df_apollo_all)}")

# Sample cases
print("\nSample 15 cases where Website URL is different from Enriched Email Domain:")
samples = df_apollo_all[diff_mask][['First Name', 'Last Name', 'Company Name', 'Website', 'Email']].head(15)
for _, r in samples.iterrows():
    w = extract_root(r['Website'])
    em_d = r['Email'].split('@')[1] if '@' in str(r['Email']) else ''
    print(f"  Company: {r['Company Name']:<30} | Website: {w:<25} | Email Domain: {em_d}")
