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

# Get domains from abel_abraham_nestacktechnologies_com-sep
cur.execute('SELECT company_domain, company, name, website_link, email FROM apollo_saved_leads WHERE batch = %s;', ('abel_abraham_nestacktechnologies_com-sep',))
rows_abel_large = cur.fetchall()
df_large = pd.DataFrame(rows_abel_large, columns=['domain', 'company', 'name', 'link', 'email'])

# Get domains from abel-sep
cur.execute('SELECT company_domain, company, name, website_link, email FROM apollo_saved_leads WHERE batch = %s;', ('abel-sep',))
rows_abel_small = cur.fetchall()
df_small = pd.DataFrame(rows_abel_small, columns=['domain', 'company', 'name', 'link', 'email'])

print(f"Large batch: {len(df_large)} rows, {df_large['domain'].nunique()} unique domains")
print(f"Small batch: {len(df_small)} rows, {df_small['domain'].nunique()} unique domains")

# Overlap between large and small batches
overlap = set(df_large['domain'].str.lower()).intersection(set(df_small['domain'].str.lower()))
print(f"Overlap between the two batches: {len(overlap)} domains")

# Load freshsales report
fs_df = pd.read_csv('exports/fsales_report_192724_12000032465_12000090541__09_09_2026_07_17_36.csv')
fs_emails = fs_df['Contact : Emails'].dropna().astype(str).tolist()
fs_domains = set([e.split('@')[1].lower() for e in fs_emails if '@' in e])
print(f"Freshsales report: {len(fs_df)} rows, {len(fs_domains)} unique domains")

# Overlap with Freshsales report
large_in_fs = set(df_large['domain'].str.lower()).intersection(fs_domains)
print(f"Large batch domains in Freshsales report: {len(large_in_fs)}")

small_in_fs = set(df_small['domain'].str.lower()).intersection(fs_domains)
print(f"Small batch domains in Freshsales report: {len(small_in_fs)}")

# Check against apollo_scrapers.emails (7.47M table)
cur.execute("SELECT domain FROM emails WHERE domain IN %s LIMIT 1000;", (tuple(df_large['domain'].dropna().unique()[:500]),))
found_in_crm_table = cur.fetchall()
print(f"Sample of 500 large batch domains in emails table: {len(found_in_crm_table)} found")

# Look at multi-branch / company patterns in df_large
print("\nTop 15 most frequent domains in Large Batch:")
print(df_large['domain'].value_counts().head(15))

# Look at companies with multiple domains or duplicate company names
print("\nTop 15 most frequent company names in Large Batch:")
print(df_large['company'].value_counts().head(15))
