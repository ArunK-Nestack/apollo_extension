import pymysql
import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    port=int(os.getenv('DB_PORT', 3306))
)
cur = conn.cursor()

# Load the 3,154 contacts from Freshsales report
p = r'C:\Users\test\Downloads\fsales_report_192724_12000032465_12000090559__09_09_2026_17_34_13.csv'
df = pd.read_csv(p)
print(f"Loaded {len(df)} contacts from Freshsales report.")

emails = [str(e).strip().lower() for e in df['Contact : Emails'].dropna()]
domains = [e.split('@')[1].strip().lower() for e in emails if '@' in e]

print(f"Total valid emails: {len(emails)}")
print(f"Total unique emails: {len(set(emails))}")
print(f"Total unique email domains: {len(set(domains))}")

# Check 1: Pre-existing contacts in Freshsales before today
df['created_dt'] = pd.to_datetime(df['Contact : Created at'], format='mixed')
today = pd.to_datetime('2026-09-09').date()
pre_existing_contacts = df[df['created_dt'].dt.date < today]
print(f"\n[Factor 1] Pre-existing in Freshsales before today: {len(pre_existing_contacts)} contacts")
print(f"           Unique domains of pre-existing: {pre_existing_contacts['Contact : Accounts'].nunique()}")

# Check 2: How many emails exist in apollo_scrapers.emails
cur.execute("USE apollo_scrapers;")
# Query in chunks of 1000
found_in_apollo_scrapers = set()
found_domains_apollo_scrapers = set()

unique_emails_list = list(set(emails))
for i in range(0, len(unique_emails_list), 1000):
    chunk = unique_emails_list[i:i+1000]
    cur.execute(f"SELECT email, domain FROM emails WHERE email IN %s;", (tuple(chunk),))
    for r in cur.fetchall():
        found_in_apollo_scrapers.add(r[0].lower())
        if r[1]:
            found_domains_apollo_scrapers.add(r[1].lower())

print(f"\n[Factor 2] Emails found in apollo_scrapers.emails (7.47M): {len(found_in_apollo_scrapers)}")
print(f"           Domains matched in apollo_scrapers.emails: {len(found_domains_apollo_scrapers)}")

# Check 3: Check candidate domains in apollo_scrapers.emails
unique_domains_list = list(set(domains))
domains_in_apollo_scrapers_emails = set()
for i in range(0, len(unique_domains_list), 1000):
    chunk = unique_domains_list[i:i+1000]
    cur.execute(f"SELECT distinct domain FROM emails WHERE domain IN %s;", (tuple(chunk),))
    for r in cur.fetchall():
        if r[0]:
            domains_in_apollo_scrapers_emails.add(r[0].lower())

print(f"\n[Factor 3] Unique Domains found in apollo_scrapers.emails (7.47M): {len(domains_in_apollo_scrapers_emails)}")

# Check 4: Check nestack_agents.emails (7.78M)
cur.execute("USE nestack_agents;")
found_in_nestack_agents = set()
for i in range(0, len(unique_emails_list), 1000):
    chunk = unique_emails_list[i:i+1000]
    cur.execute(f"SELECT email FROM emails WHERE email IN %s;", (tuple(chunk),))
    for r in cur.fetchall():
        found_in_nestack_agents.add(r[0].lower())

print(f"\n[Factor 4] Emails found in nestack_agents.emails (7.78M): {len(found_in_nestack_agents)}")

# Check 5: Check nestack.emails (6.33M)
cur.execute("USE nestack;")
found_in_nestack = set()
for i in range(0, len(unique_emails_list), 1000):
    chunk = unique_emails_list[i:i+1000]
    cur.execute(f"SELECT email FROM emails WHERE email IN %s;", (tuple(chunk),))
    for r in cur.fetchall():
        found_in_nestack.add(r[0].lower())

print(f"\n[Factor 5] Emails found in nestack.emails (6.33M): {len(found_in_nestack)}")

# Union of all database matches
all_db_matched_emails = found_in_apollo_scrapers.union(found_in_nestack_agents).union(found_in_nestack)
print(f"\n>>> Total Emails that already existed across your RDS databases: {len(all_db_matched_emails)}")
