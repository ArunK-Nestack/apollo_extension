import sys
import os
import csv

sys.path.append(os.getcwd())
from backend.api import get_connection

batch = "vijay_nestacktech_com-sep-new"
overlapping_domains = [
    "bmwofcamarillo.com",
    "bonnerchevrolet.com",
    "capitalbpg.com",
    "courtesycadillac.net",
    "driveholon.com",
    "exchangeandmart.co.uk",
    "govetted.com",
    "mhautoranch.com",
    "pro-adas.com",
    "promasterelectronic.com",
    "renthal.com",
    "rvwheelator.com",
    "stykemainchevy.com",
    "tml.com",
    "tommypikecustoms.com",
    "universalgrinding.com",
    "winnerford.com"
]

def remove_overlapping():
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Delete the 17 records
            placeholders = ",".join(["%s"] * len(overlapping_domains))
            sql = f"DELETE FROM apollo_saved_leads WHERE batch = %s AND company_domain IN ({placeholders})"
            params = [batch] + overlapping_domains
            cur.execute(sql, params)
            deleted_count = cur.rowcount
            conn.commit()
            
            print(f"Deleted {deleted_count} overlapping leads from batch '{batch}'.")
            
            # Fetch remaining leads
            cur.execute("""
                SELECT id, batch, apollo_id, name, first_name, last_name, job_title, 
                       company, company_domain, website_link, location, linkedin_url, 
                       apollo_profile_url, segment, created_at
                FROM apollo_saved_leads 
                WHERE batch = %s
                ORDER BY id ASC;
            """, (batch,))
            rows = cur.fetchall()
            print(f"Remaining leads in batch '{batch}': {len(rows)}")
            
            # Export to CSV
            csv_path = os.path.join(os.getcwd(), f"{batch}_unique_leads.csv")
            headers = [
                "id", "batch", "apollo_id", "name", "first_name", "last_name", "job_title",
                "company", "company_domain", "website_link", "location", "linkedin_url",
                "apollo_profile_url", "segment", "created_at"
            ]
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for r in rows:
                    writer.writerow(r)
            
            print(f"Exported {len(rows)} clean unique leads to: {csv_path}")

if __name__ == "__main__":
    remove_overlapping()
