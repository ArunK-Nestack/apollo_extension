import pymysql
import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    port=int(os.getenv('DB_PORT', 3306)),
    database='apollo_scrapers'
)
cur = conn.cursor()

# Load the Freshsales report
p = r'C:\Users\test\Downloads\fsales_report_192724_12000032465_12000090559__09_09_2026_17_34_13.csv'
df = pd.read_csv(p)

# Get the matched domains that were in emails table
emails = [str(e).strip().lower() for e in df['Contact : Emails'].dropna()]
email_to_row = {str(r['Contact : Emails']).strip().lower(): r for _, r in df.iterrows()}

cur.execute("SELECT email, domain FROM emails WHERE email IN %s;", (tuple(emails[:1000]),))
matched_emails = cur.fetchall()
print(f"Sample matched emails: {len(matched_emails)}")

# Now check apollo_saved_leads for these emails/contacts to see what website_link and company_domain was saved during scraping
cur.execute("SELECT name, company, company_domain, website_link, batch FROM apollo_saved_leads WHERE batch IN ('abel_abraham_nestacktechnologies_com-sep', 'abel-sep') LIMIT 10;")
print("\nSample apollo_saved_leads rows:")
for r in cur.fetchall():
    print(r)

# Let's match by Contact Last Name + First Name or Company to see what the scraper saw at scrape time vs what the email domain became!
print("\nComparing scraped company_domain vs enriched email domain:")
mismatches = 0
matches = 0
sample_mismatches = []

# Load the exported batch CSV that was used
batch_csv = 'exports/abel_abraham_nestacktechnologies_com-sep_4197_verified_unique_leads.csv'
df_scraped = pd.read_csv(batch_csv)
scraped_domains_set = set(df_scraped['Website'].dropna().str.lower())

for _, r in df.iterrows():
    em = str(r['Contact : Emails']).strip().lower()
    if '@' not in em:
        continue
    em_dom = em.split('@')[1].strip().lower()
    first = str(r['Contact : First name']).strip().lower()
    last = str(r['Contact : Last name']).strip().lower()
    
    # Check if em_dom is in scraped_domains_set
    if em_dom in scraped_domains_set:
        matches += 1
    else:
        mismatches += 1
        if len(sample_mismatches) < 15:
            sample_mismatches.append((first, last, em, em_dom))

print(f"Enriched email domain MATCHED scraped website domain: {matches}")
print(f"Enriched email domain MISMATCHED (was different from) scraped website domain: {mismatches}")
print("\nSample mismatches (Enriched email domain differs from scraped website domain):")
for sm in sample_mismatches:
    print(sm)
